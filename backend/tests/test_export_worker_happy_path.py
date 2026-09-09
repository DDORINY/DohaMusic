"""Canonical WAV Export Worker end-to-end happy-path proof."""

from __future__ import annotations

import io
import math
import shutil
import struct
import wave
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from backend.audio.working_preview_renderer import (
    FfmpegWorkingCompositionPreviewRenderer,
    PreviewRenderClip,
    PreviewRenderTrack,
)
from backend.models.workspace import (
    Artifact,
    ArtifactStorageLocation,
    AssetVersion,
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    ExportPublicationState,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    JobStatus,
    WorkingComposition,
)
from backend.services.workspace.artifact_application_service import ArtifactApplicationService
from backend.services.workspace.artifact_ingestion_service import (
    ArtifactIngestionRequest,
    ArtifactIngestionService,
)
from backend.services.workspace.asset_service import AssetService
from backend.services.workspace.export_worker_service import ExportWorkerService
from backend.tests.test_export_job_completion_uow import _service as completion_service
from backend.tests.test_export_publication_ledger import LedgerFixture

RATE = 48_000
FRAMES = RATE


def _source_wav(*, frequency: int = 1_000, amplitude: float = 0.18) -> bytes:
    payload = io.BytesIO()
    frames = bytearray()
    for frame in range(FRAMES):
        sample = round(amplitude * 32_767 * math.sin(2 * math.pi * frequency * frame / RATE))
        frames.extend(struct.pack("<hh", sample, sample))
    with wave.open(payload, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)
    return payload.getvalue()


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("pan", ["-1", "-0.5", "0", "0.5", "1"])
def test_real_worker_happy_path_and_fresh_terminal_replay(tmp_path: Path, pan: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    fixture = LedgerFixture(tmp_path)
    source = fixture.staging / "source.wav"
    source.write_bytes(_source_wav())
    ingestion = ArtifactIngestionService(
        fixture.factory,
        artifact_roots=fixture.roots,
        staging_root=fixture.staging,
    )
    ingested = ingestion.ingest(
        ArtifactIngestionRequest(
            asset_version_id=fixture.version.asset_version_id,
            artifact_kind="audio",
            producer_type="provider",
            producer_id="export-worker-test",
            run_id="export-worker-happy-path",
            storage_domain="music",
            temporary_path=source,
            expected_media_type="audio/wav",
            expected_sha256=None,
            original_filename="source.wav",
        )
    )
    second_version = AssetService(fixture.factory).create_asset_version(
        asset_id=fixture.version.asset_id,
        version_origin="user_created",
        settings_snapshot={},
        created_by=fixture.owner,
    )
    second_source = fixture.staging / "source-b.wav"
    second_source.write_bytes(_source_wav(frequency=440, amplitude=0.11))
    second_ingested = ingestion.ingest(
        ArtifactIngestionRequest(
            asset_version_id=second_version.asset_version_id,
            artifact_kind="audio",
            producer_type="provider",
            producer_id="export-worker-test",
            run_id="export-worker-source-b",
            storage_domain="music",
            temporary_path=second_source,
            expected_media_type="audio/wav",
            expected_sha256=None,
            original_filename="source-b.wav",
        )
    )
    track_id = uuid4()
    second_track_id = uuid4()
    clip_id = uuid4()
    second_clip_id = uuid4()
    with fixture.factory() as session, session.begin():
        snapshot = session.get(CompositionSnapshot, fixture.snapshot.composition_snapshot_id)
        assert snapshot is not None
        snapshot.master_gain_db = Decimal("-2")
        session.add_all(
            [
                CompositionSnapshotTrack(
                    snapshot_track_id=track_id,
                    composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                    canonical_track_id=uuid4(),
                    track_type="audio",
                    name="Frozen Track",
                    track_order=0,
                    gain_db=Decimal("-3"),
                    pan=Decimal(pan),
                    muted=False,
                    solo=False,
                ),
                CompositionSnapshotTrack(
                    snapshot_track_id=second_track_id,
                    composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                    canonical_track_id=uuid4(),
                    track_type="audio",
                    name="Frozen Track B",
                    track_order=1,
                    gain_db=Decimal("-5"),
                    pan=Decimal("0.5"),
                    muted=False,
                    solo=False,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                CompositionSnapshotClip(
                    composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                    snapshot_track_id=track_id,
                    canonical_clip_id=clip_id,
                    source_asset_version_id=fixture.version.asset_version_id,
                    timeline_start=0,
                    source_in=0,
                    source_out=250_000,
                    source_duration=1_000_000,
                    timeline_duration=1_000_000,
                    loop_enabled=True,
                    loop_phase=0,
                    gain_db=Decimal("-4"),
                    fade_in=100_000,
                    fade_out=100_000,
                ),
                CompositionSnapshotClip(
                    composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
                    snapshot_track_id=second_track_id,
                    canonical_clip_id=second_clip_id,
                    source_asset_version_id=second_version.asset_version_id,
                    timeline_start=0,
                    source_in=0,
                    source_out=1_000_000,
                    source_duration=1_000_000,
                    timeline_duration=1_000_000,
                    loop_enabled=False,
                    loop_phase=0,
                    gain_db=Decimal("-2"),
                    fade_in=0,
                    fade_out=0,
                ),
            ]
        )
        working = WorkingComposition(
            project_id=fixture.project.project_id,
            base_composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
            mix_settings={},
            master_gain_db=Decimal("18"),
            revision=7,
        )
        session.add(working)
        session.flush()
        live_track = CompositionTrack(
            working_composition_id=working.working_composition_id,
            track_type="audio",
            name="Mutated Live Track",
            track_order=0,
        )
        session.add(live_track)
        session.flush()
        session.add(
            CompositionClip(
                working_composition_id=working.working_composition_id,
                track_id=live_track.track_id,
                source_asset_version_id=second_version.asset_version_id,
                timeline_start=200_000,
                source_in=250_000,
                source_out=750_000,
                source_duration=1_000_000,
                timeline_duration=500_000,
                loop_enabled=False,
                loop_phase=0,
                gain_db=Decimal("12"),
                fade_in=0,
                fade_out=300_000,
            )
        )
    artifacts = ArtifactApplicationService(fixture.factory, artifact_roots=fixture.roots)
    renderer = FfmpegWorkingCompositionPreviewRenderer(
        ffmpeg_executable=ffmpeg,
        temp_root=fixture.staging / "render",
        open_artifact=lambda _artifact_id: None,
    )

    @contextmanager
    def open_reference_artifact(artifact_id: UUID):
        with artifacts.open_content_for_owner(artifact_id, effective_owner_id=fixture.owner) as (
            handle,
            stream,
        ):
            yield handle.size_bytes, stream

    with (
        renderer.render(
            [
                PreviewRenderClip(
                    clip_id=clip_id,
                    track_order=0,
                    canonical_order=0,
                    artifact_id=ingested.artifact_id,
                    source_in_us=0,
                    source_out_us=250_000,
                    timeline_start_us=0,
                    timeline_duration_us=1_000_000,
                    manifest_schema=5,
                    loop_enabled=True,
                    loop_phase_us=0,
                    gain_db=Decimal("-4"),
                    fade_in_us=100_000,
                    fade_out_us=100_000,
                ),
                PreviewRenderClip(
                    clip_id=second_clip_id,
                    track_order=1,
                    canonical_order=1,
                    artifact_id=second_ingested.artifact_id,
                    source_in_us=0,
                    source_out_us=1_000_000,
                    timeline_start_us=0,
                    timeline_duration_us=1_000_000,
                    manifest_schema=5,
                    gain_db=Decimal("-2"),
                ),
            ],
            track_count=2,
            tracks=[
                PreviewRenderTrack(0, Decimal("-3"), Decimal(pan), False, False),
                PreviewRenderTrack(1, Decimal("-5"), Decimal("0.5"), False, False),
            ],
            master_gain_db=Decimal("-2"),
            open_artifact=open_reference_artifact,
        ) as rendered,
        wave.open(str(rendered.path), "rb") as reference,
    ):
        reference_pcm = reference.readframes(reference.getnframes())
    worker = ExportWorkerService(
        fixture.factory,
        artifacts=artifacts,
        renderer=renderer,
        publications=fixture.service(),
        completion=completion_service(fixture),
    )

    result = worker.execute_owned_claim(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
    )

    with fixture.factory() as session:
        job = session.get(Job, fixture.job.job_id)
        ledger = session.get(JobExportPublication, fixture.job.job_id)
        export_result = session.get(JobExportResult, fixture.job.job_id)
        output = session.scalar(select(JobOutput).where(JobOutput.job_id == fixture.job.job_id))
        assert job is not None and job.status is JobStatus.SUCCEEDED
        assert ledger is not None and ledger.state is ExportPublicationState.COMPLETED
        assert result.export_result.job_id == export_result.job_id
        assert result.job_output.artifact_id == output.artifact_id
        assert export_result.exported_artifact_id == output.artifact_id == ledger.artifact_id
        assert export_result.integrated_loudness_lufs is not None
        assert export_result.true_peak_dbtp is not None
        assert not export_result.overall_pass
        assert _count(session, AssetVersion) == 3
        assert _count(session, Artifact) == 3
        assert _count(session, ArtifactStorageLocation) == 3
        assert _count(session, JobOutput) == 1
        assert _count(session, JobExportResult) == 1
    publications = tuple(fixture.roots.roots["music"].rglob("*.wav"))
    assert len(publications) == 3
    source_names = {f"{ingested.artifact_id}.wav", f"{second_ingested.artifact_id}.wav"}
    final_path = next(path for path in publications if path.name not in source_names)
    with wave.open(str(final_path), "rb") as output:
        assert output.getframerate() == RATE
        assert output.getnchannels() == 2
        assert output.getsampwidth() == 2
        assert output.getnframes() == FRAMES
        export_pcm = output.readframes(output.getnframes())
        assert export_pcm == reference_pcm
        assert export_pcm != _source_wav(frequency=440, amplitude=0.11)[44:]
    moved = final_path.with_suffix(".released")
    final_path.rename(moved)
    moved.rename(final_path)

    replay_worker = ExportWorkerService(
        fixture.factory,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=completion_service(fixture),
        analyzer=object(),
    )
    replay = replay_worker.execute_owned_claim(
        job_id=fixture.job.job_id,
        claimed_by="response-lost",
        claim_token=uuid4(),
    )
    assert replay.export_result.job_id == result.export_result.job_id
    assert replay.export_result.exported_artifact_id == result.export_result.exported_artifact_id
    assert tuple(fixture.roots.roots["music"].rglob("*.wav")) == publications
    fixture.engine.dispose()
