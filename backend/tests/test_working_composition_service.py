"""WorkingComposition atomic mutation and replay contract tests."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

import backend.services.workspace.working_composition_service as working_composition_module
from backend.api.exception_handlers import register_exception_handlers
from backend.api.v1.dependencies import register_request_id_middleware
from backend.api.v1.routes.working_compositions import router as working_router
from backend.core.exceptions import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    ResourceNotFoundError,
    WorkspaceBootstrapRequiredError,
)
from backend.core.idempotency_completion import IdempotencyResultType
from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.idempotency_record import IdempotencyRecord
from backend.models.workspace import (
    WORKSPACE_ENTITY_CLASSES,
    Artifact,
    Asset,
    AssetType,
    AssetVersion,
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    MusicProject,
    ProjectAsset,
    WorkingComposition,
    WorkingCompositionHistoryEntry,
    WorkingCompositionHistoryState,
    WorkingPreviewRender,
    Workspace,
)
from backend.repositories.idempotency_repository import IdempotencyRepository
from backend.repositories.workspace import CompositionRepository
from backend.services.workspace import (
    WorkingCompositionError,
    WorkingCompositionErrorCode,
    WorkingCompositionService,
    WorkingMutationResult,
    WorkspaceService,
)
from backend.services.workspace.working_composition_service import _fingerprint


@dataclass(frozen=True, slots=True)
class Graph:
    owner_id: UUID
    workspace_id: UUID
    project_id: UUID
    asset_version_id: UUID


@pytest.fixture(scope="module")
def schema_template(tmp_path_factory) -> Path:
    template = tmp_path_factory.mktemp("working-schema") / "template.db"
    engine = create_database_engine(f"sqlite:///{template.as_posix()}")
    tables = [entity.__table__ for entity in WORKSPACE_ENTITY_CLASSES]
    tables.extend(
        [WorkingCompositionHistoryState.__table__, WorkingCompositionHistoryEntry.__table__]
    )
    tables.append(IdempotencyRecord.__table__)
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()
    return template


@pytest.fixture
def session_factory(tmp_path: Path, schema_template: Path):
    database = tmp_path / "working-composition.db"
    shutil.copy2(schema_template, database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    assert engine.pool.checkedout() == 0
    engine.dispose()


@pytest.fixture
def graph(session_factory) -> Graph:
    owner_id = uuid4()
    workspace = Workspace(
        workspace_id=uuid4(),
        owner_id=owner_id,
        name="Working Composition",
        lifecycle_status="active",
    )
    project = MusicProject(
        project_id=uuid4(),
        workspace_id=workspace.workspace_id,
        title="Atomic Editing",
        lifecycle_status="active",
        created_by=owner_id,
    )
    asset = Asset(
        asset_id=uuid4(),
        workspace_id=workspace.workspace_id,
        owner_id=owner_id,
        asset_type=AssetType.MUSIC,
        lifecycle_status="active",
    )
    version = AssetVersion(
        asset_version_id=uuid4(),
        asset_id=asset.asset_id,
        version_number=1,
        version_origin="generated",
        settings_snapshot={},
        created_by=owner_id,
    )
    artifact = Artifact(
        artifact_id=uuid4(),
        asset_version_id=version.asset_version_id,
        artifact_kind="audio",
        media_type="audio/wav",
        size_bytes=1_000,
        duration_us=10_000_000,
        checksum_algorithm="sha256",
        artifact_checksum="a" * 64,
        producer_type="workspace",
        retention_status="active",
    )
    project_asset = ProjectAsset(
        project_asset_id=uuid4(),
        project_id=project.project_id,
        asset_id=asset.asset_id,
        role="music",
        display_order=0,
    )
    with session_factory.begin() as session:
        session.add(workspace)
        session.flush()
        session.add_all([project, asset])
        session.flush()
        session.add(version)
        session.flush()
        session.add_all([artifact, project_asset])
    return Graph(
        owner_id=owner_id,
        workspace_id=workspace.workspace_id,
        project_id=project.project_id,
        asset_version_id=version.asset_version_id,
    )


@pytest.fixture
def service(session_factory) -> WorkingCompositionService:
    return WorkingCompositionService(session_factory)


@pytest.fixture
def working_client(service, session_factory, graph):
    app = FastAPI()
    app.state.working_composition_service = service
    app.state.workspace_service = WorkspaceService(session_factory)
    register_request_id_middleware(app)
    register_exception_handlers(app)
    app.include_router(working_router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client


def _initialize(service: WorkingCompositionService, graph: Graph, key: str = "init"):
    return service.initialize(
        graph.project_id,
        effective_owner_id=graph.owner_id,
        idempotency_key=key,
    )


def _create_track(
    service: WorkingCompositionService,
    graph: Graph,
    working_id: UUID,
    revision: int,
    *,
    key: str = "track",
    name: str = "Audio",
):
    return service.create_track(
        graph.project_id,
        working_composition_id=working_id,
        name=name,
        expected_revision=revision,
        effective_owner_id=graph.owner_id,
        idempotency_key=key,
    )


def _create_clip(
    service: WorkingCompositionService,
    graph: Graph,
    working_id: UUID,
    track_id: UUID,
    revision: int,
    *,
    key: str = "clip",
    timeline_start: str = "0",
):
    return service.create_clip(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        source_asset_version_id=graph.asset_version_id,
        timeline_start=timeline_start,
        source_in="0",
        source_out="4",
        expected_revision=revision,
        effective_owner_id=graph.owner_id,
        idempotency_key=key,
    )


def test_first_initialize_creates_revision_zero_and_completion(
    service, session_factory, graph
) -> None:
    result = _initialize(service, graph)
    working_id = result.identities["working_composition_id"]
    assert result.completed_revision == 0
    assert result.replayed is False
    with session_factory() as session:
        working = session.get(WorkingComposition, working_id)
        record = session.scalar(select(IdempotencyRecord))
        assert working is not None and working.revision == 0
        assert record is not None and record.status == "COMPLETED"
        assert record.completed_revision == 0


def test_same_initialize_key_replays_first_identity_and_revision(
    service, session_factory, graph
) -> None:
    first = _initialize(service, graph)
    _create_track(
        service,
        graph,
        first.identities["working_composition_id"],
        0,
        key="later-track",
    )
    replay = _initialize(service, graph)
    assert replay.replayed is True
    assert replay.identities == first.identities
    assert replay.completed_revision == first.completed_revision == 0
    with session_factory() as session:
        assert session.scalar(select(func.count(WorkingComposition.working_composition_id))) == 1
        working = session.get(WorkingComposition, first.identities["working_composition_id"])
        assert working is not None and working.revision == 1
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == 2


def test_different_initialize_key_is_product_conflict_without_mutation(
    service, session_factory, graph
) -> None:
    first = _initialize(service, graph)
    with pytest.raises(WorkingCompositionError) as caught:
        _initialize(service, graph, "different")
    assert caught.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_ALREADY_EXISTS
    with session_factory() as session:
        working = session.get(WorkingComposition, first.identities["working_composition_id"])
        assert working is not None and working.revision == 0
        assert session.scalar(select(func.count(CompositionTrack.track_id))) == 0
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 0
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == 1


def test_concurrent_initialize_has_exactly_one_success_and_no_loser_partial_rows(
    service, session_factory, graph
) -> None:
    def attempt(key: str):
        try:
            return _initialize(service, graph, key)
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["race-a", "race-b"]))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_ALREADY_EXISTS
    with session_factory() as session:
        assert session.scalar(select(func.count(WorkingComposition.working_composition_id))) == 1
        assert session.scalar(select(func.count(CompositionTrack.track_id))) == 0
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 0
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == 1


def test_read_has_no_implicit_create_and_returns_ordered_derived_duration(
    service, session_factory, graph
) -> None:
    with pytest.raises(WorkingCompositionError) as caught:
        service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert caught.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND
    with session_factory() as session:
        assert session.scalar(select(func.count(WorkingComposition.working_composition_id))) == 0

    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    second = _create_track(service, graph, working_id, 0, key="track-a", name="A")
    first_track = second.identities["track_id"]
    _create_clip(service, graph, working_id, first_track, 1, timeline_start="2")
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert [track.track_order for track in aggregate.tracks] == [0]
    assert aggregate.timeline_duration_us == 6_000_000


def test_track_create_replay_and_fingerprint_conflict(service, session_factory, graph) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    first = _create_track(service, graph, working_id, 0)
    replay = _create_track(service, graph, working_id, 0)
    assert replay.replayed is True
    assert replay.identities == first.identities
    assert replay.completed_revision == 1
    with pytest.raises(IdempotencyConflictError):
        _create_track(service, graph, working_id, 0, name="Different")
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionTrack.track_id))) == 1


def test_revision_stale_rejects_and_success_increments_once(service, graph) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    track = _create_track(service, graph, working_id, 0)
    track_id = track.identities["track_id"]
    renamed = service.rename_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        name="Renamed",
        expected_revision=1,
        effective_owner_id=graph.owner_id,
    )
    assert renamed.completed_revision == 2
    with pytest.raises(WorkingCompositionError) as caught:
        service.rename_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=track_id,
            name="Stale",
            expected_revision=1,
            effective_owner_id=graph.owner_id,
        )
    assert caught.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT


def test_clip_create_uses_trusted_duration_overlap_and_adjacency(service, graph) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    track = _create_track(service, graph, working_id, 0)
    track_id = track.identities["track_id"]
    first = _create_clip(service, graph, working_id, track_id, 1)
    assert first.completed_revision == 2
    adjacent = _create_clip(
        service,
        graph,
        working_id,
        track_id,
        2,
        key="adjacent",
        timeline_start="4",
    )
    assert adjacent.completed_revision == 3
    with pytest.raises(WorkingCompositionError) as caught:
        _create_clip(
            service,
            graph,
            working_id,
            track_id,
            3,
            key="overlap",
            timeline_start="3.5",
        )
    assert caught.value.code is WorkingCompositionErrorCode.CLIP_OVERLAP


def test_track_not_empty_and_clip_delete_tombstones_only(service, session_factory, graph) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    track = _create_track(service, graph, working_id, 0)
    track_id = track.identities["track_id"]
    clip = _create_clip(service, graph, working_id, track_id, 1)
    clip_id = clip.identities["clip_id"]
    with pytest.raises(WorkingCompositionError) as caught:
        service.delete_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=track_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="delete-track",
        )
    assert caught.value.code is WorkingCompositionErrorCode.TRACK_NOT_EMPTY
    deleted = service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-clip",
    )
    assert deleted.completed_revision == 3
    with session_factory() as session:
        persisted = session.get(CompositionClip, clip_id)
        assert persisted is not None and persisted.deleted_at is not None
        assert persisted.source_asset_version_id == graph.asset_version_id


def test_split_replay_keeps_first_revision_and_children_after_later_mutation(
    service, graph
) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    track = _create_track(service, graph, working_id, 0)
    clip = _create_clip(service, graph, working_id, track.identities["track_id"], 1)
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip.identities["clip_id"],
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split",
    )
    service.rename_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track.identities["track_id"],
        name="Later",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    replay = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip.identities["clip_id"],
        split_at="2.0",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split",
    )
    assert replay.replayed is True
    assert replay.completed_revision == split.completed_revision == 3
    assert replay.identities == split.identities


def test_split_forced_failure_rolls_back_original_children_revision_and_completion(
    service, session_factory, graph, monkeypatch
) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    track = _create_track(service, graph, working_id, 0)
    clip = _create_clip(service, graph, working_id, track.identities["track_id"], 1)
    original_add = CompositionRepository.add_composition_clip
    calls = 0

    def fail_second(repository, candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced split failure")
        return original_add(repository, candidate)

    monkeypatch.setattr(CompositionRepository, "add_composition_clip", fail_second)
    with pytest.raises(RuntimeError, match="forced split failure"):
        service.split_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip.identities["clip_id"],
            split_at="2",
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="failed-split",
        )
    with session_factory() as session:
        original = session.get(CompositionClip, clip.identities["clip_id"])
        working = session.get(WorkingComposition, working_id)
        assert original is not None and original.deleted_at is None
        assert working is not None and working.revision == 2
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 1
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == "CLIP_SPLIT"
                )
            )
            == 0
        )


def test_checkout_replaces_atomically_and_replays_first_base_revision(
    service, session_factory, graph
) -> None:
    initialized = _initialize(service, graph)
    working_id = initialized.identities["working_composition_id"]
    _create_track(service, graph, working_id, 0)
    snapshot_id = uuid4()
    snapshot_track_id = uuid4()
    canonical_track_id = uuid4()
    canonical_clip_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            CompositionSnapshot(
                composition_snapshot_id=snapshot_id,
                project_id=graph.project_id,
                snapshot_version=1,
                mix_settings_snapshot={},
                provider_versions={},
                model_manifest_ids={},
                created_by=graph.owner_id,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshotTrack(
                snapshot_track_id=snapshot_track_id,
                composition_snapshot_id=snapshot_id,
                canonical_track_id=canonical_track_id,
                track_type="audio",
                name="Snapshot Track",
                track_order=0,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshotClip(
                composition_snapshot_id=snapshot_id,
                snapshot_track_id=snapshot_track_id,
                canonical_clip_id=canonical_clip_id,
                source_asset_version_id=graph.asset_version_id,
                timeline_start=0,
                source_in=0,
                source_out=2_000_000,
                source_duration=10_000_000,
            )
        )
    checkout = service.checkout(
        graph.project_id,
        working_composition_id=working_id,
        composition_snapshot_id=snapshot_id,
        expected_revision=1,
        effective_owner_id=graph.owner_id,
        idempotency_key="checkout",
    )
    assert checkout.completed_revision == 2
    service.rename_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=canonical_track_id,
        name="Later",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
    )
    replay = service.checkout(
        graph.project_id,
        working_composition_id=working_id,
        composition_snapshot_id=snapshot_id,
        expected_revision=1,
        effective_owner_id=graph.owner_id,
        idempotency_key="checkout",
    )
    assert replay.replayed is True
    assert replay.completed_revision == 2
    assert replay.identities["base_composition_snapshot_id"] == snapshot_id
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert aggregate.working_composition.revision == 3
    assert aggregate.tracks[0].name == "Later"


def test_product_router_exposes_service_results_and_structured_duplicate_error(
    working_client, graph
) -> None:
    base = f"/api/v1/projects/{graph.project_id}/working-composition"
    first = working_client.post(
        f"{base}/initialize",
        json={},
        headers={"Idempotency-Key": "api-init"},
    )
    assert first.status_code == 201
    initialized = first.json()["data"]
    assert initialized["completed_revision"] == 0
    working_id = initialized["working_composition_id"]

    replay = working_client.post(
        f"{base}/initialize",
        json={},
        headers={"Idempotency-Key": "api-init"},
    )
    assert replay.status_code == 201
    assert replay.json()["data"] == {**initialized, "replayed": True}

    duplicate = working_client.post(
        f"{base}/initialize",
        json={},
        headers={"Idempotency-Key": "api-new-key"},
    )
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["error_code"] == "WORKING_COMPOSITION_ALREADY_EXISTS"
    assert not any(
        token in str(error).lower()
        for token in ("storage", "locator", "sqlite", "constraint", "\\")
    )

    created_track = working_client.post(
        f"{base}/tracks",
        json={
            "working_composition_id": working_id,
            "expected_revision": 0,
            "name": "API Track",
        },
        headers={"Idempotency-Key": "api-track"},
    )
    assert created_track.status_code == 201
    assert created_track.json()["data"]["completed_revision"] == 1
    track_id = created_track.json()["data"]["track_id"]
    deleted_track = working_client.request(
        "DELETE",
        f"{base}/tracks/{track_id}",
        json={"working_composition_id": working_id, "expected_revision": 1},
        headers={"Idempotency-Key": "api-track-delete"},
    )
    assert deleted_track.status_code == 200
    restored_track = working_client.post(
        f"{base}/tracks/{track_id}/restore",
        json={
            "working_composition_id": working_id,
            "expected_revision": 2,
            "target_track_order": 0,
        },
        headers={"Idempotency-Key": "api-track-restore"},
    )
    assert restored_track.status_code == 200
    assert restored_track.json()["data"] == {
        "track_id": track_id,
        "completed_revision": 3,
        "replayed": False,
    }
    read = working_client.get(base)
    assert read.status_code == 200
    assert read.json()["data"]["tracks"][0]["name"] == "API Track"


def test_router_and_openapi_counts_are_exact_without_new_duplicate_ids() -> None:
    routes = [route for route in working_router.routes if isinstance(route, APIRoute)]
    surface = {
        (method, route.path, route.operation_id) for route in routes for method in route.methods
    }
    assert surface == {
        (
            "GET",
            "/projects/{project_id}/working-composition/history",
            "get_working_composition_history",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/history/undo",
            "undo_working_composition_history",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/history/redo",
            "redo_working_composition_history",
        ),
        (
            "GET",
            "/projects/{project_id}/working-composition",
            "get_working_composition",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/initialize",
            "initialize_working_composition",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/checkout",
            "checkout_working_composition",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/commit",
            "commit_working_composition",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/preview",
            "create_working_composition_preview",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/tracks",
            "create_working_composition_track",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/tracks/reorder",
            "reorder_working_composition_tracks",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/tracks/{track_id}",
            "rename_working_composition_track",
        ),
        (
            "DELETE",
            "/projects/{project_id}/working-composition/tracks/{track_id}",
            "delete_working_composition_track",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/tracks/{track_id}/restore",
            "restore_working_composition_track",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips",
            "create_working_composition_clip",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{clip_id}/copy",
            "copy_working_composition_clip",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/move",
            "move_working_composition_clip",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/gain",
            "update_working_composition_clip_gain",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/tracks/{track_id}/mixer",
            "update_working_composition_track_mixer",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/master-gain",
            "update_working_composition_master_gain",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/fade",
            "update_working_composition_clip_fade",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/loop",
            "update_working_composition_clip_loop",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{clip_id}/loop/restore",
            "restore_working_composition_clip_loop_state",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/trim-start",
            "trim_working_composition_clip_start",
        ),
        (
            "PATCH",
            "/projects/{project_id}/working-composition/clips/{clip_id}/trim-end",
            "trim_working_composition_clip_end",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{clip_id}/split",
            "split_working_composition_clip",
        ),
        (
            "DELETE",
            "/projects/{project_id}/working-composition/clips/{clip_id}",
            "delete_working_composition_clip",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{clip_id}/restore",
            "restore_working_composition_clip",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{original_clip_id}/unsplit",
            "unsplit_working_composition_clip",
        ),
        (
            "POST",
            "/projects/{project_id}/working-composition/clips/{original_clip_id}/resplit",
            "resplit_working_composition_clip",
        ),
    }
    assert len(routes) == 29
    assert len({path for _, path, _ in surface}) == 28
    operation_ids = [operation_id for _, _, operation_id in surface]
    assert len(operation_ids) == len(set(operation_ids)) == 29


def test_track_reorder_is_contiguous_and_empty_track_delete_replays(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    first = _create_track(service, graph, working_id, 0, key="first", name="First")
    second = _create_track(service, graph, working_id, 1, key="second", name="Second")
    reordered = service.reorder_tracks(
        graph.project_id,
        working_composition_id=working_id,
        ordered_track_ids=[second.identities["track_id"], first.identities["track_id"]],
        expected_revision=2,
        effective_owner_id=graph.owner_id,
    )
    assert reordered.completed_revision == 3
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert [track.track_id for track in aggregate.tracks] == [
        second.identities["track_id"],
        first.identities["track_id"],
    ]
    assert [track.track_order for track in aggregate.tracks] == [0, 1]
    deleted = service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=first.identities["track_id"],
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-empty",
    )
    replay = service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=first.identities["track_id"],
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-empty",
    )
    assert deleted.completed_revision == replay.completed_revision == 4
    assert replay.replayed is True


def test_move_trim_start_trim_end_and_delete_increment_exactly_once(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    moved = service.move_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start="1.0000004",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
    )
    trimmed_start = service.trim_clip_start(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start="2",
        source_in="1",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    trimmed_end = service.trim_clip_end(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        source_out="3.5",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
    )
    assert [
        moved.completed_revision,
        trimmed_start.completed_revision,
        trimmed_end.completed_revision,
    ] == [3, 4, 5]
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    clip = aggregate.clips[0]
    assert clip.timeline_start == 2_000_000
    assert clip.source_in == 1_000_000
    assert clip.source_out == 3_500_000


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_project_asset", WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE),
        ("inactive_asset", WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE),
        ("ambiguous", WorkingCompositionErrorCode.SOURCE_ARTIFACT_AMBIGUOUS),
        ("null_duration", WorkingCompositionErrorCode.SOURCE_DURATION_UNAVAILABLE),
    ],
)
def test_clip_source_fail_closed_cases(
    service, session_factory, graph, mutation, expected_code
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    with session_factory.begin() as session:
        version = session.get(AssetVersion, graph.asset_version_id)
        assert version is not None
        asset = session.get(Asset, version.asset_id)
        assert asset is not None
        if mutation == "missing_project_asset":
            project_asset = session.scalar(
                select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
            )
            assert project_asset is not None
            project_asset.deleted_at = datetime.now(UTC)
        elif mutation == "inactive_asset":
            asset.lifecycle_status = "archived"
        elif mutation == "ambiguous":
            session.add(
                Artifact(
                    artifact_id=uuid4(),
                    asset_version_id=graph.asset_version_id,
                    artifact_kind="stem",
                    media_type="audio/flac",
                    size_bytes=2_000,
                    duration_us=10_000_000,
                    checksum_algorithm="sha256",
                    artifact_checksum="b" * 64,
                    producer_type="workspace",
                    retention_status="active",
                )
            )
        else:
            artifact = session.scalar(
                select(Artifact).where(Artifact.asset_version_id == graph.asset_version_id)
            )
            assert artifact is not None
            artifact.duration_us = None
            artifact.media_type = "audio/mpeg"
    with pytest.raises(WorkingCompositionError) as caught:
        _create_clip(service, graph, working_id, track_id, 1)
    assert caught.value.code is expected_code
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert aggregate.working_composition.revision == 1
    assert aggregate.clips == ()


def test_idempotency_in_progress_is_preserved(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    key = "busy-track"
    operation = IdempotencyResultType.TRACK_CREATE
    scope = (
        f"working-composition:{graph.owner_id}:{graph.project_id}:{working_id}:{operation.value}"
    )
    fingerprint = _fingerprint(
        effective_owner_id=graph.owner_id,
        project_id=graph.project_id,
        working_composition_id=working_id,
        operation=operation.value,
        expected_revision=0,
        target_identity=None,
        body={"name": "Busy"},
    )
    with session_factory.begin() as session:
        session.add(
            IdempotencyRecord(
                scope=scope,
                key_hash=IdempotencyRepository.hash_key(key),
                request_fingerprint=fingerprint,
                status="IN_PROGRESS",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    with pytest.raises(IdempotencyInProgressError):
        _create_track(service, graph, working_id, 0, key=key, name="Busy")


def test_create_rolls_back_row_revision_and_claim_when_completion_fails(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("forced completion failure")

    monkeypatch.setattr(IdempotencyRepository, "complete_with_result", fail_completion)
    with pytest.raises(RuntimeError, match="forced completion failure"):
        _create_track(service, graph, working_id, 0, key="failed-track")
    with session_factory() as session:
        working = session.get(WorkingComposition, working_id)
        assert working is not None and working.revision == 0
        assert session.scalar(select(func.count(CompositionTrack.track_id))) == 0
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == 1


def test_concurrent_expected_revision_has_one_success_and_one_conflict(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]

    def rename(name: str):
        try:
            return service.rename_track(
                graph.project_id,
                working_composition_id=working_id,
                track_id=track_id,
                name=name,
                expected_revision=1,
                effective_owner_id=graph.owner_id,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rename, ["Concurrent A", "Concurrent B"]))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert aggregate.working_composition.revision == 2


def test_reorder_forced_failure_restores_original_order(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    first = _create_track(service, graph, working_id, 0, key="reorder-a")
    second = _create_track(service, graph, working_id, 1, key="reorder-b")
    original = CompositionRepository.reorder_active_composition_tracks

    def fail_after_reorder(repository, target_working_id, order):
        original(repository, target_working_id, order)
        raise RuntimeError("forced reorder failure")

    monkeypatch.setattr(
        CompositionRepository,
        "reorder_active_composition_tracks",
        fail_after_reorder,
    )
    with pytest.raises(RuntimeError, match="forced reorder failure"):
        service.reorder_tracks(
            graph.project_id,
            working_composition_id=working_id,
            ordered_track_ids=[
                second.identities["track_id"],
                first.identities["track_id"],
            ],
            expected_revision=2,
            effective_owner_id=graph.owner_id,
        )
    with session_factory() as session:
        tracks = CompositionRepository(session).list_active_composition_tracks(working_id)
        working = session.get(WorkingComposition, working_id)
        assert [track.track_id for track in tracks] == [
            first.identities["track_id"],
            second.identities["track_id"],
        ]
        assert working is not None and working.revision == 2


def test_delete_forced_completion_failure_restores_clip_and_revision(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("forced delete completion failure")

    monkeypatch.setattr(IdempotencyRepository, "complete_with_result", fail_completion)
    with pytest.raises(RuntimeError, match="forced delete completion failure"):
        service.delete_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="failed-delete",
        )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        working = session.get(WorkingComposition, working_id)
        assert clip is not None and clip.deleted_at is None
        assert working is not None and working.revision == 2


def test_checkout_forced_failure_preserves_previous_working_state(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    old_track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    old_clip_id = _create_clip(service, graph, working_id, old_track_id, 1).identities["clip_id"]
    snapshot_id = uuid4()
    snapshot_track_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            CompositionSnapshot(
                composition_snapshot_id=snapshot_id,
                project_id=graph.project_id,
                snapshot_version=1,
                mix_settings_snapshot={},
                provider_versions={},
                model_manifest_ids={},
                created_by=graph.owner_id,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshotTrack(
                snapshot_track_id=snapshot_track_id,
                composition_snapshot_id=snapshot_id,
                canonical_track_id=uuid4(),
                track_type="audio",
                name="Replacement",
                track_order=0,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshotClip(
                composition_snapshot_id=snapshot_id,
                snapshot_track_id=snapshot_track_id,
                canonical_clip_id=uuid4(),
                source_asset_version_id=graph.asset_version_id,
                timeline_start=0,
                source_in=0,
                source_out=1_000_000,
                source_duration=10_000_000,
            )
        )

    def fail_add(*_args, **_kwargs):
        raise RuntimeError("forced checkout failure")

    monkeypatch.setattr(CompositionRepository, "add_composition_clip", fail_add)
    with pytest.raises(RuntimeError, match="forced checkout failure"):
        service.checkout(
            graph.project_id,
            working_composition_id=working_id,
            composition_snapshot_id=snapshot_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="failed-checkout",
        )
    with session_factory() as session:
        old_track = session.get(CompositionTrack, old_track_id)
        old_clip = session.get(CompositionClip, old_clip_id)
        working = session.get(WorkingComposition, working_id)
        assert old_track is not None and old_track.deleted_at is None
        assert old_clip is not None and old_clip.deleted_at is None
        assert working is not None and working.revision == 2
        assert working.base_composition_snapshot_id is None
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == "WORKING_COMPOSITION_CHECKOUT"
                )
            )
            == 0
        )


def test_checkout_rejects_cross_project_snapshot_without_partial_state(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    other_project_id = uuid4()
    other_snapshot_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            MusicProject(
                project_id=other_project_id,
                workspace_id=graph.workspace_id,
                title="Other Project",
                lifecycle_status="active",
                created_by=graph.owner_id,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshot(
                composition_snapshot_id=other_snapshot_id,
                project_id=other_project_id,
                snapshot_version=1,
                mix_settings_snapshot={},
                provider_versions={},
                model_manifest_ids={},
                created_by=graph.owner_id,
            )
        )

    with pytest.raises(ResourceNotFoundError):
        service.checkout(
            graph.project_id,
            working_composition_id=working_id,
            composition_snapshot_id=other_snapshot_id,
            expected_revision=0,
            effective_owner_id=graph.owner_id,
            idempotency_key="cross-project-checkout",
        )
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert aggregate.working_composition.revision == 0
    assert aggregate.working_composition.base_composition_snapshot_id is None
    with session_factory() as session:
        checkout_results = session.scalar(
            select(func.count(IdempotencyRecord.id)).where(
                IdempotencyRecord.result_type
                == IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT.value
            )
        )
        assert checkout_results == 0


def test_cross_track_overlap_is_allowed_and_exact_asset_version_is_persisted(
    service, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    first_track = _create_track(service, graph, working_id, 0, key="cross-track-first").identities[
        "track_id"
    ]
    second_track = _create_track(
        service, graph, working_id, 1, key="cross-track-second"
    ).identities["track_id"]
    first_clip = _create_clip(
        service,
        graph,
        working_id,
        first_track,
        2,
        key="cross-track-clip-a",
    )
    second_clip = _create_clip(
        service,
        graph,
        working_id,
        second_track,
        3,
        key="cross-track-clip-b",
    )

    assert first_clip.completed_revision == 3
    assert second_clip.completed_revision == 4
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert len(aggregate.clips) == 2
    assert {clip.source_asset_version_id for clip in aggregate.clips} == {graph.asset_version_id}


def test_query_plans_use_existing_working_source_and_snapshot_indexes(
    session_factory, graph
) -> None:
    queries = {
        "working": (
            (
                "SELECT working_composition_id FROM working_compositions "
                "WHERE project_id = :project_id"
            ),
            "sqlite_autoindex_working_compositions",
        ),
        "tracks": (
            (
                "SELECT track_id FROM composition_tracks "
                "WHERE working_composition_id = :working_id AND deleted_at IS NULL "
                "ORDER BY track_order, track_id"
            ),
            "ix_composition_tracks_active_order",
        ),
        "clips": (
            (
                "SELECT clip_id FROM composition_clips "
                "WHERE track_id = :track_id AND deleted_at IS NULL "
                "ORDER BY timeline_start, clip_id"
            ),
            "ix_composition_clips_active_timeline",
        ),
        "clip_count": (
            (
                "SELECT count(clip_id) FROM composition_clips "
                "WHERE working_composition_id = :working_id "
                "AND track_id = :track_id AND deleted_at IS NULL"
            ),
            "ix_composition_clips_active_timeline",
        ),
        "artifacts": (
            (
                "SELECT artifact_id FROM artifacts "
                "WHERE asset_version_id = :version_id "
                "ORDER BY created_at, artifact_id"
            ),
            "ix_artifacts_version_created",
        ),
        "snapshot_tracks": (
            (
                "SELECT snapshot_track_id FROM composition_snapshot_tracks "
                "WHERE composition_snapshot_id = :snapshot_id "
                "ORDER BY track_order, snapshot_track_id"
            ),
            "ix_composition_snapshot_tracks_order",
        ),
        "snapshot_clips": (
            (
                "SELECT snapshot_clip_id FROM composition_snapshot_clips "
                "WHERE snapshot_track_id = :snapshot_track_id "
                "ORDER BY timeline_start, snapshot_clip_id"
            ),
            "ix_composition_snapshot_clips_timeline",
        ),
        "snapshot_checkout_clips": (
            (
                "SELECT snapshot_clip_id FROM composition_snapshot_clips "
                "WHERE composition_snapshot_id = :snapshot_id "
                "ORDER BY snapshot_track_id, timeline_start, snapshot_clip_id"
            ),
            "sqlite_autoindex_composition_snapshot_clips",
        ),
    }
    parameters = {
        "project_id": str(graph.project_id).replace("-", ""),
        "working_id": str(uuid4()).replace("-", ""),
        "track_id": str(uuid4()).replace("-", ""),
        "version_id": str(graph.asset_version_id).replace("-", ""),
        "snapshot_id": str(uuid4()).replace("-", ""),
        "snapshot_track_id": str(uuid4()).replace("-", ""),
    }
    with session_factory() as session:
        for query, expected_index in queries.values():
            plan = " ".join(
                str(row[-1])
                for row in session.execute(text(f"EXPLAIN QUERY PLAN {query}"), parameters)
            )
            assert expected_index in plan


def test_track_restore_reindexes_replays_and_preserves_identity(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    tracks = [
        _create_track(
            service,
            graph,
            working_id,
            revision,
            key=f"track-{revision}",
            name=f"Track {revision}",
        )
        for revision in range(3)
    ]
    target_id = tracks[1].identities["track_id"]
    service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-middle",
    )
    restored = service.restore_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        target_track_order=1,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-middle",
    )
    replay = service.restore_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        target_track_order=1,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-middle",
    )
    assert restored.identities["track_id"] == target_id
    assert replay.replayed is True
    assert replay.completed_revision == restored.completed_revision == 5
    with pytest.raises(IdempotencyConflictError):
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=target_id,
            target_track_order=0,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-middle",
        )
    with session_factory() as session:
        ordered = CompositionRepository(session).list_active_composition_tracks(working_id)
        assert [track.track_id for track in ordered] == [
            tracks[0].identities["track_id"],
            target_id,
            tracks[2].identities["track_id"],
        ]
        assert [track.track_order for track in ordered] == [0, 1, 2]
    with pytest.raises(WorkingCompositionError) as active:
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=target_id,
            target_track_order=1,
            expected_revision=5,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-active",
        )
    assert active.value.code is WorkingCompositionErrorCode.TRACK_ALREADY_ACTIVE
    service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-again",
    )
    with pytest.raises(WorkingCompositionError) as invalid:
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=target_id,
            target_track_order=3,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-invalid",
        )
    assert invalid.value.code is WorkingCompositionErrorCode.TRACK_RESTORE_ORDER_INVALID
    with pytest.raises(WorkingCompositionError) as negative:
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=target_id,
            target_track_order=-1,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-negative",
        )
    assert negative.value.code is WorkingCompositionErrorCode.TRACK_RESTORE_ORDER_INVALID
    with pytest.raises(WorkingCompositionError) as hidden:
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=uuid4(),
            target_track_order=0,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-hidden",
        )
    assert hidden.value.code is WorkingCompositionErrorCode.TRACK_NOT_FOUND
    repeated = service.restore_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        target_track_order=0,
        expected_revision=6,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-again",
    )
    assert repeated.identities["track_id"] == target_id
    service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        expected_revision=7,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-before-append",
    )
    appended = service.restore_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=target_id,
        target_track_order=2,
        expected_revision=8,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-append",
    )
    assert appended.identities["track_id"] == target_id
    with session_factory() as session:
        ordered = CompositionRepository(session).list_active_composition_tracks(working_id)
        assert [track.track_order for track in ordered] == [0, 1, 2]
        assert ordered[-1].track_id == target_id


def test_track_restore_forced_failure_rolls_back_and_concurrent_cas(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        expected_revision=1,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete",
    )
    original = CompositionRepository.restore_composition_track

    def fail_after_restore(repository, track, *, target_track_order):
        original(repository, track, target_track_order=target_track_order)
        raise RuntimeError("forced track restore failure")

    monkeypatch.setattr(CompositionRepository, "restore_composition_track", fail_after_restore)
    with pytest.raises(RuntimeError, match="forced track restore failure"):
        service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=track_id,
            target_track_order=0,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="failed-restore",
        )
    monkeypatch.setattr(CompositionRepository, "restore_composition_track", original)
    with session_factory() as session:
        assert session.get(CompositionTrack, track_id).deleted_at is not None
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == "TRACK_RESTORE"
                )
            )
            == 0
        )

    def restore(key: str):
        return service.restore_track(
            graph.project_id,
            working_composition_id=working_id,
            track_id=track_id,
            target_track_order=0,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(restore, key) for key in ("race-a", "race-b")]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except WorkingCompositionError as error:
            outcomes.append(error.code)
    assert sum(isinstance(item, WorkingMutationResult) for item in outcomes) == 1
    assert outcomes.count(WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT) == 1


def test_clip_restore_preserves_frozen_geometry_replays_and_repeats(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with session_factory() as session:
        before = session.get(CompositionClip, clip_id)
        frozen = (
            before.source_asset_version_id,
            before.timeline_start,
            before.source_in,
            before.source_out,
            before.source_duration,
            before.split_from_clip_id,
        )
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="clip-delete",
    )
    with pytest.raises(WorkingCompositionError) as stale:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="clip-restore-stale",
        )
    assert stale.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with pytest.raises(WorkingCompositionError) as hidden:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=uuid4(),
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="clip-restore-hidden",
        )
    assert hidden.value.code is WorkingCompositionErrorCode.CLIP_NOT_FOUND
    restored = service.restore_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="clip-restore",
    )
    replay = service.restore_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="clip-restore",
    )
    assert restored.identities["clip_id"] == replay.identities["clip_id"] == clip_id
    assert replay.replayed is True
    assert replay.completed_revision == restored.completed_revision == 4
    with session_factory() as session:
        current = session.get(CompositionClip, clip_id)
        assert current.deleted_at is None
        assert (
            current.source_asset_version_id,
            current.timeline_start,
            current.source_in,
            current.source_out,
            current.source_duration,
            current.split_from_clip_id,
        ) == frozen
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="clip-delete-again",
    )
    repeated = service.restore_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="clip-restore-again",
    )
    assert repeated.identities["clip_id"] == clip_id


def test_clip_restore_revalidates_source_and_overlap(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-source",
    )
    with session_factory.begin() as session:
        link = session.scalar(
            select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
        )
        link.deleted_at = datetime.now(UTC)
    with pytest.raises(WorkingCompositionError) as unavailable:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-revoked",
        )
    assert unavailable.value.code is WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE
    with session_factory.begin() as session:
        link = session.scalar(
            select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
        )
        link.deleted_at = None
    _create_clip(
        service,
        graph,
        working_id,
        track_id,
        3,
        key="replacement",
    )
    with pytest.raises(WorkingCompositionError) as overlap:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-overlap",
        )
    assert overlap.value.code is WorkingCompositionErrorCode.CLIP_OVERLAP
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).deleted_at is not None
        assert session.get(WorkingComposition, working_id).revision == 4


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("inactive_asset", WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE),
        ("ambiguous", WorkingCompositionErrorCode.SOURCE_ARTIFACT_AMBIGUOUS),
        ("null_duration", WorkingCompositionErrorCode.SOURCE_DURATION_UNAVAILABLE),
    ],
)
def test_clip_restore_current_eligibility_fail_closed(
    service, session_factory, graph, mutation, expected_code
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-before-eligibility",
    )
    with session_factory.begin() as session:
        version = session.get(AssetVersion, graph.asset_version_id)
        asset = session.get(Asset, version.asset_id)
        if mutation == "inactive_asset":
            asset.lifecycle_status = "archived"
        elif mutation == "ambiguous":
            session.add(
                Artifact(
                    artifact_id=uuid4(),
                    asset_version_id=graph.asset_version_id,
                    artifact_kind="stem",
                    media_type="audio/flac",
                    size_bytes=2_000,
                    duration_us=10_000_000,
                    checksum_algorithm="sha256",
                    artifact_checksum="b" * 64,
                    producer_type="workspace",
                    retention_status="active",
                )
            )
        else:
            artifact = session.scalar(
                select(Artifact).where(Artifact.asset_version_id == graph.asset_version_id)
            )
            artifact.duration_us = None
            artifact.media_type = "audio/mpeg"
    with pytest.raises(WorkingCompositionError) as caught:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"restore-{mutation}",
        )
    assert caught.value.code is expected_code
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).deleted_at is not None
        assert session.get(WorkingComposition, working_id).revision == 3


def test_clip_restore_rejects_inactive_parent_and_forced_failure_rolls_back(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-clip-parent",
    )
    service.delete_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-parent",
    )
    with pytest.raises(WorkingCompositionError) as inactive:
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-inactive-parent",
        )
    assert inactive.value.code is WorkingCompositionErrorCode.TRACK_NOT_FOUND
    service.restore_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        target_track_order=0,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-parent",
    )
    original_restore = CompositionRepository.restore_composition_clip

    def fail_after_restore(repository, clip):
        original_restore(repository, clip)
        raise RuntimeError("forced clip restore failure")

    monkeypatch.setattr(CompositionRepository, "restore_composition_clip", fail_after_restore)
    with pytest.raises(RuntimeError, match="forced clip restore failure"):
        service.restore_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            expected_revision=5,
            effective_owner_id=graph.owner_id,
            idempotency_key="failed-clip-restore",
        )
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).deleted_at is not None
        assert session.get(WorkingComposition, working_id).revision == 5


def test_unsplit_resplit_replay_and_repeated_toggle_keep_all_ids(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-toggle",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    unsplit = service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="unsplit-1",
    )
    replay = service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="unsplit-1",
    )
    assert replay.replayed is True
    assert replay.completed_revision == unsplit.completed_revision == 4
    assert replay.identities == split.identities
    revision = 4
    for index in range(2):
        resplit = service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=revision,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"resplit-{index}",
        )
        revision += 1
        assert resplit.identities == split.identities
        resplit_replay = service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=revision - 1,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"resplit-{index}",
        )
        assert resplit_replay.replayed is True
        assert resplit_replay.completed_revision == resplit.completed_revision
        assert resplit_replay.identities == split.identities
        with pytest.raises(WorkingCompositionError) as wrong_state:
            service.resplit_clip(
                graph.project_id,
                working_composition_id=working_id,
                original_clip_id=original_id,
                left_clip_id=left_id,
                right_clip_id=right_id,
                expected_revision=revision,
                effective_owner_id=graph.owner_id,
                idempotency_key=f"resplit-wrong-state-{index}",
            )
        assert wrong_state.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
        unsplit = service.unsplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=revision,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"unsplit-{index + 2}",
        )
        revision += 1
        assert unsplit.identities == split.identities
    with session_factory() as session:
        clips = list(
            session.scalars(
                select(CompositionClip).where(CompositionClip.working_composition_id == working_id)
            )
        )
        assert {clip.clip_id for clip in clips} == {original_id, left_id, right_id}
        assert session.get(CompositionClip, original_id).deleted_at is None
        assert session.get(CompositionClip, left_id).deleted_at is not None
        assert session.get(CompositionClip, right_id).deleted_at is not None


def test_concurrent_unsplit_cas_allows_exactly_one_toggle(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-concurrent-toggle",
    )

    def unsplit(key: str):
        try:
            return service.unsplit_clip(
                graph.project_id,
                working_composition_id=working_id,
                original_clip_id=original_id,
                left_clip_id=split.identities["left_clip_id"],
                right_clip_id=split.identities["right_clip_id"],
                expected_revision=3,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(unsplit, ("unsplit-race-a", "unsplit-race-b")))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    aggregate = service.get_working_composition(graph.project_id, effective_owner_id=graph.owner_id)
    assert aggregate.working_composition.revision == 4
    assert [clip.clip_id for clip in aggregate.clips] == [original_id]


def test_unsplit_rejects_changed_geometry_and_forced_failure_is_atomic(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-structural",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    service.move_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=right_id,
        timeline_start="2.5",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    with pytest.raises(WorkingCompositionError) as structural:
        service.unsplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="unsplit-invalid",
        )
    assert structural.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    service.move_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=right_id,
        timeline_start="2",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
    )
    original_tombstone = CompositionRepository.tombstone_composition_clip

    def fail_after_left_tombstone(repository, clip):
        original_tombstone(repository, clip)
        if clip.clip_id == left_id:
            raise RuntimeError("forced unsplit failure")

    monkeypatch.setattr(
        CompositionRepository,
        "tombstone_composition_clip",
        fail_after_left_tombstone,
    )
    with pytest.raises(RuntimeError, match="forced unsplit failure"):
        service.unsplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=5,
            effective_owner_id=graph.owner_id,
            idempotency_key="unsplit-failed",
        )
    with session_factory() as session:
        assert session.get(CompositionClip, original_id).deleted_at is not None
        assert session.get(CompositionClip, left_id).deleted_at is None
        assert session.get(CompositionClip, right_id).deleted_at is None
        assert session.get(WorkingComposition, working_id).revision == 5
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == "CLIP_UNSPLIT"
                )
            )
            == 0
        )


def test_unsplit_rejects_each_persisted_structural_drift_without_partial_state(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-drift-matrix",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    other_version_id = uuid4()
    with session_factory.begin() as session:
        base_version = session.get(AssetVersion, graph.asset_version_id)
        session.add(
            AssetVersion(
                asset_version_id=other_version_id,
                asset_id=base_version.asset_id,
                version_number=2,
                version_origin="generated",
                settings_snapshot={},
                created_by=graph.owner_id,
            )
        )

    for drift in (
        "left_moved",
        "right_moved",
        "left_trimmed",
        "right_trimmed",
        "source_mismatch",
        "wrong_lineage",
        "original_active",
        "child_tombstoned",
    ):
        with session_factory.begin() as session:
            original = session.get(CompositionClip, original_id)
            left = session.get(CompositionClip, left_id)
            right = session.get(CompositionClip, right_id)
            if drift == "left_moved":
                left.timeline_start += 1
            elif drift == "right_moved":
                right.timeline_start += 1
            elif drift == "left_trimmed":
                left.source_out -= 1
                left.timeline_duration -= 1
            elif drift == "right_trimmed":
                right.source_in += 1
                right.timeline_duration -= 1
            elif drift == "source_mismatch":
                left.source_asset_version_id = other_version_id
            elif drift == "wrong_lineage":
                left.split_from_clip_id = right_id
            elif drift == "original_active":
                original.deleted_at = None
            else:
                right.deleted_at = datetime.now(UTC)

        with pytest.raises(WorkingCompositionError) as structural:
            service.unsplit_clip(
                graph.project_id,
                working_composition_id=working_id,
                original_clip_id=original_id,
                left_clip_id=left_id,
                right_clip_id=right_id,
                expected_revision=3,
                effective_owner_id=graph.owner_id,
                idempotency_key=f"unsplit-drift-{drift}",
            )
        assert structural.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
        with session_factory() as session:
            assert session.get(WorkingComposition, working_id).revision == 3
            assert (
                session.scalar(
                    select(func.count(IdempotencyRecord.id)).where(
                        IdempotencyRecord.result_type == "CLIP_UNSPLIT"
                    )
                )
                == 0
            )

        with session_factory.begin() as session:
            original = session.get(CompositionClip, original_id)
            left = session.get(CompositionClip, left_id)
            right = session.get(CompositionClip, right_id)
            original.deleted_at = datetime.now(UTC)
            left.deleted_at = None
            left.timeline_start = 0
            left.source_in = 0
            left.source_out = 2_000_000
            left.timeline_duration = 2_000_000
            left.loop_phase = 0
            left.source_asset_version_id = graph.asset_version_id
            left.split_from_clip_id = original_id
            right.deleted_at = None
            right.timeline_start = 2_000_000
            right.source_in = 2_000_000
            right.source_out = 4_000_000
            right.timeline_duration = 2_000_000
            right.loop_phase = 0


def test_resplit_rejects_changed_geometry_and_revoked_source(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-resplit-conflict",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="unsplit-resplit-conflict",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, left_id).timeline_start = 1
    with pytest.raises(WorkingCompositionError) as structural:
        service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="resplit-geometry-conflict",
        )
    assert structural.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    with session_factory.begin() as session:
        session.get(CompositionClip, left_id).timeline_start = 0
        link = session.scalar(
            select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
        )
        link.deleted_at = datetime.now(UTC)
    with pytest.raises(WorkingCompositionError) as unavailable:
        service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="resplit-revoked",
        )
    assert unavailable.value.code is WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE
    with session_factory() as session:
        assert session.get(CompositionClip, original_id).deleted_at is None
        assert session.get(CompositionClip, left_id).deleted_at is not None
        assert session.get(CompositionClip, right_id).deleted_at is not None
        assert session.get(WorkingComposition, working_id).revision == 4


def test_clip_copy_preserves_source_geometry_supports_explicit_destinations_and_replay(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    source_track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    target_track_id = _create_track(
        service, graph, working_id, 1, key="copy-target-track", name="Target"
    ).identities["track_id"]
    source_clip_id = _create_clip(service, graph, working_id, source_track_id, 2).identities[
        "clip_id"
    ]

    first = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=source_clip_id,
        target_track_id=source_track_id,
        target_timeline_start="4",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-adjacent",
    )
    replay = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=source_clip_id,
        target_track_id=source_track_id,
        target_timeline_start="4",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-adjacent",
    )
    assert first.identities["clip_id"] != source_clip_id
    assert replay.identities == first.identities
    assert replay.completed_revision == first.completed_revision == 4
    assert replay.replayed is True

    cross_track = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=source_clip_id,
        target_track_id=target_track_id,
        target_timeline_start="0",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-cross-track",
    )
    assert cross_track.identities["clip_id"] not in {
        source_clip_id,
        first.identities["clip_id"],
    }

    with pytest.raises(IdempotencyConflictError):
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=target_track_id,
            target_timeline_start="5",
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-cross-track",
        )
    with pytest.raises(WorkingCompositionError) as overlap:
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=source_track_id,
            target_timeline_start="3",
            expected_revision=5,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-overlap",
        )
    assert overlap.value.code is WorkingCompositionErrorCode.CLIP_OVERLAP

    with session_factory() as session:
        source = session.get(CompositionClip, source_clip_id)
        adjacent = session.get(CompositionClip, first.identities["clip_id"])
        cross = session.get(CompositionClip, cross_track.identities["clip_id"])
        frozen = (
            source.source_asset_version_id,
            source.source_in,
            source.source_out,
            source.source_duration,
        )
        assert (
            adjacent.source_asset_version_id,
            adjacent.source_in,
            adjacent.source_out,
            adjacent.source_duration,
        ) == frozen
        assert (
            cross.source_asset_version_id,
            cross.source_in,
            cross.source_out,
            cross.source_duration,
        ) == frozen
        assert source.track_id == source_track_id and source.timeline_start == 0
        assert adjacent.track_id == source_track_id and adjacent.timeline_start == 4_000_000
        assert cross.track_id == target_track_id and cross.timeline_start == 0
        assert adjacent.split_from_clip_id is None and cross.split_from_clip_id is None
        assert session.get(WorkingComposition, working_id).revision == 5
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_COPY.value
                )
            )
            == 2
        )


def test_clip_copy_rejects_tombstones_invalid_target_stale_revision_and_revoked_source(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    source_clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    with pytest.raises(WorkingCompositionError) as stale:
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=track_id,
            target_timeline_start="4",
            expected_revision=1,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-stale",
        )
    assert stale.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with pytest.raises(WorkingCompositionError) as target:
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=uuid4(),
            target_timeline_start="4",
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-invalid-target",
        )
    assert target.value.code is WorkingCompositionErrorCode.TRACK_NOT_FOUND

    with session_factory.begin() as session:
        link = session.scalar(
            select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
        )
        link.deleted_at = datetime.now(UTC)
    with pytest.raises(WorkingCompositionError) as unavailable:
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=track_id,
            target_timeline_start="4",
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-revoked",
        )
    assert unavailable.value.code is WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE
    with session_factory.begin() as session:
        link = session.scalar(
            select(ProjectAsset).where(ProjectAsset.project_id == graph.project_id)
        )
        link.deleted_at = None
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=source_clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-source-delete",
    )
    with pytest.raises(WorkingCompositionError) as tombstone:
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=track_id,
            target_timeline_start="4",
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="copy-tombstone",
        )
    assert tombstone.value.code is WorkingCompositionErrorCode.CLIP_NOT_FOUND
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 1
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_COPY.value
                )
            )
            == 0
        )


@pytest.mark.parametrize("failure_point", ["eligibility", "insert", "revision", "completion"])
def test_clip_copy_forced_failures_roll_back_all_rows(
    service, session_factory, graph, monkeypatch, failure_point
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    source_clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    def fail_after(method):
        def failing(*args, **kwargs):
            method(*args, **kwargs)
            raise RuntimeError(f"forced copy failure after {failure_point}")

        return failing

    if failure_point == "eligibility":
        monkeypatch.setattr(
            service, "_validate_restore_source", fail_after(service._validate_restore_source)
        )
    elif failure_point == "insert":
        monkeypatch.setattr(
            CompositionRepository,
            "add_composition_clip",
            fail_after(CompositionRepository.add_composition_clip),
        )
    elif failure_point == "revision":
        monkeypatch.setattr(service, "_increment_revision", fail_after(service._increment_revision))
    else:
        monkeypatch.setattr(
            working_composition_module,
            "_complete_result",
            fail_after(working_composition_module._complete_result),
        )

    with pytest.raises(RuntimeError, match="forced copy failure"):
        service.copy_clip(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=source_clip_id,
            target_track_id=track_id,
            target_timeline_start="4",
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"copy-failed-{failure_point}",
        )
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 1
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_COPY.value
                )
            )
            == 0
        )


def test_concurrent_clip_copy_expected_revision_creates_exactly_one_clip(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    source_clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    def copy(key: str):
        try:
            return service.copy_clip(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=source_clip_id,
                target_track_id=track_id,
                target_timeline_start="4",
                expected_revision=2,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(copy, ["copy-race-a", "copy-race-b"]))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionClip.clip_id))) == 2
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_COPY.value
                )
            )
            == 1
        )


def test_clip_copy_product_api_is_strict_and_returns_new_identity(
    working_client, service, graph
) -> None:
    working_id = _initialize(service, graph, "copy-api-init").identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    source_clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    path = f"/api/v1/projects/{graph.project_id}/working-composition/clips/{source_clip_id}/copy"
    body = {
        "working_composition_id": str(working_id),
        "expected_revision": 2,
        "target_track_id": str(track_id),
        "target_timeline_start": "4",
    }
    response = working_client.post(path, json=body, headers={"Idempotency-Key": "copy-api"})
    assert response.status_code == 201
    assert response.json()["data"] == {
        "clip_id": response.json()["data"]["clip_id"],
        "completed_revision": 3,
        "replayed": False,
    }
    assert response.json()["data"]["clip_id"] != str(source_clip_id)
    forbidden = working_client.post(
        path,
        json={
            **body,
            "expected_revision": 3,
            "source_asset_version_id": str(graph.asset_version_id),
        },
        headers={"Idempotency-Key": "copy-api-forbidden"},
    )
    assert forbidden.status_code == 422


def test_split_child_copy_drops_lineage_and_commit_freezes_copied_identity(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-child-split",
    )
    child_id = split.identities["left_clip_id"]
    copied = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=child_id,
        target_track_id=track_id,
        target_timeline_start="4",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="copy-split-child",
    )
    copied_id = copied.identities["clip_id"]
    committed = service.commit(
        graph.project_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-copied-child",
    )
    with session_factory() as session:
        child = session.get(CompositionClip, child_id)
        copied_clip = session.get(CompositionClip, copied_id)
        assert child.split_from_clip_id == original_id
        assert copied_clip.split_from_clip_id is None
        assert copied_clip.source_asset_version_id == child.source_asset_version_id
        assert (copied_clip.source_in, copied_clip.source_out, copied_clip.source_duration) == (
            child.source_in,
            child.source_out,
            child.source_duration,
        )
        snapshot_clip = session.scalar(
            select(CompositionSnapshotClip).where(
                CompositionSnapshotClip.composition_snapshot_id
                == committed.identities["composition_snapshot_id"],
                CompositionSnapshotClip.canonical_clip_id == copied_id,
            )
        )
        assert snapshot_clip is not None
        assert snapshot_clip.canonical_clip_id == copied_id
        assert snapshot_clip.source_asset_version_id == copied_clip.source_asset_version_id
        assert (
            snapshot_clip.source_in,
            snapshot_clip.source_out,
            snapshot_clip.source_duration,
            snapshot_clip.split_from_clip_id,
        ) == (
            copied_clip.source_in,
            copied_clip.source_out,
            copied_clip.source_duration,
            None,
        )


def _assert_empty_commit_has_no_side_effects(
    service, session_factory, graph, working_id: UUID, revision: int, key: str
) -> None:
    with session_factory() as session:
        completion_count = session.scalar(select(func.count(IdempotencyRecord.id)))
    with pytest.raises(WorkingCompositionError) as caught:
        service.commit(
            graph.project_id,
            expected_revision=revision,
            effective_owner_id=graph.owner_id,
            idempotency_key=key,
        )
    assert caught.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_EMPTY
    with session_factory() as session:
        working = session.get(WorkingComposition, working_id)
        repository = CompositionRepository(session)
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 0
        assert session.scalar(select(func.count(CompositionSnapshotTrack.snapshot_track_id))) == 0
        assert session.scalar(select(func.count(CompositionSnapshotClip.snapshot_clip_id))) == 0
        assert repository.get_project_selection(graph.project_id) is None
        assert working is not None
        assert working.base_composition_snapshot_id is None
        assert working.revision == revision
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == completion_count


def test_commit_rejects_zero_tracks_and_zero_clips_without_side_effects(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    _assert_empty_commit_has_no_side_effects(
        service, session_factory, graph, working_id, 0, "empty-no-track"
    )


def test_commit_rejects_track_without_clips_without_side_effects(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    _create_track(service, graph, working_id, 0)
    _assert_empty_commit_has_no_side_effects(
        service, session_factory, graph, working_id, 1, "empty-track"
    )


def test_commit_rejects_tombstoned_clips_without_side_effects(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="delete-only-clip",
    )
    _assert_empty_commit_has_no_side_effects(
        service, session_factory, graph, working_id, 3, "empty-tombstone"
    )


def test_commit_freezes_canonical_arrangement_selection_base_and_revision(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    first_track = _create_track(
        service, graph, working_id, 0, key="commit-track-a", name="First"
    ).identities["track_id"]
    second_track = _create_track(
        service, graph, working_id, 1, key="commit-track-b", name="Second"
    ).identities["track_id"]
    second_clip = _create_clip(
        service,
        graph,
        working_id,
        second_track,
        2,
        key="commit-clip-b",
        timeline_start="2",
    ).identities["clip_id"]
    first_clip = _create_clip(
        service, graph, working_id, first_track, 3, key="commit-clip-a"
    ).identities["clip_id"]
    with session_factory.begin() as session:
        session.get(WorkingComposition, working_id).mix_settings = {"master_gain": -3}

    result = service.commit(
        graph.project_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-success",
    )
    snapshot_id = result.identities["composition_snapshot_id"]
    assert result.identities["working_composition_id"] == working_id
    assert result.completed_revision == 5
    assert result.replayed is False

    with session_factory() as session:
        repository = CompositionRepository(session)
        snapshot = session.get(CompositionSnapshot, snapshot_id)
        frozen_tracks = repository.list_snapshot_tracks(snapshot_id)
        frozen_clips = repository.list_snapshot_clips_for_snapshot(snapshot_id)
        selection = repository.get_project_selection(graph.project_id)
        working = session.get(WorkingComposition, working_id)
        assert snapshot is not None
        assert snapshot.snapshot_version == 1
        assert snapshot.mix_settings_snapshot == {"master_gain": -3}
        assert snapshot.processing_chain_id is None
        assert snapshot.provider_versions == {}
        assert snapshot.model_manifest_ids == {}
        assert [track.canonical_track_id for track in frozen_tracks] == [
            first_track,
            second_track,
        ]
        assert [track.track_order for track in frozen_tracks] == [0, 1]
        assert [clip.canonical_clip_id for clip in frozen_clips] == [
            first_clip,
            second_clip,
        ]
        assert {clip.source_asset_version_id for clip in frozen_clips} == {graph.asset_version_id}
        assert selection is not None
        assert selection.selected_composition_snapshot_id == snapshot_id
        assert working is not None
        assert working.base_composition_snapshot_id == snapshot_id
        assert working.revision == 5
        assert len(repository.list_active_composition_tracks(working_id)) == 2
        assert len(repository.list_working_composition_clips(working_id)) == 2
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.result_type == IdempotencyResultType.COMPOSITION_COMMIT.value
            )
        )
        assert record is not None
        assert record.result_payload == {"composition_snapshot_id": str(snapshot_id)}
        assert record.completed_revision == 5


def test_commit_replays_first_snapshot_and_rejects_key_reuse_or_stale_revision(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    _create_clip(service, graph, working_id, track_id, 1)
    first = service.commit(
        graph.project_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-replay",
    )
    replay = service.commit(
        graph.project_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-replay",
    )
    assert replay.replayed is True
    assert replay.identities == first.identities
    assert replay.completed_revision == first.completed_revision == 3

    with pytest.raises(IdempotencyConflictError):
        service.commit(
            graph.project_id,
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="commit-replay",
        )
    with pytest.raises(WorkingCompositionError) as stale:
        service.commit(
            graph.project_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="commit-stale",
        )
    assert stale.value.code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 1
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.COMPOSITION_COMMIT.value
                )
            )
            == 1
        )


def test_commit_history_is_immutable_and_does_not_copy_media_or_preview_state(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    first = service.commit(
        graph.project_id,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-immutable-first",
    )
    first_snapshot_id = first.identities["composition_snapshot_id"]

    service.move_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start="5",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    second = service.commit(
        graph.project_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="commit-immutable-second",
    )

    with session_factory() as session:
        repository = CompositionRepository(session)
        history = repository.list_project_snapshots(graph.project_id)
        first_clips = repository.list_snapshot_clips_for_snapshot(first_snapshot_id)
        second_clips = repository.list_snapshot_clips_for_snapshot(
            second.identities["composition_snapshot_id"]
        )
        assert [snapshot.composition_snapshot_id for snapshot in history] == [
            second.identities["composition_snapshot_id"],
            first_snapshot_id,
        ]
        assert [snapshot.snapshot_version for snapshot in history] == [2, 1]
        assert [clip.timeline_start for clip in first_clips] == [0]
        assert [clip.timeline_start for clip in second_clips] == [5_000_000]
        assert session.scalar(select(func.count(AssetVersion.asset_version_id))) == 1
        assert session.scalar(select(func.count(Artifact.artifact_id))) == 1
        assert session.scalar(select(func.count(WorkingPreviewRender.preview_render_id))) == 0
        assert all(snapshot.processing_chain_id is None for snapshot in history)


@pytest.mark.parametrize(
    "failure_point",
    [
        "revision",
        "snapshot",
        "snapshot_track",
        "snapshot_clip",
        "selection",
        "base",
        "completion",
    ],
)
def test_commit_forced_failures_roll_back_every_transaction_stage(
    service, session_factory, graph, monkeypatch, failure_point
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    _create_clip(service, graph, working_id, track_id, 1)

    def fail_after(method):
        def failing(*args, **kwargs):
            method(*args, **kwargs)
            raise RuntimeError(f"forced commit failure after {failure_point}")

        return failing

    if failure_point == "revision":
        monkeypatch.setattr(
            service,
            "_increment_revision",
            fail_after(service._increment_revision),
        )
    elif failure_point == "snapshot":
        monkeypatch.setattr(
            CompositionRepository,
            "add_snapshot",
            fail_after(CompositionRepository.add_snapshot),
        )
    elif failure_point == "snapshot_track":
        monkeypatch.setattr(
            CompositionRepository,
            "add_snapshot_track",
            fail_after(CompositionRepository.add_snapshot_track),
        )
    elif failure_point == "snapshot_clip":
        monkeypatch.setattr(
            CompositionRepository,
            "add_snapshot_clip",
            fail_after(CompositionRepository.add_snapshot_clip),
        )
    elif failure_point == "selection":
        monkeypatch.setattr(
            CompositionRepository,
            "set_project_selection",
            fail_after(CompositionRepository.set_project_selection),
        )
    elif failure_point == "base":
        monkeypatch.setattr(
            CompositionRepository,
            "flush",
            fail_after(CompositionRepository.flush),
        )
    else:
        monkeypatch.setattr(
            working_composition_module,
            "_complete_result",
            fail_after(working_composition_module._complete_result),
        )

    with pytest.raises(RuntimeError, match="forced commit failure"):
        service.commit(
            graph.project_id,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"commit-failed-{failure_point}",
        )
    with session_factory() as session:
        repository = CompositionRepository(session)
        working = session.get(WorkingComposition, working_id)
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 0
        assert session.scalar(select(func.count(CompositionSnapshotTrack.snapshot_track_id))) == 0
        assert session.scalar(select(func.count(CompositionSnapshotClip.snapshot_clip_id))) == 0
        assert repository.get_project_selection(graph.project_id) is None
        assert working is not None
        assert working.base_composition_snapshot_id is None
        assert working.revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.key_hash
                    == IdempotencyRepository.hash_key(f"commit-failed-{failure_point}")
                )
            )
            == 0
        )


def test_concurrent_commit_expected_revision_creates_exactly_one_snapshot(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    _create_clip(service, graph, working_id, track_id, 1)

    def commit(key: str):
        try:
            return service.commit(
                graph.project_id,
                expected_revision=2,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, ["commit-race-a", "commit-race-b"]))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with session_factory() as session:
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 1
        assert session.scalar(select(func.count(CompositionSnapshotTrack.snapshot_track_id))) == 1
        assert session.scalar(select(func.count(CompositionSnapshotClip.snapshot_clip_id))) == 1
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.COMPOSITION_COMMIT.value
                )
            )
            == 1
        )


def test_commit_product_api_returns_structured_success_and_empty_error(
    working_client, service, graph
) -> None:
    working_id = _initialize(service, graph, "commit-api-init").identities["working_composition_id"]
    base = f"/api/v1/projects/{graph.project_id}/working-composition"
    empty = working_client.post(
        f"{base}/commit",
        json={"expected_revision": 0},
        headers={"Idempotency-Key": "commit-api-empty"},
    )
    assert empty.status_code == 409
    assert empty.json()["error"]["error_code"] == "WORKING_COMPOSITION_EMPTY"
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    _create_clip(service, graph, working_id, track_id, 1)
    response = working_client.post(
        f"{base}/commit",
        json={"expected_revision": 2},
        headers={"Idempotency-Key": "commit-api-success"},
    )
    assert response.status_code == 201
    assert response.json()["data"] == {
        "working_composition_id": str(working_id),
        "composition_snapshot_id": response.json()["data"]["composition_snapshot_id"],
        "completed_revision": 3,
        "replayed": False,
    }


def test_clip_gain_defaults_persists_replays_and_changes_only_gain(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with session_factory() as session:
        before = session.get(CompositionClip, clip_id)
        assert before is not None and before.gain_db == Decimal("0.00")
        geometry = (
            before.track_id,
            before.source_asset_version_id,
            before.timeline_start,
            before.source_in,
            before.source_out,
            before.source_duration,
        )

    first = service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("3.25"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-first",
    )
    service.rename_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        name="Gain replay barrier",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    replay = service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("3.25"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-first",
    )
    assert first.completed_revision == replay.completed_revision == 3
    assert replay.replayed is True
    assert replay.identities == {"clip_id": clip_id}
    with pytest.raises(IdempotencyConflictError):
        service.set_clip_gain(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            gain_db=Decimal("3.26"),
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="gain-first",
        )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        working = session.get(WorkingComposition, working_id)
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.result_type == IdempotencyResultType.CLIP_GAIN_UPDATE.value
            )
        )
        assert clip is not None and clip.gain_db == Decimal("3.25")
        assert geometry == (
            clip.track_id,
            clip.source_asset_version_id,
            clip.timeline_start,
            clip.source_in,
            clip.source_out,
            clip.source_duration,
        )
        assert working is not None and working.revision == 4
        assert record is not None and record.completed_revision == 3
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 0
        assert session.scalar(select(func.count(WorkingPreviewRender.preview_render_id))) == 0


@pytest.mark.parametrize(
    "value",
    [
        Decimal("-24.01"),
        Decimal("24.01"),
        Decimal("0.001"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e999999"),
        "3.00",
    ],
)
def test_clip_gain_rejects_noncanonical_values_without_mutation(
    service, session_factory, graph, value
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with pytest.raises(WorkingCompositionError) as caught:
        service.set_clip_gain(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            gain_db=value,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="gain-invalid",
        )
    assert caught.value.code is WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).gain_db == Decimal("0.00")
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_GAIN_UPDATE.value
                )
            )
            == 0
        )


def test_clip_gain_absolute_updates_support_same_identity_undo_redo(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    for expected_revision, gain_db, key in (
        (2, Decimal("-24.00"), "gain-command-initial"),
        (3, Decimal("24.00"), "gain-command-after"),
        (4, Decimal("-24.00"), "gain-command-undo"),
        (5, Decimal("24.00"), "gain-command-redo"),
    ):
        result = service.set_clip_gain(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            gain_db=gain_db,
            expected_revision=expected_revision,
            effective_owner_id=graph.owner_id,
            idempotency_key=key,
        )
        assert result.identities == {"clip_id": clip_id}
        assert result.completed_revision == expected_revision + 1

    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).gain_db == Decimal("24.00")
        assert session.get(WorkingComposition, working_id).revision == 6


def test_clip_gain_copy_split_restore_and_unsplit_structure_semantics(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        gain_db=Decimal("6.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-before-split",
    )
    copied = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        target_track_id=track_id,
        target_timeline_start="5",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-copy",
    )
    copied_id = copied.identities["clip_id"]
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-split",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    with session_factory() as session:
        assert {
            session.get(CompositionClip, identity).gain_db
            for identity in (original_id, copied_id, left_id, right_id)
        } == {Decimal("6.00")}

    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=left_id,
        gain_db=Decimal("-2.00"),
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-left-diverge",
    )
    with pytest.raises(WorkingCompositionError) as conflict:
        service.unsplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="gain-unsplit-conflict",
        )
    assert conflict.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 6
        assert session.get(CompositionClip, original_id).deleted_at is not None
        assert session.get(CompositionClip, left_id).deleted_at is None
        assert session.get(CompositionClip, right_id).deleted_at is None

    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=left_id,
        gain_db=Decimal("6.00"),
        expected_revision=6,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-left-restore",
    )
    service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=7,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-unsplit-success",
    )
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        gain_db=Decimal("1.00"),
        expected_revision=8,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-original-diverge",
    )
    with pytest.raises(WorkingCompositionError) as resplit_conflict:
        service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=9,
            effective_owner_id=graph.owner_id,
            idempotency_key="gain-resplit-conflict",
        )
    assert resplit_conflict.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT


def test_clip_gain_delete_restore_preserves_identity_value_and_revision(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("-8.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-before-delete",
    )
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-delete",
    )
    restored = service.restore_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-restore",
    )
    assert restored.identities == {"clip_id": clip_id}
    assert restored.completed_revision == 5
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and clip.deleted_at is None
        assert clip.gain_db == Decimal("-8.00")


def test_concurrent_clip_gain_expected_revision_allows_exactly_one_update(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    def update(value: Decimal, key: str):
        try:
            return service.set_clip_gain(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=clip_id,
                gain_db=value,
                expected_revision=2,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: update(*item),
                ((Decimal("1.00"), "gain-race-a"), (Decimal("2.00"), "gain-race-b")),
            )
        )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 3
        assert session.get(CompositionClip, clip_id).gain_db in {
            Decimal("1.00"),
            Decimal("2.00"),
        }
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_GAIN_UPDATE.value
                )
            )
            == 1
        )


def test_clip_gain_commit_freezes_and_checkout_restores_snapshot_value(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("4.50"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-before-commit",
    )
    committed = service.commit(
        graph.project_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-commit",
    )
    snapshot_id = committed.identities["composition_snapshot_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("-3.00"),
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-after-commit",
    )
    with session_factory() as session:
        frozen = session.scalar(
            select(CompositionSnapshotClip).where(
                CompositionSnapshotClip.composition_snapshot_id == snapshot_id,
                CompositionSnapshotClip.canonical_clip_id == clip_id,
            )
        )
        assert frozen is not None and frozen.gain_db == Decimal("4.50")
        assert session.get(CompositionClip, clip_id).gain_db == Decimal("-3.00")

    service.checkout(
        graph.project_id,
        working_composition_id=working_id,
        composition_snapshot_id=snapshot_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="gain-checkout",
    )
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).gain_db == Decimal("4.50")


def test_clip_gain_product_api_is_strict_and_returns_canonical_gain(
    working_client, service, graph
) -> None:
    working_id = _initialize(service, graph, "gain-api-init").identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    path = f"/api/v1/projects/{graph.project_id}/working-composition/clips/{clip_id}/gain"
    payload = {
        "working_composition_id": str(working_id),
        "expected_revision": 2,
        "gain_db": 2.5,
    }
    response = working_client.patch(path, json=payload, headers={"Idempotency-Key": "gain-api"})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "clip_id": str(clip_id),
        "completed_revision": 3,
        "replayed": False,
    }
    read = working_client.get(f"/api/v1/projects/{graph.project_id}/working-composition")
    assert read.json()["data"]["clips"][0]["gain_db"] == "2.50"

    out_of_range = working_client.patch(
        path,
        json={**payload, "expected_revision": 3, "gain_db": 24.01},
        headers={"Idempotency-Key": "gain-api-invalid"},
    )
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["error_code"] == "CLIP_GAIN_OUT_OF_RANGE"
    string_value = working_client.patch(
        path,
        json={**payload, "expected_revision": 3, "gain_db": "2.50"},
        headers={"Idempotency-Key": "gain-api-string"},
    )
    assert string_value.status_code == 422


def test_clip_fade_defaults_replays_and_changes_only_fade(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with session_factory() as session:
        before = session.get(CompositionClip, clip_id)
        assert before is not None
        assert (before.fade_in, before.fade_out) == (0, 0)
        stable = (
            before.track_id,
            before.source_asset_version_id,
            before.timeline_start,
            before.source_in,
            before.source_out,
            before.source_duration,
            before.gain_db,
        )

    first = service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("1.000001"),
        fade_out=Decimal("0.999999"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-first",
    )
    service.rename_track(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        name="Fade replay barrier",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    replay = service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("1.000001"),
        fade_out=Decimal("0.999999"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-first",
    )
    assert first.completed_revision == replay.completed_revision == 3
    assert replay.replayed is True
    assert replay.identities == {"clip_id": clip_id}
    with pytest.raises(IdempotencyConflictError):
        service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            fade_in=Decimal("1.00"),
            fade_out=Decimal("1.00"),
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="fade-first",
        )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        working = session.get(WorkingComposition, working_id)
        assert clip is not None and (clip.fade_in, clip.fade_out) == (1_000_001, 999_999)
        assert stable == (
            clip.track_id,
            clip.source_asset_version_id,
            clip.timeline_start,
            clip.source_in,
            clip.source_out,
            clip.source_duration,
            clip.gain_db,
        )
        assert working is not None and working.revision == 4
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.result_type == IdempotencyResultType.CLIP_FADE_UPDATE.value
            )
        )
        assert record is not None and record.completed_revision == 3
        assert session.scalar(select(func.count(CompositionSnapshot.composition_snapshot_id))) == 0
        assert session.scalar(select(func.count(WorkingPreviewRender.preview_render_id))) == 0


@pytest.mark.parametrize(
    ("fade_in", "fade_out"),
    [
        (Decimal("-0.000001"), Decimal("0")),
        (Decimal("0"), Decimal("-0.000001")),
        (Decimal("0.0000001"), Decimal("0")),
        (Decimal("NaN"), Decimal("0")),
        (Decimal("Infinity"), Decimal("0")),
        (Decimal("3"), Decimal("2")),
        ("1.00", Decimal("0")),
    ],
)
def test_clip_fade_rejects_invalid_values_without_mutation(
    service, session_factory, graph, fade_in, fade_out
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with pytest.raises(WorkingCompositionError) as caught:
        service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            fade_in=fade_in,
            fade_out=fade_out,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="fade-invalid",
        )
    assert caught.value.code is WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and (clip.fade_in, clip.fade_out) == (0, 0)
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_FADE_UPDATE.value
                )
            )
            == 0
        )


def test_clip_fade_copy_split_restore_trim_and_inverse_semantics(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        fade_in=Decimal("1"),
        fade_out=Decimal("1"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-before-copy",
    )
    copied = service.copy_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        target_track_id=track_id,
        target_timeline_start="5",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-copy",
    )
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at="2",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-split",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    with session_factory() as session:
        assert (
            session.get(CompositionClip, copied.identities["clip_id"]).fade_in,
            session.get(CompositionClip, copied.identities["clip_id"]).fade_out,
        ) == (
            1_000_000,
            1_000_000,
        )
        assert (
            session.get(CompositionClip, left_id).fade_in,
            session.get(CompositionClip, left_id).fade_out,
        ) == (1_000_000, 0)
        assert (
            session.get(CompositionClip, right_id).fade_in,
            session.get(CompositionClip, right_id).fade_out,
        ) == (0, 1_000_000)

    service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-unsplit",
    )
    service.resplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=6,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-resplit",
    )
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=left_id,
        fade_in=Decimal("0.5"),
        fade_out=Decimal("0"),
        expected_revision=7,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-child-diverge",
    )
    with pytest.raises(WorkingCompositionError) as conflict:
        service.unsplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=8,
            effective_owner_id=graph.owner_id,
            idempotency_key="fade-unsplit-conflict",
        )
    assert conflict.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 8
        assert session.get(CompositionClip, original_id).deleted_at is not None
        assert session.get(CompositionClip, left_id).deleted_at is None
        assert session.get(CompositionClip, right_id).deleted_at is None


def test_clip_fade_split_and_trim_fail_closed_without_implicit_clamp(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("1"),
        fade_out=Decimal("1"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-boundary",
    )
    for split_at in (Decimal("0.5"), Decimal("3.5")):
        with pytest.raises(WorkingCompositionError) as conflict:
            service.split_clip(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=clip_id,
                split_at=split_at,
                expected_revision=3,
                effective_owner_id=graph.owner_id,
                idempotency_key=f"fade-split-{split_at}",
            )
        assert conflict.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    with pytest.raises(WorkingCompositionError) as trimmed:
        service.trim_clip_end(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            source_out=Decimal("1.999999"),
            expected_revision=3,
            effective_owner_id=graph.owner_id,
        )
    assert trimmed.value.code is WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE
    moved = service.move_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start=Decimal("1"),
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    assert moved.completed_revision == 4
    service.delete_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-delete",
    )
    service.restore_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-restore",
    )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and (clip.fade_in, clip.fade_out) == (1_000_000, 1_000_000)
        assert clip.timeline_start == 1_000_000


def test_clip_fade_commit_freezes_and_checkout_restores_exact_envelope(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("0.500001"),
        fade_out=Decimal("0.749999"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-before-commit",
    )
    committed = service.commit(
        graph.project_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-commit",
    )
    snapshot_id = committed.identities["composition_snapshot_id"]
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("0"),
        fade_out=Decimal("0"),
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-after-commit",
    )
    with session_factory() as session:
        frozen = session.scalar(
            select(CompositionSnapshotClip).where(
                CompositionSnapshotClip.composition_snapshot_id == snapshot_id,
                CompositionSnapshotClip.canonical_clip_id == clip_id,
            )
        )
        assert frozen is not None
        assert (frozen.fade_in, frozen.fade_out) == (500_001, 749_999)
        assert (
            session.get(CompositionClip, clip_id).fade_in,
            session.get(CompositionClip, clip_id).fade_out,
        ) == (0, 0)
    service.checkout(
        graph.project_id,
        working_composition_id=working_id,
        composition_snapshot_id=snapshot_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-checkout",
    )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and (clip.fade_in, clip.fade_out) == (500_001, 749_999)


def test_clip_fade_product_api_is_strict_and_returns_canonical_values(
    working_client, service, graph
) -> None:
    working_id = _initialize(service, graph, "fade-api-init").identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    path = f"/api/v1/projects/{graph.project_id}/working-composition/clips/{clip_id}/fade"
    payload = {
        "working_composition_id": str(working_id),
        "expected_revision": 2,
        "fade_in": 0.500001,
        "fade_out": 0.75,
    }
    response = working_client.patch(path, json=payload, headers={"Idempotency-Key": "fade-api"})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "clip_id": str(clip_id),
        "completed_revision": 3,
        "replayed": False,
    }
    read = working_client.get(f"/api/v1/projects/{graph.project_id}/working-composition")
    assert read.json()["data"]["clips"][0]["fade_in"] == "0.500001"
    assert read.json()["data"]["clips"][0]["fade_out"] == "0.75"
    string_value = working_client.patch(
        path,
        json={**payload, "expected_revision": 3, "fade_in": "0.5"},
        headers={"Idempotency-Key": "fade-api-string"},
    )
    assert string_value.status_code == 422
    invalid_sum = working_client.patch(
        path,
        json={**payload, "expected_revision": 3, "fade_in": 3, "fade_out": 2},
        headers={"Idempotency-Key": "fade-api-sum"},
    )
    assert invalid_sum.status_code == 422
    assert invalid_sum.json()["error"]["error_code"] == "CLIP_FADE_OUT_OF_RANGE"


def test_clip_fade_concurrent_cas_and_forced_completion_failure_are_atomic(
    service, session_factory, graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    def update(value: Decimal, key: str):
        try:
            return service.set_clip_fade(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=clip_id,
                fade_in=value,
                fade_out=Decimal("0"),
                expected_revision=2,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        except WorkingCompositionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: update(*item),
                ((Decimal("1"), "fade-race-a"), (Decimal("2"), "fade-race-b")),
            )
        )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT

    original_complete = IdempotencyRepository.complete_with_result

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("forced completion failure")

    monkeypatch.setattr(IdempotencyRepository, "complete_with_result", fail_completion)
    with pytest.raises(RuntimeError, match="forced completion failure"):
        service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            fade_in=Decimal("0.25"),
            fade_out=Decimal("0.25"),
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="fade-forced-failure",
        )
    monkeypatch.setattr(IdempotencyRepository, "complete_with_result", original_complete)
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and clip.fade_in in {1_000_000, 2_000_000}
        assert clip.fade_out == 0
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_FADE_UPDATE.value
                )
            )
            == 1
        )


def test_clip_fade_rejects_missing_and_tombstoned_clip_without_completion(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    for candidate, key in ((uuid4(), "fade-missing"), (clip_id, "fade-deleted")):
        if candidate == clip_id:
            service.delete_clip(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=clip_id,
                expected_revision=2,
                effective_owner_id=graph.owner_id,
                idempotency_key="fade-delete-before-update",
            )
            expected_revision = 3
        else:
            expected_revision = 2
        with pytest.raises(WorkingCompositionError) as caught:
            service.set_clip_fade(
                graph.project_id,
                working_composition_id=working_id,
                clip_id=candidate,
                fade_in=Decimal("0.5"),
                fade_out=Decimal("0.5"),
                expected_revision=expected_revision,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        assert caught.value.code is WorkingCompositionErrorCode.CLIP_NOT_FOUND
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_FADE_UPDATE.value
                )
            )
            == 0
        )


def test_clip_fade_rejects_cross_scope_and_stale_revision_without_completion(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    attempts = (
        (uuid4(), working_id, graph.owner_id, 2, "fade-cross-project", ResourceNotFoundError),
        (
            graph.project_id,
            uuid4(),
            graph.owner_id,
            2,
            "fade-cross-working",
            WorkingCompositionError,
        ),
        (
            graph.project_id,
            working_id,
            uuid4(),
            2,
            "fade-cross-owner",
            WorkspaceBootstrapRequiredError,
        ),
        (
            graph.project_id,
            working_id,
            graph.owner_id,
            1,
            "fade-stale",
            WorkingCompositionError,
        ),
    )
    for project_id, candidate_working_id, owner_id, revision, key, error_type in attempts:
        with pytest.raises(error_type):
            service.set_clip_fade(
                project_id,
                working_composition_id=candidate_working_id,
                clip_id=clip_id,
                fade_in=Decimal("0.5"),
                fade_out=Decimal("0.5"),
                expected_revision=revision,
                effective_owner_id=owner_id,
                idempotency_key=key,
            )

    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip is not None and (clip.fade_in, clip.fade_out) == (0, 0)
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_FADE_UPDATE.value
                )
            )
            == 0
        )


def test_clip_fade_resplit_rejects_tombstoned_child_divergence(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    original_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        fade_in=Decimal("1"),
        fade_out=Decimal("1"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-resplit-original",
    )
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=original_id,
        split_at=Decimal("2"),
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-resplit-split",
    )
    left_id = split.identities["left_clip_id"]
    right_id = split.identities["right_clip_id"]
    service.unsplit_clip(
        graph.project_id,
        working_composition_id=working_id,
        original_clip_id=original_id,
        left_clip_id=left_id,
        right_clip_id=right_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="fade-resplit-unsplit",
    )
    with session_factory.begin() as session:
        left = session.get(CompositionClip, left_id)
        assert left is not None and left.deleted_at is not None
        left.fade_in = 500_000

    with pytest.raises(WorkingCompositionError) as conflict:
        service.resplit_clip(
            graph.project_id,
            working_composition_id=working_id,
            original_clip_id=original_id,
            left_clip_id=left_id,
            right_clip_id=right_id,
            expected_revision=5,
            effective_owner_id=graph.owner_id,
            idempotency_key="fade-resplit-diverged",
        )
    assert conflict.value.code is WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 5
        assert session.get(CompositionClip, original_id).deleted_at is None
        assert session.get(CompositionClip, left_id).deleted_at is not None
        assert session.get(CompositionClip, right_id).deleted_at is not None


@pytest.mark.parametrize("duration", ["2", "4", "6"])
def test_clip_loop_enable_accepts_short_equal_and_long_duration(
    service, session_factory, graph, duration
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    result = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration=duration,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key=f"enable-{duration}",
    )
    assert result.completed_revision == 3
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip.loop_enabled is True
        assert clip.timeline_duration == int(duration) * 1_000_000
        assert clip.loop_phase == 0


@pytest.mark.parametrize("duration", ["2", "6"])
def test_clip_loop_disable_rejects_noncanonical_duration_without_side_effects(
    service, session_factory, graph, duration
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with pytest.raises(WorkingCompositionError) as caught:
        service.set_clip_loop(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            loop_enabled=False,
            timeline_duration=duration,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"disable-invalid-{duration}",
        )
    assert caught.value.code is WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.loop_enabled, clip.timeline_duration, clip.loop_phase) == (
            False,
            4_000_000,
            0,
        )
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_LOOP_UPDATE.value
                )
            )
            == 0
        )


def test_clip_loop_valid_disable_replays_and_fingerprint_conflicts(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    first = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=False,
        timeline_duration="4",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="disable-valid",
    )
    replay = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=False,
        timeline_duration="4",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="disable-valid",
    )
    assert first.completed_revision == replay.completed_revision == 3
    assert replay.replayed is True
    with pytest.raises(IdempotencyConflictError):
        service.set_clip_loop(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            loop_enabled=False,
            timeline_duration="3",
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key="disable-valid",
        )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.loop_enabled, clip.timeline_duration, clip.loop_phase) == (
            False,
            4_000_000,
            0,
        )


def test_clip_loop_disable_response_loss_retry_has_one_mutation_and_completion(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    lost_response = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=False,
        timeline_duration="4",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="disable-response-loss",
    )
    retry = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=False,
        timeline_duration="4",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="disable-response-loss",
    )

    assert retry.replayed is True
    assert retry.completed_revision == lost_response.completed_revision == 3
    assert retry.identities == lost_response.identities
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.loop_enabled, clip.timeline_duration, clip.loop_phase) == (
            False,
            4_000_000,
            0,
        )
        assert session.get(WorkingComposition, working_id).revision == 3
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_LOOP_UPDATE.value
                )
            )
            == 1
        )


def test_clip_loop_duration_update_preserves_nonzero_phase(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="4",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="phase-preserve-enable",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, clip_id).loop_phase = 3_000_000
    first = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="phase-preserve-duration",
    )
    replay = service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="phase-preserve-duration",
    )
    assert first.completed_revision == replay.completed_revision == 4
    assert replay.replayed is True
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.timeline_duration, clip.loop_phase) == (6_000_000, 3_000_000)
        assert session.get(WorkingComposition, working_id).revision == 4


def test_clip_loop_history_restore_recovers_nonzero_phase_after_disable(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-enable",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, clip_id).loop_phase = 3_000_000
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=False,
        timeline_duration="4",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-disable",
    )
    restored = service.restore_clip_loop_state(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        loop_phase="3",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-history",
    )
    replay = service.restore_clip_loop_state(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        loop_phase="3",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-history",
    )
    assert restored.completed_revision == replay.completed_revision == 5
    assert replay.replayed is True
    with pytest.raises(IdempotencyConflictError):
        service.restore_clip_loop_state(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            loop_enabled=True,
            timeline_duration="6",
            loop_phase="2",
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="restore-history",
        )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.loop_enabled, clip.timeline_duration, clip.loop_phase) == (
            True,
            6_000_000,
            3_000_000,
        )
        assert session.get(WorkingComposition, working_id).revision == 5
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_LOOP_RESTORE.value
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    ("loop_enabled", "duration", "phase"),
    [(False, "4", "1"), (False, "3", "0"), (True, "6", "4")],
)
def test_clip_loop_history_restore_rejects_noncanonical_geometry_without_side_effects(
    service, session_factory, graph, loop_enabled, duration, phase
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    with pytest.raises(WorkingCompositionError) as caught:
        service.restore_clip_loop_state(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            loop_enabled=loop_enabled,
            timeline_duration=duration,
            loop_phase=phase,
            expected_revision=2,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"invalid-restore-{loop_enabled}-{duration}-{phase}",
        )
    assert caught.value.code is WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.loop_enabled, clip.timeline_duration, clip.loop_phase) == (
            False,
            4_000_000,
            0,
        )
        assert session.get(WorkingComposition, working_id).revision == 2
        assert (
            session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.result_type == IdempotencyResultType.CLIP_LOOP_RESTORE.value
                )
            )
            == 0
        )


@pytest.mark.parametrize(
    ("phase", "split_at", "expected_right_phase"),
    [(0, "1", 1_000_000), (1_000_000, "1", 2_000_000), (3_000_000, "2", 1_000_000)],
)
def test_clip_loop_split_preserves_phase_equation_and_short_fragments(
    service, session_factory, graph, phase, split_at, expected_right_phase
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key=f"split-enable-{phase}",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, clip_id).loop_phase = phase

    service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        split_at=split_at,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key=f"split-phase-{phase}",
    )
    with session_factory() as session:
        children = session.scalars(
            select(CompositionClip)
            .where(CompositionClip.split_from_clip_id == clip_id)
            .order_by(CompositionClip.timeline_start)
        ).all()
        left, right = children
        split_us = int(Decimal(split_at) * 1_000_000)
        assert (left.timeline_duration, left.loop_phase) == (split_us, phase)
        assert (right.timeline_duration, right.loop_phase) == (
            6_000_000 - split_us,
            expected_right_phase,
        )
        assert left.timeline_duration < left.source_out - left.source_in


@pytest.mark.parametrize(
    ("phase", "trim_start", "expected_phase"),
    [(0, "1", 1_000_000), (1_000_000, "1", 2_000_000), (3_000_000, "2", 1_000_000)],
)
def test_clip_loop_trim_start_advances_phase_and_allows_short_result(
    service, session_factory, graph, phase, trim_start, expected_phase
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="3",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key=f"trim-start-enable-{phase}",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, clip_id).loop_phase = phase

    service.trim_clip_start(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start=trim_start,
        source_in="0",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        trim_us = int(Decimal(trim_start) * 1_000_000)
        assert clip.timeline_start == trim_us
        assert clip.timeline_duration == 3_000_000 - trim_us
        assert clip.loop_phase == expected_phase
        assert clip.timeline_duration < clip.source_out - clip.source_in


def test_clip_loop_trim_end_keeps_timeline_start_and_phase(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="trim-end-enable",
    )
    with session_factory.begin() as session:
        session.get(CompositionClip, clip_id).loop_phase = 3_000_000

    service.trim_clip_end(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        source_out="2",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert clip.timeline_start == 0
        assert clip.timeline_duration == 4_000_000
        assert clip.loop_phase == 3_000_000


def test_split_nonzero_phase_fragment_duration_edit_preserves_phase(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="6",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-duration-enable",
    )
    split = service.split_clip(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        split_at="1",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-duration-fragment",
    )
    right_id = split.identities["right_clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=right_id,
        loop_enabled=True,
        timeline_duration="3",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="split-duration-edit",
    )
    with session_factory() as session:
        right = session.get(CompositionClip, right_id)
        assert (right.timeline_duration, right.loop_phase) == (3_000_000, 1_000_000)


def test_trim_nonzero_phase_fragment_duration_edit_preserves_phase(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="3",
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="trim-duration-enable",
    )
    service.trim_clip_start(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        timeline_start="1",
        source_in="0",
        expected_revision=3,
        effective_owner_id=graph.owner_id,
    )
    service.set_clip_loop(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        loop_enabled=True,
        timeline_duration="1.5",
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="trim-duration-edit",
    )
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.timeline_duration, clip.loop_phase) == (1_500_000, 1_000_000)
