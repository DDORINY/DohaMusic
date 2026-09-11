"""Shared real DB/UoW fixture for multi-format Export integration proofs."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from backend.audio.export_delivery_encoder import CanonicalExportDeliveryEncoder
from backend.audio.export_delivery_validator import ExportDeliveryValidator
from backend.audio.working_preview_renderer import FfmpegWorkingCompositionPreviewRenderer
from backend.models.workspace import (
    Artifact,
    ArtifactStorageLocation,
    Asset,
    AssetType,
    AssetVersion,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    ProjectAsset,
)
from backend.repositories.workspace import JobRepository
from backend.services.workspace.artifact_application_service import ArtifactApplicationService
from backend.services.workspace.artifact_ingestion_service import (
    ArtifactIngestionRequest,
    ArtifactIngestionService,
)
from backend.services.workspace.export_worker_service import ExportWorkerService
from backend.services.workspace.job_service import JobService
from backend.tests.test_export_job_completion_uow import _service as completion_service
from backend.tests.test_export_publication_ledger import LedgerFixture
from backend.tests.test_export_worker_happy_path import _source_wav


class MultiFormatExportIntegrationFixture:
    def __init__(self, tmp_path: Path, *, amplitude: float = 0.12, muted: bool = False) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is unavailable")
        self.ffmpeg = ffmpeg
        self.base = LedgerFixture(tmp_path)
        source = self.base.staging / "shared-source.wav"
        source.write_bytes(_source_wav(frequency=997, amplitude=amplitude))
        ingestion = ArtifactIngestionService(
            self.base.factory,
            artifact_roots=self.base.roots,
            staging_root=self.base.staging,
        )
        ingestion.ingest(
            ArtifactIngestionRequest(
                asset_version_id=self.base.version.asset_version_id,
                artifact_kind="audio",
                producer_type="provider",
                storage_domain="music",
                temporary_path=source,
                expected_media_type="audio/wav",
                expected_sha256=None,
                original_filename="shared-source.wav",
            )
        )
        track_id = uuid4()
        with self.base.factory() as session, session.begin():
            session.add(
                CompositionSnapshotTrack(
                    snapshot_track_id=track_id,
                    composition_snapshot_id=self.base.snapshot.composition_snapshot_id,
                    canonical_track_id=uuid4(),
                    track_type="audio",
                    name="Shared Export Track",
                    track_order=0,
                    gain_db=Decimal("0"),
                    pan=Decimal("0"),
                    muted=muted,
                    solo=False,
                )
            )
            session.flush()
            session.add(
                CompositionSnapshotClip(
                    composition_snapshot_id=self.base.snapshot.composition_snapshot_id,
                    snapshot_track_id=track_id,
                    canonical_clip_id=uuid4(),
                    source_asset_version_id=self.base.version.asset_version_id,
                    timeline_start=0,
                    source_in=0,
                    source_out=1_000_000,
                    source_duration=1_000_000,
                    timeline_duration=1_000_000,
                    loop_enabled=False,
                    loop_phase=0,
                    gain_db=Decimal("0"),
                    fade_in=0,
                    fade_out=0,
                )
            )
        self.artifacts = ArtifactApplicationService(
            self.base.factory, artifact_roots=self.base.roots
        )
        self.renderer = FfmpegWorkingCompositionPreviewRenderer(
            ffmpeg_executable=ffmpeg,
            temp_root=self.base.staging / "shared-render",
            open_artifact=lambda _artifact_id: None,
        )
        self.publications = self.base.service()
        self.completion = completion_service(self.base)
        self.encoder = CanonicalExportDeliveryEncoder(
            ffmpeg_executable=ffmpeg,
            temp_root=self.base.staging / "shared-encode",
        )
        self.validator = ExportDeliveryValidator(ffmpeg_executable=ffmpeg)
        self.worker = ExportWorkerService(
            self.base.factory,
            artifacts=self.artifacts,
            renderer=self.renderer,
            publications=self.publications,
            completion=self.completion,
            encoder=self.encoder,
            delivery_validator=self.validator,
        )

    def create_job(self, export_format: str, key: str):
        return JobService(self.base.factory).create_job_for_owner(
            effective_owner_id=self.base.owner,
            project_id=self.base.project.project_id,
            job_type="export",
            api_contract_version="1",
            settings_snapshot={"format": export_format},
            idempotency_key=key,
            composition_snapshot_id=self.base.snapshot.composition_snapshot_id,
        )

    def execute(self, export_format: str, key: str):
        creation = self.create_job(export_format, key)
        claim = self.claim_next(key)
        assert claim[0] == creation.aggregate.job.job_id
        return creation, self.execute_claim(claim)

    def claim_next(self, label: str):
        token = uuid4()
        claimed_by = f"fixture-{label}"
        with self.base.factory() as session, session.begin():
            claimed = JobRepository(session).claim_next_job(
                claimed_by=claimed_by,
                claim_token=token,
                now=datetime.now(UTC),
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                job_type="export",
            )
            assert claimed is not None
            job_id = claimed.job_id
        return job_id, claimed_by, token

    def execute_claim(self, claim, *, worker=None):
        job_id, claimed_by, token = claim
        return (worker or self.worker).execute_owned_claim(
            job_id=job_id,
            claimed_by=claimed_by,
            claim_token=token,
        )

    def build_worker(
        self, *, encoder=None, validator=None, publications=None, completion=None
    ) -> ExportWorkerService:
        return ExportWorkerService(
            self.base.factory,
            artifacts=self.artifacts,
            renderer=self.renderer,
            publications=publications or self.publications,
            completion=completion or self.completion,
            encoder=encoder or self.encoder,
            delivery_validator=validator or self.validator,
        )

    def cancel(self, job_id) -> None:
        with self.base.factory() as session, session.begin():
            job = session.get(Job, job_id)
            assert job is not None
            job.cancel_requested_at = datetime.now(UTC)

    def invalidate_claim(self, job_id) -> None:
        with self.base.factory() as session, session.begin():
            job = session.get(Job, job_id)
            assert job is not None
            job.claim_token = uuid4()
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    def recover_and_claim(self, label: str):
        with self.base.factory() as session, session.begin():
            recovered = JobRepository(session).recover_expired_claim(
                now=datetime.now(UTC), job_type="export", requeue=True
            )
            assert recovered is not None
        return self.claim_next(label)

    def job_status(self, job_id):
        with self.base.factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            return job.status

    def export_counts(self) -> dict[str, int]:
        with self.base.factory() as session:
            export_asset_ids = select(Asset.asset_id).where(Asset.asset_type == AssetType.EXPORT)
            return {
                "assets": session.scalar(
                    select(func.count())
                    .select_from(Asset)
                    .where(Asset.asset_type == AssetType.EXPORT)
                ),
                "memberships": session.scalar(
                    select(func.count())
                    .select_from(ProjectAsset)
                    .where(ProjectAsset.role == "export")
                ),
                "versions": session.scalar(
                    select(func.count())
                    .select_from(AssetVersion)
                    .where(AssetVersion.asset_id.in_(export_asset_ids))
                ),
                "artifacts": session.scalar(
                    select(func.count())
                    .select_from(Artifact)
                    .where(
                        Artifact.asset_version_id.in_(
                            select(AssetVersion.asset_version_id).where(
                                AssetVersion.asset_id.in_(export_asset_ids)
                            )
                        )
                    )
                ),
                "locations": session.scalar(
                    select(func.count())
                    .select_from(ArtifactStorageLocation)
                    .where(
                        ArtifactStorageLocation.artifact_id.in_(
                            select(Artifact.artifact_id).where(
                                Artifact.asset_version_id.in_(
                                    select(AssetVersion.asset_version_id).where(
                                        AssetVersion.asset_id.in_(export_asset_ids)
                                    )
                                )
                            )
                        )
                    )
                ),
                "outputs": session.scalar(select(func.count()).select_from(JobOutput)),
                "results": session.scalar(select(func.count()).select_from(JobExportResult)),
                "publications": session.scalar(
                    select(func.count()).select_from(JobExportPublication)
                ),
                "physical": len(tuple(self.base.roots.roots["music"].rglob("result.*"))),
            }

    def completed_jobs(self, job_ids: list) -> list[Job]:
        with self.base.factory() as session:
            return list(
                session.scalars(select(Job).where(Job.job_id.in_(job_ids)).order_by(Job.created_at))
            )

    def close(self) -> None:
        self.base.engine.dispose()
