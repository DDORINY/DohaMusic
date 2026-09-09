"""Working Preview ownership, manifest, replay, and completion contracts."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.api.exception_handlers import register_exception_handlers
from backend.api.v1.dependencies import register_request_id_middleware
from backend.api.v1.routes.working_compositions import router as working_router
from backend.core.exceptions import IdempotencyConflictError
from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.workspace import (
    Artifact,
    Asset,
    AssetType,
    AssetVersion,
    CompositionClip,
    CompositionSnapshot,
    CompositionTrack,
    Job,
    JobOutput,
    JobStatus,
    MusicProject,
    ProjectAsset,
    WorkingComposition,
    WorkingPreviewAsset,
    WorkingPreviewRender,
    WorkingPreviewRenderClip,
    WorkingPreviewRenderTrack,
    Workspace,
)
from backend.services.workspace import (
    ArtifactIngestionService,
    JobService,
    WorkingPreviewError,
    WorkingPreviewService,
    WorkspaceService,
)
from backend.storage import ArtifactStorageRoots


@dataclass(frozen=True, slots=True)
class PreviewGraph:
    owner_id: UUID
    project_id: UUID
    working_id: UUID
    source_artifact_id: UUID


@pytest.fixture
def preview_graph(tmp_path: Path):
    database = tmp_path / "preview.db"
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    owner_id = uuid4()
    workspace = Workspace(
        workspace_id=uuid4(), owner_id=owner_id, name="Preview", lifecycle_status="active"
    )
    project = MusicProject(
        project_id=uuid4(),
        workspace_id=workspace.workspace_id,
        title="Revision pin",
        lifecycle_status="active",
        created_by=owner_id,
    )
    source_asset = Asset(
        asset_id=uuid4(),
        workspace_id=workspace.workspace_id,
        owner_id=owner_id,
        asset_type=AssetType.MUSIC,
        lifecycle_status="active",
    )
    source_version = AssetVersion(
        asset_version_id=uuid4(),
        asset_id=source_asset.asset_id,
        version_number=1,
        version_origin="test",
        settings_snapshot={},
        created_by=owner_id,
    )
    source_artifact = Artifact(
        artifact_id=uuid4(),
        asset_version_id=source_version.asset_version_id,
        artifact_kind="audio",
        media_type="audio/wav",
        size_bytes=1_000,
        duration_us=30_000_000,
        checksum_algorithm="sha256",
        artifact_checksum="a" * 64,
        producer_type="workspace",
        retention_status="active",
    )
    working = WorkingComposition(
        working_composition_id=uuid4(),
        project_id=project.project_id,
        mix_settings={},
        revision=7,
    )
    track = CompositionTrack(
        track_id=uuid4(),
        working_composition_id=working.working_composition_id,
        track_type="audio",
        name="Audio",
        track_order=0,
    )
    clips = [
        CompositionClip(
            clip_id=uuid4(),
            working_composition_id=working.working_composition_id,
            track_id=track.track_id,
            source_asset_version_id=source_version.asset_version_id,
            timeline_start=index * 1_000_000,
            source_in=0,
            source_out=1_000_000,
            source_duration=30_000_000,
        )
        for index in range(17)
    ]
    with factory.begin() as session:
        session.add(workspace)
        session.flush()
        session.add_all([project, source_asset])
        session.flush()
        session.add(source_version)
        session.flush()
        session.add_all(
            [
                source_artifact,
                ProjectAsset(
                    project_id=project.project_id,
                    asset_id=source_asset.asset_id,
                    role="music",
                    display_order=0,
                ),
                working,
            ]
        )
        session.flush()
        session.add(track)
        session.flush()
        session.add_all(clips)
    graph = PreviewGraph(
        owner_id=owner_id,
        project_id=project.project_id,
        working_id=working.working_composition_id,
        source_artifact_id=source_artifact.artifact_id,
    )
    yield factory, graph, tmp_path
    assert engine.pool.checkedout() == 0
    engine.dispose()


def _create(service: WorkingPreviewService, graph: PreviewGraph, key: str = "preview"):
    return service.create_for_owner(
        project_id=graph.project_id,
        expected_revision=7,
        effective_owner_id=graph.owner_id,
        idempotency_key=key,
    )


def test_create_pins_more_than_sixteen_clips_without_composition_mutation(
    preview_graph,
) -> None:
    factory, graph, _ = preview_graph
    before = _composition_counts(factory, graph.working_id)
    result = _create(WorkingPreviewService(factory), graph)
    after = _composition_counts(factory, graph.working_id)

    assert result.status is JobStatus.QUEUED
    assert result.rendered_revision == 7
    assert before == after
    with factory() as session:
        render = session.get(WorkingPreviewRender, result.preview_render_id)
        clips = list(
            session.scalars(
                select(WorkingPreviewRenderClip)
                .where(WorkingPreviewRenderClip.preview_render_id == result.preview_render_id)
                .order_by(WorkingPreviewRenderClip.canonical_order)
            )
        )
        assert render is not None and render.preview_asset_version_id is None
        assert len(clips) == 17
        assert [item.canonical_order for item in clips] == list(range(17))
        assert {item.source_artifact_id for item in clips} == {graph.source_artifact_id}
        assert {item.gain_db for item in clips} == {Decimal("0.00")}
        assert {(item.fade_in_us, item.fade_out_us) for item in clips} == {(0, 0)}
        job = session.get(Job, result.job_id)
        assert job is not None and job.settings_snapshot == {"manifest_schema": 5}
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 0


def test_preview_manifest_pins_gain_without_rereading_working_clip(preview_graph) -> None:
    factory, graph, _ = preview_graph
    with factory.begin() as session:
        source = session.scalar(
            select(CompositionClip).where(
                CompositionClip.working_composition_id == graph.working_id
            )
        )
        assert source is not None
        source.gain_db = Decimal("6.00")
        source_clip_id = source.clip_id

    created = _create(WorkingPreviewService(factory), graph, "gain-pinned")
    with factory.begin() as session:
        source = session.get(CompositionClip, source_clip_id)
        assert source is not None
        source.gain_db = Decimal("-6.00")
    with factory() as session:
        pinned = session.get(
            WorkingPreviewRenderClip,
            {"preview_render_id": created.preview_render_id, "clip_id": source_clip_id},
        )
        assert pinned is not None and pinned.gain_db == Decimal("6.00")


def test_preview_manifest_pins_fade_without_rereading_working_clip(preview_graph) -> None:
    factory, graph, _ = preview_graph
    with factory.begin() as session:
        source = session.scalar(
            select(CompositionClip).where(
                CompositionClip.working_composition_id == graph.working_id
            )
        )
        assert source is not None
        source.fade_in = 250_001
        source.fade_out = 499_999
        source_clip_id = source.clip_id

    created = _create(WorkingPreviewService(factory), graph, "fade-pinned")
    with factory.begin() as session:
        source = session.get(CompositionClip, source_clip_id)
        assert source is not None
        source.fade_in = 0
        source.fade_out = 0
    with factory() as session:
        pinned = session.get(
            WorkingPreviewRenderClip,
            {"preview_render_id": created.preview_render_id, "clip_id": source_clip_id},
        )
        assert pinned is not None
        assert (pinned.fade_in_us, pinned.fade_out_us) == (250_001, 499_999)


def test_preview_manifest_pins_track_and_master_mixer_state(preview_graph) -> None:
    factory, graph, _ = preview_graph
    with factory.begin() as session:
        working = session.get(WorkingComposition, graph.working_id)
        track = session.scalar(
            select(CompositionTrack).where(
                CompositionTrack.working_composition_id == graph.working_id
            )
        )
        assert working is not None and track is not None
        working.master_gain_db = Decimal("-3.00")
        track.gain_db, track.pan, track.muted, track.solo = (
            Decimal("2.00"),
            Decimal("0.50"),
            False,
            True,
        )
        track_id = track.track_id
    created = _create(WorkingPreviewService(factory), graph, "mixer-pinned")
    with factory.begin() as session:
        working = session.get(WorkingComposition, graph.working_id)
        track = session.get(CompositionTrack, track_id)
        assert working is not None and track is not None
        working.master_gain_db = Decimal("0.00")
        track.gain_db, track.pan, track.muted, track.solo = (
            Decimal("0.00"),
            Decimal("0.00"),
            True,
            False,
        )
    with factory() as session:
        render = session.get(WorkingPreviewRender, created.preview_render_id)
        pinned = session.get(
            WorkingPreviewRenderTrack,
            {"preview_render_id": created.preview_render_id, "track_id": track_id},
        )
        assert render is not None and render.master_gain_db == Decimal("-3.00")
        assert pinned is not None
        assert (pinned.gain_db, pinned.pan, pinned.muted, pinned.solo) == (
            Decimal("2.00"),
            Decimal("0.50"),
            False,
            True,
        )


def test_preview_product_api_returns_async_job_without_locator_exposure(preview_graph) -> None:
    factory, graph, _ = preview_graph
    app = FastAPI()
    app.state.workspace_service = WorkspaceService(factory)
    app.state.working_preview_service = WorkingPreviewService(factory)
    register_request_id_middleware(app)
    register_exception_handlers(app)
    app.include_router(working_router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/projects/{graph.project_id}/working-composition/preview",
            headers={"Idempotency-Key": "api-preview"},
            json={"expected_revision": 7},
        )
    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["status"] == "queued"
    assert payload["rendered_revision"] == 7
    assert "artifact_id" not in payload
    assert "path" not in response.text.lower()
    assert "locator" not in response.text.lower()


def test_same_key_replays_job_and_different_fingerprint_conflicts(preview_graph) -> None:
    factory, graph, _ = preview_graph
    service = WorkingPreviewService(factory)
    first = _create(service, graph)
    with factory.begin() as session:
        working = session.get(WorkingComposition, graph.working_id)
        assert working is not None
        working.revision = 8
    replay = _create(service, graph)
    assert replay.replayed is True
    assert replay.job_id == first.job_id
    assert replay.preview_render_id == first.preview_render_id
    with pytest.raises(IdempotencyConflictError):
        service.create_for_owner(
            project_id=graph.project_id,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="preview",
        )
    with factory() as session:
        assert session.scalar(select(func.count(Job.job_id))) == 1
        assert session.scalar(select(func.count(AssetVersion.asset_version_id))) == 1


def test_new_actions_share_one_noncanonical_project_asset(preview_graph) -> None:
    factory, graph, _ = preview_graph
    service = WorkingPreviewService(factory)
    first = _create(service, graph, "one")
    second = _create(service, graph, "two")
    assert first.job_id != second.job_id
    with factory() as session:
        bindings = list(session.scalars(select(WorkingPreviewAsset)))
        renders = list(session.scalars(select(WorkingPreviewRender)))
        preview_asset = session.get(Asset, bindings[0].asset_id)
        assert len(bindings) == 1
        assert len(renders) == 2
        assert {item.preview_asset_id for item in renders} == {bindings[0].asset_id}
        assert preview_asset is not None and preview_asset.asset_type is AssetType.MIX
        assert preview_asset.selected_asset_version_id is None
        assert not session.scalar(
            select(ProjectAsset.project_asset_id).where(
                ProjectAsset.asset_id == preview_asset.asset_id
            )
        )


def test_retry_clones_exact_manifest_without_overwriting_version(preview_graph) -> None:
    factory, graph, _ = preview_graph
    first = _create(WorkingPreviewService(factory), graph)
    with factory.begin() as session:
        original = session.get(Job, first.job_id)
        assert original is not None
        original.status = JobStatus.FAILED
        original.error_code = "WORKING_PREVIEW_RENDER_FAILED"
        original.error_message = "Preview render failed."
        original.error_retryable = True
    retried = JobService(factory).retry_job_for_owner(
        first.job_id,
        effective_owner_id=graph.owner_id,
        idempotency_key="retry-preview",
    )
    retry_job = retried.aggregate.job
    with factory() as session:
        retry_render = session.scalar(
            select(WorkingPreviewRender).where(
                WorkingPreviewRender.workspace_job_id == retry_job.job_id
            )
        )
        assert retry_render is not None
        original_clips = list(
            session.scalars(
                select(WorkingPreviewRenderClip).where(
                    WorkingPreviewRenderClip.preview_render_id == first.preview_render_id
                )
            )
        )
        retry_clips = list(
            session.scalars(
                select(WorkingPreviewRenderClip).where(
                    WorkingPreviewRenderClip.preview_render_id == retry_render.preview_render_id
                )
            )
        )
        assert retry_job.retry_of_job_id == first.job_id
        assert retry_render.preview_asset_version_id is None
        assert retry_render.rendered_revision == 7
        assert [item.source_artifact_id for item in retry_clips] == [
            item.source_artifact_id for item in original_clips
        ]
        assert [item.gain_db for item in retry_clips] == [item.gain_db for item in original_clips]
        assert [(item.fade_in_us, item.fade_out_us) for item in retry_clips] == [
            (item.fade_in_us, item.fade_out_us) for item in original_clips
        ]


def test_success_atomically_creates_version_artifact_and_job_output(preview_graph) -> None:
    factory, graph, tmp_path = preview_graph
    artifact_root = tmp_path / "artifacts"
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    for domain in ("lm", "audio", "vocal", "music"):
        (artifact_root / domain).mkdir(parents=True)
    ingestion = ArtifactIngestionService(
        factory,
        artifact_roots=ArtifactStorageRoots.from_base_root(artifact_root),
        staging_root=staging_root,
    )
    service = WorkingPreviewService(factory, ingestion_service=ingestion)
    created = _create(service, graph)
    token = uuid4()
    with factory.begin() as session:
        job = session.get(Job, created.job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.claimed_by = "preview-worker"
        job.claim_token = token
    output = staging_root / "preview.wav"
    _write_wav(output)

    completed = service.complete_render(
        job_id=created.job_id,
        claimed_by="preview-worker",
        claim_token=token,
        output_path=output,
    )
    with factory() as session:
        render = session.get(WorkingPreviewRender, created.preview_render_id)
        version = session.get(AssetVersion, completed.preview_asset_version_id)
        artifact = session.get(Artifact, completed.artifact_id)
        job = session.get(Job, created.job_id)
        job_output = session.scalar(select(JobOutput).where(JobOutput.job_id == created.job_id))
        assert render is not None and render.preview_asset_version_id == version.asset_version_id
        assert artifact is not None and artifact.asset_version_id == version.asset_version_id
        assert artifact.retention_status == "active"
        assert job is not None and job.status is JobStatus.SUCCEEDED
        assert job_output is not None and job_output.artifact_id == artifact.artifact_id
        assert job_output.output_role == "working_preview"

    with factory.begin() as session:
        render = session.get(WorkingPreviewRender, created.preview_render_id)
        assert render is not None
        render.payload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert service.expire_due_preview_payloads() == 1
    with factory() as session:
        artifact = session.get(Artifact, completed.artifact_id)
        version = session.get(AssetVersion, completed.preview_asset_version_id)
        render = session.get(WorkingPreviewRender, created.preview_render_id)
        assert artifact is not None and artifact.retention_status == "expired"
        assert version is not None and render is not None
        assert render.preview_asset_version_id == version.asset_version_id


def test_invalid_completion_compensates_payload_without_partial_rows(preview_graph) -> None:
    factory, graph, tmp_path = preview_graph
    artifact_root = tmp_path / "invalid-artifacts"
    staging_root = tmp_path / "invalid-staging"
    staging_root.mkdir()
    for domain in ("lm", "audio", "vocal", "music"):
        (artifact_root / domain).mkdir(parents=True)
    service = WorkingPreviewService(
        factory,
        ingestion_service=ArtifactIngestionService(
            factory,
            artifact_roots=ArtifactStorageRoots.from_base_root(artifact_root),
            staging_root=staging_root,
        ),
    )
    created = _create(service, graph)
    token = uuid4()
    with factory.begin() as session:
        job = session.get(Job, created.job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.claimed_by = "preview-worker"
        job.claim_token = token
    output = staging_root / "wrong-duration.wav"
    with wave.open(str(output), "wb") as invalid:
        invalid.setnchannels(2)
        invalid.setsampwidth(2)
        invalid.setframerate(48_000)
        invalid.writeframes(b"\0\0\0\0" * 48_000)

    with pytest.raises(WorkingPreviewError):
        service.complete_render(
            job_id=created.job_id,
            claimed_by="preview-worker",
            claim_token=token,
            output_path=output,
        )

    with factory() as session:
        render = session.get(WorkingPreviewRender, created.preview_render_id)
        job = session.get(Job, created.job_id)
        assert render is not None and render.preview_asset_version_id is None
        assert job is not None and job.status is JobStatus.RUNNING
        assert session.scalar(select(func.count(AssetVersion.asset_version_id))) == 1
        assert session.scalar(select(func.count(Artifact.artifact_id))) == 1
        assert session.scalar(select(func.count(JobOutput.job_output_id))) == 0
    assert not [path for path in (artifact_root / "music").rglob("*") if path.is_file()]


def _composition_counts(factory, working_id: UUID) -> tuple[int, int, int]:
    with factory() as session:
        working = session.get(WorkingComposition, working_id)
        return (
            working.revision,
            session.scalar(
                select(func.count(CompositionTrack.track_id)).where(
                    CompositionTrack.working_composition_id == working_id
                )
            ),
            session.scalar(
                select(func.count(CompositionClip.clip_id)).where(
                    CompositionClip.working_composition_id == working_id
                )
            ),
        )


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\0\0\0\0" * (17 * 48_000))
