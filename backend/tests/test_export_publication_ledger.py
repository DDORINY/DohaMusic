"""Durable Export publication ledger and restart-recovery contracts."""

from __future__ import annotations

import hashlib
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.workspace import (
    AssetType,
    ExportPublicationState,
    Job,
    JobExportPublication,
    JobStatus,
)
from backend.services.workspace import (
    AssetService,
    CompositionService,
    ExportPublicationError,
    ExportPublicationErrorCode,
    ExportPublicationService,
    SnapshotItemInput,
    WorkspaceService,
)
from backend.storage.artifact_publisher import (
    LocalArtifactPublisher,
    PublicationOutcome,
    TrustedPublicationIdentity,
)
from backend.storage.artifact_resolver import ArtifactStorageRoots


class LedgerFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.engine = create_database_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.owner = uuid4()
        workspace_service = WorkspaceService(self.factory)
        workspace = workspace_service.create_workspace(owner_id=self.owner, name="Export")
        self.project = workspace_service.create_project(
            workspace_id=workspace.workspace_id,
            title="Export Project",
            created_by=self.owner,
        )
        asset_service = AssetService(self.factory)
        asset = asset_service.create_asset(
            owner_id=self.owner,
            workspace_id=workspace.workspace_id,
            asset_type=AssetType.MUSIC,
        )
        self.version = asset_service.create_asset_version(
            asset_id=asset.asset_id,
            version_origin="user_created",
            settings_snapshot={},
            created_by=self.owner,
        )
        workspace_service.attach_asset(
            project_id=self.project.project_id,
            asset_id=asset.asset_id,
            display_order=0,
            role="music",
        )
        self.snapshot = (
            CompositionService(self.factory)
            .create_snapshot(
                project_id=self.project.project_id,
                effective_owner_id=self.owner,
                items=[SnapshotItemInput(self.version.asset_version_id, "music", 0)],
                mix_settings_snapshot={},
                provider_versions={},
                model_manifest_ids={},
                idempotency_key="export-ledger-snapshot",
            )
            .aggregate.snapshot
        )
        self.claim_token = uuid4()
        self.claimed_by = "export-worker"
        with self.factory() as session, session.begin():
            self.job = Job(
                project_id=self.project.project_id,
                workspace_id=workspace.workspace_id,
                composition_snapshot_id=self.snapshot.composition_snapshot_id,
                job_type="export",
                status=JobStatus.RUNNING,
                api_contract_version="1",
                settings_snapshot={},
                requested_by=self.owner,
                claim_token=self.claim_token,
                claimed_by=self.claimed_by,
            )
            session.add(self.job)
            session.flush()
            session.expunge(self.job)
        roots_path = tmp_path / "artifacts"
        for domain in ("lm", "audio", "vocal", "music"):
            (roots_path / domain).mkdir(parents=True)
        self.staging = tmp_path / "staging"
        self.staging.mkdir()
        self.roots = ArtifactStorageRoots.from_base_root(roots_path)
        self.publisher = LocalArtifactPublisher(self.roots, self.staging)

    def service(self) -> ExportPublicationService:
        return ExportPublicationService(self.factory, publisher=self.publisher)

    def intent(self):
        return self.service().ensure_intent(
            job_id=self.job.job_id,
            composition_snapshot_id=self.snapshot.composition_snapshot_id,
        )

    def wav(self, name: str = "output.wav", sample: int = 1000) -> Path:
        path = self.staging / name
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(sample.to_bytes(2, "little", signed=True) * 32)
        return path

    def integrity(self, path: Path) -> tuple[str, int]:
        payload = path.read_bytes()
        return hashlib.sha256(payload).hexdigest(), len(payload)

    def set_integrity(self, path: Path):
        checksum, size = self.integrity(path)
        return self.service().set_expected_integrity(
            job_id=self.job.job_id,
            claimed_by=self.claimed_by,
            claim_token=self.claim_token,
            sha256=checksum,
            size_bytes=size,
        )


@pytest.fixture
def ledger(tmp_path):
    fixture = LedgerFixture(tmp_path)
    yield fixture
    fixture.engine.dispose()


def test_intent_is_snapshot_bound_deterministic_and_idempotent(ledger):
    first = ledger.intent()
    second = ledger.intent()
    identity = TrustedPublicationIdentity.for_wav_export(ledger.job.job_id)
    assert first.job_id == second.job_id == ledger.job.job_id
    assert first.composition_snapshot_id == ledger.snapshot.composition_snapshot_id
    assert (first.storage_domain, first.storage_key) == (
        identity.storage_domain,
        identity.storage_key,
    )
    assert first.state is ExportPublicationState.INTENDED
    with ledger.factory() as session:
        assert session.scalar(select(func.count()).select_from(JobExportPublication)) == 1


def test_intent_rejects_incompatible_snapshot_and_format(ledger):
    ledger.intent()
    with pytest.raises(ExportPublicationError) as snapshot_error:
        ledger.service().ensure_intent(
            job_id=ledger.job.job_id,
            composition_snapshot_id=uuid4(),
        )
    assert snapshot_error.value.code is ExportPublicationErrorCode.INVALID_JOB
    with pytest.raises(ExportPublicationError) as format_error:
        ledger.service().ensure_intent(
            job_id=ledger.job.job_id,
            composition_snapshot_id=ledger.snapshot.composition_snapshot_id,
            export_format="mp3",
        )
    assert format_error.value.code is ExportPublicationErrorCode.CONFLICT


def test_expected_integrity_is_idempotent_and_immutable(ledger):
    ledger.intent()
    payload = ledger.wav()
    first = ledger.set_integrity(payload)
    replay = ledger.set_integrity(payload)
    assert replay.expected_sha256 == first.expected_sha256
    with pytest.raises(ExportPublicationError) as mismatch:
        ledger.service().set_expected_integrity(
            job_id=ledger.job.job_id,
            claimed_by=ledger.claimed_by,
            claim_token=ledger.claim_token,
            sha256="0" * 64,
            size_bytes=first.expected_size_bytes,
        )
    assert mismatch.value.code is ExportPublicationErrorCode.CONFLICT


def test_crash_before_publish_preserves_resumable_intent(ledger):
    ledger.intent()
    payload = ledger.wav()
    ledger.set_integrity(payload)
    recovered = ledger.service().publish_or_recover(
        job_id=ledger.job.job_id,
        claimed_by=ledger.claimed_by,
        claim_token=ledger.claim_token,
    )
    assert recovered.publication.state is ExportPublicationState.INTENDED
    assert recovered.storage_outcome is None


def test_crash_after_publish_is_adopted_by_fresh_process_state(ledger):
    publication = ledger.intent()
    payload = ledger.wav()
    publication = ledger.set_integrity(payload)
    identity = TrustedPublicationIdentity.for_wav_export(ledger.job.job_id)
    published = ledger.publisher.publish_or_adopt(
        payload,
        identity=identity,
        artifact_kind="audio",
        expected_media_type="audio/wav",
        expected_sha256=publication.expected_sha256,
        expected_size_bytes=publication.expected_size_bytes,
    )
    assert published.outcome is PublicationOutcome.PUBLISHED_NEW

    fresh_publisher = LocalArtifactPublisher(ledger.roots, ledger.staging)
    fresh_service = ExportPublicationService(ledger.factory, publisher=fresh_publisher)
    recovered = fresh_service.publish_or_recover(
        job_id=ledger.job.job_id,
        claimed_by=ledger.claimed_by,
        claim_token=ledger.claim_token,
    )
    assert recovered.storage_outcome is PublicationOutcome.ADOPTED_EXISTING
    assert recovered.publication.state is ExportPublicationState.PUBLISHED


def test_mark_published_response_loss_replays_without_republish(ledger):
    ledger.intent()
    payload = ledger.wav()
    ledger.set_integrity(payload)
    first = ledger.service().publish_or_recover(
        job_id=ledger.job.job_id,
        claimed_by=ledger.claimed_by,
        claim_token=ledger.claim_token,
        staged_payload=payload,
    )
    replay = ledger.service().publish_or_recover(
        job_id=ledger.job.job_id,
        claimed_by=ledger.claimed_by,
        claim_token=ledger.claim_token,
    )
    assert first.publication.state is ExportPublicationState.PUBLISHED
    assert replay.publication.state is ExportPublicationState.PUBLISHED
    assert replay.storage_outcome is PublicationOutcome.ADOPTED_EXISTING


def test_stale_claim_and_cancellation_reject_logical_transition(ledger):
    ledger.intent()
    payload = ledger.wav()
    checksum, size = ledger.integrity(payload)
    with pytest.raises(ExportPublicationError) as stale:
        ledger.service().set_expected_integrity(
            job_id=ledger.job.job_id,
            claimed_by=ledger.claimed_by,
            claim_token=uuid4(),
            sha256=checksum,
            size_bytes=size,
        )
    assert stale.value.code is ExportPublicationErrorCode.STALE_CLAIM
    with ledger.factory() as session, session.begin():
        session.get(Job, ledger.job.job_id).cancel_requested_at = datetime.now(UTC)
    with pytest.raises(ExportPublicationError) as cancelled:
        ledger.service().set_expected_integrity(
            job_id=ledger.job.job_id,
            claimed_by=ledger.claimed_by,
            claim_token=ledger.claim_token,
            sha256=checksum,
            size_bytes=size,
        )
    assert cancelled.value.code is ExportPublicationErrorCode.CANCELLED


def test_concurrent_intent_converges_on_one_row(ledger):
    def ensure() -> UUID:
        return ledger.intent().job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: ensure(), range(2))) == [
            ledger.job.job_id,
            ledger.job.job_id,
        ]
    with ledger.factory() as session:
        assert session.scalar(select(func.count()).select_from(JobExportPublication)) == 1


def test_tampered_payload_fails_closed_and_marks_reconciliation(ledger):
    ledger.intent()
    payload = ledger.wav()
    publication = ledger.set_integrity(payload)
    identity = TrustedPublicationIdentity.for_wav_export(ledger.job.job_id)
    result = ledger.publisher.publish_or_adopt(
        payload,
        identity=identity,
        artifact_kind="audio",
        expected_media_type="audio/wav",
        expected_sha256=publication.expected_sha256,
        expected_size_bytes=publication.expected_size_bytes,
    )
    result.path.write_bytes(b"tampered")
    with pytest.raises(ExportPublicationError) as mismatch:
        ledger.service().publish_or_recover(
            job_id=ledger.job.job_id,
            claimed_by=ledger.claimed_by,
            claim_token=ledger.claim_token,
        )
    assert mismatch.value.code is ExportPublicationErrorCode.STORAGE_MISMATCH
    with ledger.factory() as session:
        persisted = session.get(JobExportPublication, ledger.job.job_id)
        assert persisted.state is ExportPublicationState.RECONCILIATION_REQUIRED
