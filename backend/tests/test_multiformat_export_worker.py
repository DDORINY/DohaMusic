"""Real MP3/FLAC Export Worker, delivery, replay, and signal proofs."""

from __future__ import annotations

import array
import hashlib
import math
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.audio.export_delivery_encoder import CanonicalExportDeliveryEncoder
from backend.audio.export_delivery_validator import ExportDeliveryFormat, ExportDeliveryValidator
from backend.audio.working_preview_renderer import FfmpegWorkingCompositionPreviewRenderer
from backend.core.exceptions import ApplicationValidationError, IdempotencyConflictError
from backend.models.workspace import (
    Artifact,
    AssetVersion,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    ExportPublicationState,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    JobStatus,
)
from backend.services.workspace.artifact_application_service import ArtifactApplicationService
from backend.services.workspace.artifact_ingestion_service import (
    ArtifactIngestionRequest,
    ArtifactIngestionService,
)
from backend.services.workspace.export_publication_service import (
    ExportPublicationError,
    ExportPublicationErrorCode,
    ExportPublicationService,
)
from backend.services.workspace.export_worker_service import ExportWorkerService
from backend.services.workspace.job_service import JobService
from backend.storage.artifact_publisher import (
    LocalArtifactPublisher,
    PublicationOutcome,
    TrustedPublicationIdentity,
)
from backend.tests.test_export_job_completion_uow import _service as completion_service
from backend.tests.test_export_publication_ledger import LedgerFixture
from backend.tests.test_export_worker_happy_path import RATE, _source_wav


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _decode(ffmpeg: str, path: Path) -> bytes:
    return subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "48000", "-"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
        shell=False,
    ).stdout


def _similarity(reference: bytes, decoded: bytes) -> tuple[float, float]:
    left = array.array("h", reference)
    right = array.array("h", decoded)
    best_offset = 0
    best_correlation = -1.0
    for offset in range(-2304, 2305):
        start_left = max(0, -offset)
        start_right = max(0, offset)
        length = min(len(left) - start_left, len(right) - start_right)
        if length < RATE:
            continue
        a = left[start_left : start_left + length : 128]
        b = right[start_right : start_right + length : 128]
        energy = sum(value * value for value in a)
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        right_energy = sum(value * value for value in b)
        correlation = dot / math.sqrt(energy * right_energy)
        if correlation > best_correlation:
            best_offset, best_correlation = offset, correlation
    start_left = max(0, -best_offset)
    start_right = max(0, best_offset)
    length = min(len(left) - start_left, len(right) - start_right)
    edge_samples = 1152 * 2
    start_left += edge_samples
    start_right += edge_samples
    length -= edge_samples * 2
    a = left[start_left : start_left + length]
    b = right[start_right : start_right + length]
    energy = sum(value * value for value in a)
    error = sum((x - y) ** 2 for x, y in zip(a, b, strict=True))
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    right_energy = sum(value * value for value in b)
    return 10 * math.log10(energy / max(error, 1)), dot / math.sqrt(energy * right_energy)


@pytest.mark.parametrize(
    ("export_format", "media_type", "mode", "amplitude", "muted"),
    [
        (ExportDeliveryFormat.MP3, "audio/mpeg", "audible", 0.12, False),
        (ExportDeliveryFormat.FLAC, "audio/flac", "audible", 0.12, False),
        (ExportDeliveryFormat.MP3, "audio/mpeg", "silent", 0.12, True),
        (ExportDeliveryFormat.FLAC, "audio/flac", "silent", 0.12, True),
        (ExportDeliveryFormat.MP3, "audio/mpeg", "low", 0.003, False),
        (ExportDeliveryFormat.FLAC, "audio/flac", "low", 0.003, False),
    ],
)
def test_real_multiformat_worker_completion_replay_and_signal(
    tmp_path: Path,
    export_format: ExportDeliveryFormat,
    media_type: str,
    mode: str,
    amplitude: float,
    muted: bool,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    fixture = LedgerFixture(tmp_path)
    source = fixture.staging / "source.wav"
    source.write_bytes(_source_wav(frequency=997, amplitude=amplitude))
    ingestion = ArtifactIngestionService(
        fixture.factory, artifact_roots=fixture.roots, staging_root=fixture.staging
    )
    ingestion.ingest(
        ArtifactIngestionRequest(
            asset_version_id=fixture.version.asset_version_id,
            artifact_kind="audio",
            producer_type="provider",
            storage_domain="music",
            temporary_path=source,
            expected_media_type="audio/wav",
            expected_sha256=None,
            original_filename="source.wav",
        )
    )
    track_id = uuid4()
    with fixture.factory() as session, session.begin():
        job = session.get(Job, fixture.job.job_id)
        job.settings_snapshot = {"format": export_format.value}
        session.add(
            CompositionSnapshotTrack(
                snapshot_track_id=track_id,
                composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                canonical_track_id=uuid4(),
                track_type="audio",
                name="Export",
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
                composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                snapshot_track_id=track_id,
                canonical_clip_id=uuid4(),
                source_asset_version_id=fixture.version.asset_version_id,
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
    artifacts = ArtifactApplicationService(fixture.factory, artifact_roots=fixture.roots)
    renderer = FfmpegWorkingCompositionPreviewRenderer(
        ffmpeg_executable=ffmpeg,
        temp_root=fixture.staging / "render",
        open_artifact=lambda _artifact_id: None,
    )
    worker = ExportWorkerService(
        fixture.factory,
        artifacts=artifacts,
        renderer=renderer,
        publications=fixture.service(),
        completion=completion_service(fixture),
        encoder=CanonicalExportDeliveryEncoder(
            ffmpeg_executable=ffmpeg, temp_root=fixture.staging / "encode"
        ),
        delivery_validator=ExportDeliveryValidator(ffmpeg_executable=ffmpeg),
    )
    completed = worker.execute_owned_claim(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
    )
    with fixture.factory() as session:
        job = session.get(Job, fixture.job.job_id)
        ledger = session.get(JobExportPublication, fixture.job.job_id)
        result = session.get(JobExportResult, fixture.job.job_id)
        artifact = session.get(Artifact, completed.job_output.artifact_id)
        assert job.status is JobStatus.SUCCEEDED
        assert ledger.state is ExportPublicationState.COMPLETED
        assert result.export_format == export_format.value
        assert artifact.media_type == media_type
        if mode == "silent":
            assert result.integrated_loudness_lufs is None
            assert result.true_peak_dbtp is None
        else:
            assert result.integrated_loudness_lufs is not None
            assert result.true_peak_dbtp is not None
        assert _count(session, JobOutput) == 1
        assert _count(session, JobExportResult) == 1
        assert _count(session, AssetVersion) == 2
        assert _count(session, Artifact) == 2
    final_path = next(fixture.roots.roots["music"].rglob(f"result.{export_format.value}"))
    validated = ExportDeliveryValidator(ffmpeg_executable=ffmpeg).validate(
        final_path, expected_format=export_format, expected_duration_us=1_000_000
    )
    assert (validated.sample_rate, validated.channels) == (RATE, 2)
    decoded = _decode(ffmpeg, final_path)
    reference = _source_wav(frequency=997, amplitude=amplitude)[44:]
    if mode == "silent":
        samples = array.array("h", decoded)
        assert max((abs(value) for value in samples), default=0) == 0
    elif export_format is ExportDeliveryFormat.FLAC:
        assert decoded == reference
    elif mode == "audible":
        snr, correlation = _similarity(reference, decoded)
        assert snr >= 20.0, (snr, correlation)
        assert correlation >= 0.995, (snr, correlation)
    moved = final_path.with_suffix(f".{export_format.value}.released")
    final_path.rename(moved)
    moved.rename(final_path)
    replay = ExportWorkerService(
        fixture.factory,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=completion_service(fixture),
        analyzer=object(),
    ).execute_owned_claim(job_id=fixture.job.job_id, claimed_by="lost", claim_token=uuid4())
    assert replay.export_result.exported_artifact_id == completed.export_result.exported_artifact_id
    fixture.engine.dispose()


@pytest.mark.parametrize("export_format", [ExportDeliveryFormat.MP3, ExportDeliveryFormat.FLAC])
def test_format_specific_publish_crash_recovery_and_concurrent_adoption(
    tmp_path: Path, export_format: ExportDeliveryFormat
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    fixture = LedgerFixture(tmp_path)
    service = fixture.service()
    service.ensure_intent(
        job_id=fixture.job.job_id,
        composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
        export_format=export_format.value,
    )
    canonical = fixture.staging / "canonical.wav"
    canonical.write_bytes(_source_wav(frequency=733, amplitude=0.08))
    encoder = CanonicalExportDeliveryEncoder(
        ffmpeg_executable=ffmpeg, temp_root=fixture.staging / "encode"
    )
    with encoder.encode(canonical, export_format=export_format) as delivery:
        ExportDeliveryValidator(ffmpeg_executable=ffmpeg).validate(
            delivery.path, expected_format=export_format, expected_duration_us=1_000_000
        )
        payload = delivery.path.read_bytes()
        publication = service.set_expected_integrity(
            job_id=fixture.job.job_id,
            claimed_by=fixture.claimed_by,
            claim_token=fixture.claim_token,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        stable_stage = fixture.staging / f"stable.{export_format.value}"
        stable_stage.write_bytes(payload)
    identity = TrustedPublicationIdentity.for_export(fixture.job.job_id, export_format.value)

    def publish() -> PublicationOutcome:
        publisher = LocalArtifactPublisher(fixture.roots, fixture.staging)
        return publisher.publish_or_adopt(
            stable_stage,
            identity=identity,
            artifact_kind="audio",
            expected_media_type=(
                "audio/mpeg" if export_format is ExportDeliveryFormat.MP3 else "audio/flac"
            ),
            expected_sha256=publication.expected_sha256,
            expected_size_bytes=publication.expected_size_bytes,
        ).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = set(executor.map(lambda _: publish(), range(2)))
    assert outcomes == {PublicationOutcome.PUBLISHED_NEW, PublicationOutcome.ADOPTED_EXISTING}
    final_path = next(fixture.roots.roots["music"].rglob(f"result.{export_format.value}"))
    assert len(tuple(fixture.roots.roots["music"].rglob(f"result.{export_format.value}"))) == 1
    recovered = ExportPublicationService(
        fixture.factory, publisher=LocalArtifactPublisher(fixture.roots, fixture.staging)
    ).publish_or_recover(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
    )
    assert recovered.storage_outcome is PublicationOutcome.ADOPTED_EXISTING
    assert recovered.publication.state is ExportPublicationState.PUBLISHED
    ExportDeliveryValidator(ffmpeg_executable=ffmpeg).validate(
        final_path, expected_format=export_format, expected_duration_us=1_000_000
    )
    fixture.engine.dispose()


@pytest.mark.parametrize("export_format", [ExportDeliveryFormat.MP3, ExportDeliveryFormat.FLAC])
def test_format_specific_tamper_fails_closed(
    tmp_path: Path, export_format: ExportDeliveryFormat
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    fixture = LedgerFixture(tmp_path)
    service = fixture.service()
    service.ensure_intent(
        job_id=fixture.job.job_id,
        composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
        export_format=export_format.value,
    )
    canonical = fixture.staging / "canonical.wav"
    canonical.write_bytes(_source_wav())
    with CanonicalExportDeliveryEncoder(
        ffmpeg_executable=ffmpeg, temp_root=fixture.staging / "encode"
    ).encode(canonical, export_format=export_format) as delivery:
        payload = delivery.path.read_bytes()
        service.set_expected_integrity(
            job_id=fixture.job.job_id,
            claimed_by=fixture.claimed_by,
            claim_token=fixture.claim_token,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        stable = fixture.staging / f"stable.{export_format.value}"
        stable.write_bytes(payload)
        service.publish_or_recover(
            job_id=fixture.job.job_id,
            claimed_by=fixture.claimed_by,
            claim_token=fixture.claim_token,
            staged_payload=stable,
        )
    final_path = next(fixture.roots.roots["music"].rglob(f"result.{export_format.value}"))
    final_path.write_bytes(b"tampered")
    with pytest.raises(ExportPublicationError) as error:
        ExportPublicationService(
            fixture.factory, publisher=LocalArtifactPublisher(fixture.roots, fixture.staging)
        ).publish_or_recover(
            job_id=fixture.job.job_id,
            claimed_by=fixture.claimed_by,
            claim_token=fixture.claim_token,
        )
    assert error.value.code is ExportPublicationErrorCode.STORAGE_MISMATCH
    with fixture.factory() as session:
        assert session.get(JobExportPublication, fixture.job.job_id).state is (
            ExportPublicationState.RECONCILIATION_REQUIRED
        )
        assert session.get(JobExportResult, fixture.job.job_id) is None
    assert final_path.read_bytes() == b"tampered"
    fixture.engine.dispose()


@pytest.mark.parametrize("export_format", ["wav", "mp3", "flac"])
def test_job_creation_format_binding_and_idempotent_replay(
    tmp_path: Path, export_format: str
) -> None:
    fixture = LedgerFixture(tmp_path)
    service = JobService(fixture.factory)
    request = dict(
        effective_owner_id=fixture.owner,
        project_id=fixture.project.project_id,
        job_type="export",
        api_contract_version="1",
        settings_snapshot={"format": export_format},
        idempotency_key=f"format-{export_format}",
        composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
    )
    first = service.create_job_for_owner(**request)
    replay = service.create_job_for_owner(**request)
    assert replay.replayed
    assert replay.aggregate.job.job_id == first.aggregate.job.job_id
    with fixture.factory() as session:
        publication = session.get(JobExportPublication, first.aggregate.job.job_id)
        assert publication.export_format == export_format
        assert publication.storage_key.endswith(f"result.{export_format}")
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobExportPublication)
                .where(JobExportPublication.job_id == first.aggregate.job.job_id)
            )
            == 1
        )
    fixture.engine.dispose()


def test_job_creation_same_key_different_format_conflicts(tmp_path: Path) -> None:
    fixture = LedgerFixture(tmp_path)
    service = JobService(fixture.factory)
    common = dict(
        effective_owner_id=fixture.owner,
        project_id=fixture.project.project_id,
        job_type="export",
        api_contract_version="1",
        idempotency_key="same-key-format",
        composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
    )
    service.create_job_for_owner(settings_snapshot={"format": "mp3"}, **common)
    with pytest.raises(IdempotencyConflictError):
        service.create_job_for_owner(settings_snapshot={"format": "flac"}, **common)
    with pytest.raises(ApplicationValidationError):
        service.create_job_for_owner(
            settings_snapshot={"format": "ogg"},
            **{**common, "idempotency_key": "invalid-format"},
        )
    fixture.engine.dispose()


@pytest.mark.parametrize(
    ("expected_format", "actual_format"),
    [
        (ExportDeliveryFormat.MP3, ExportDeliveryFormat.FLAC),
        (ExportDeliveryFormat.FLAC, ExportDeliveryFormat.MP3),
    ],
)
def test_format_spoof_publication_fails_closed(
    tmp_path: Path,
    expected_format: ExportDeliveryFormat,
    actual_format: ExportDeliveryFormat,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    fixture = LedgerFixture(tmp_path)
    service = fixture.service()
    service.ensure_intent(
        job_id=fixture.job.job_id,
        composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
        export_format=expected_format.value,
    )
    canonical = fixture.staging / "canonical.wav"
    canonical.write_bytes(_source_wav())
    with CanonicalExportDeliveryEncoder(
        ffmpeg_executable=ffmpeg, temp_root=fixture.staging / "encode"
    ).encode(canonical, export_format=actual_format) as delivery:
        spoof = fixture.staging / f"spoof.{expected_format.value}"
        spoof.write_bytes(delivery.path.read_bytes())
    payload = spoof.read_bytes()
    service.set_expected_integrity(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    with pytest.raises(ExportPublicationError) as error:
        service.publish_or_recover(
            job_id=fixture.job.job_id,
            claimed_by=fixture.claimed_by,
            claim_token=fixture.claim_token,
            staged_payload=spoof,
        )
    assert error.value.code is ExportPublicationErrorCode.STORAGE_MISMATCH
    with fixture.factory() as session:
        publication = session.get(JobExportPublication, fixture.job.job_id)
        assert publication.state is ExportPublicationState.RECONCILIATION_REQUIRED
        assert session.get(JobExportResult, fixture.job.job_id) is None
    assert not tuple(fixture.roots.roots["music"].rglob(f"result.{expected_format.value}"))
    fixture.engine.dispose()
