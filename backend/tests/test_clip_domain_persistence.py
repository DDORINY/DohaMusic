"""D3 Clip Domain persistence constraint와 Repository 계약 회귀 테스트."""

from __future__ import annotations

import inspect as python_inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.workspace import (
    Asset,
    AssetType,
    AssetVersion,
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    MusicProject,
    ProcessingChain,
    ProjectAsset,
    SnapshotItem,
    WorkingComposition,
    Workspace,
)
from backend.repositories.workspace.composition_repository import (
    CompositionRepository,
)


@dataclass(frozen=True, slots=True)
class Graph:
    project_id: UUID
    snapshot_id: UUID
    asset_version_id: UUID


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'clip-persistence.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            Workspace.__table__,
            MusicProject.__table__,
            ProjectAsset.__table__,
            ProcessingChain.__table__,
            Asset.__table__,
            AssetVersion.__table__,
            CompositionSnapshot.__table__,
            SnapshotItem.__table__,
            WorkingComposition.__table__,
            CompositionTrack.__table__,
            CompositionClip.__table__,
            CompositionSnapshotTrack.__table__,
            CompositionSnapshotClip.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_graph(factory: sessionmaker[Session]) -> Graph:
    owner_id = uuid4()
    workspace = Workspace(
        workspace_id=uuid4(),
        owner_id=owner_id,
        name="Clip Workspace",
        lifecycle_status="active",
    )
    project = MusicProject(
        project_id=uuid4(),
        workspace_id=workspace.workspace_id,
        title="Clip Project",
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
    snapshot = CompositionSnapshot(
        composition_snapshot_id=uuid4(),
        project_id=project.project_id,
        snapshot_version=1,
        mix_settings_snapshot={},
        provider_versions={},
        model_manifest_ids={},
        created_by=owner_id,
    )
    item = SnapshotItem(
        composition_snapshot_id=snapshot.composition_snapshot_id,
        asset_version_id=version.asset_version_id,
        item_role="music",
        sort_order=0,
    )
    with factory.begin() as session:
        session.add(workspace)
        session.flush()
        session.add_all([project, asset])
        session.flush()
        session.add_all([version, snapshot])
        session.flush()
        session.add(item)
    return Graph(project.project_id, snapshot.composition_snapshot_id, version.asset_version_id)


def _working(graph: Graph, **overrides) -> WorkingComposition:
    values = {
        "working_composition_id": uuid4(),
        "project_id": graph.project_id,
        "base_composition_snapshot_id": graph.snapshot_id,
        "mix_settings": {},
        "revision": 0,
    }
    values.update(overrides)
    return WorkingComposition(**values)


def _track(working_id: UUID, order: int = 0, **overrides) -> CompositionTrack:
    values = {
        "track_id": uuid4(),
        "working_composition_id": working_id,
        "track_type": "audio",
        "name": f"Track {order}",
        "track_order": order,
    }
    values.update(overrides)
    return CompositionTrack(**values)


def _clip(
    working_id: UUID,
    track_id: UUID,
    asset_version_id: UUID,
    **overrides,
) -> CompositionClip:
    values = {
        "clip_id": uuid4(),
        "working_composition_id": working_id,
        "track_id": track_id,
        "source_asset_version_id": asset_version_id,
        "timeline_start": 0,
        "source_in": 0,
        "source_out": 1_000_001,
        "source_duration": 3_000_003,
    }
    values.update(overrides)
    return CompositionClip(**values)


def test_working_composition_enforces_project_unique_revision_and_same_project_base(
    session_factory,
) -> None:
    first = _seed_graph(session_factory)
    second = _seed_graph(session_factory)
    with session_factory.begin() as session:
        session.add(_working(first))

    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_working(first, base_composition_snapshot_id=None))
        session.flush()
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_working(second, revision=-1))
        session.flush()
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_working(second, base_composition_snapshot_id=first.snapshot_id))
        session.flush()


def test_repository_bounds_mix_settings_without_owning_transaction(
    session_factory,
) -> None:
    graph = _seed_graph(session_factory)
    with session_factory() as session:
        repository = CompositionRepository(session)
        with pytest.raises(TypeError):
            repository.add_working_composition(_working(graph, mix_settings=[]))
        with pytest.raises(ValueError, match="8192"):
            repository.add_working_composition(_working(graph, mix_settings={"value": "x" * 8_193}))
        assert session.scalar(select(WorkingComposition)) is None


def test_track_active_order_collision_and_tombstone_reuse(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    first = _track(working.working_composition_id)
    with session_factory.begin() as session:
        session.add(working)
        session.flush()
        session.add(first)

    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_track(working.working_composition_id))
        session.flush()

    with session_factory.begin() as session:
        persisted = session.get(CompositionTrack, first.track_id)
        assert persisted is not None
        persisted.deleted_at = datetime.now(UTC)
    with session_factory.begin() as session:
        session.add(_track(working.working_composition_id))


def test_track_delete_guard_counts_only_same_working_active_clips(
    session_factory,
) -> None:
    first = _seed_graph(session_factory)
    second = _seed_graph(session_factory)
    first_working = _working(first)
    second_working = _working(second)
    first_track = _track(first_working.working_composition_id)
    second_track = _track(second_working.working_composition_id)
    active = _clip(
        first_working.working_composition_id,
        first_track.track_id,
        first.asset_version_id,
    )
    deleted = _clip(
        first_working.working_composition_id,
        first_track.track_id,
        first.asset_version_id,
        timeline_start=2_000_000,
        deleted_at=datetime.now(UTC),
    )
    foreign = _clip(
        second_working.working_composition_id,
        second_track.track_id,
        second.asset_version_id,
    )
    with session_factory.begin() as session:
        session.add_all([first_working, second_working])
        session.flush()
        session.add_all([first_track, second_track])
        session.flush()
        session.add_all([active, deleted, foreign])

    with session_factory() as session:
        repository = CompositionRepository(session)
        assert (
            repository.count_active_composition_clips(
                working_composition_id=first_working.working_composition_id,
                track_id=first_track.track_id,
            )
            == 1
        )
        assert (
            repository.count_active_composition_clips(
                working_composition_id=second_working.working_composition_id,
                track_id=first_track.track_id,
            )
            == 0
        )


def test_track_type_and_order_checks(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    with session_factory.begin() as session:
        session.add(working)
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_track(working.working_composition_id, track_type="midi"))
        session.flush()
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(_track(working.working_composition_id, order=-1))
        session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeline_start": -1},
        {"source_in": -1},
        {"source_out": 0},
        {"source_duration": 0},
        {"source_out": 3_000_004},
    ],
)
def test_clip_time_constraints_reject_invalid_ranges(
    session_factory, overrides: dict[str, int]
) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    track = _track(working.working_composition_id)
    with session_factory.begin() as session:
        session.add(working)
        session.flush()
        session.add(track)
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            _clip(
                working.working_composition_id,
                track.track_id,
                graph.asset_version_id,
                **overrides,
            )
        )
        session.flush()


def test_clip_exact_microseconds_and_asset_version_round_trip(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    track = _track(working.working_composition_id)
    clip = _clip(
        working.working_composition_id,
        track.track_id,
        graph.asset_version_id,
        timeline_start=123_456,
        source_in=234_567,
        source_out=1_234_568,
    )
    with session_factory.begin() as session:
        session.add(working)
        session.flush()
        session.add(track)
        session.flush()
        session.add(clip)
    with session_factory() as session:
        persisted = session.get(CompositionClip, clip.clip_id)
        assert persisted is not None
        assert (
            persisted.timeline_start,
            persisted.source_in,
            persisted.source_out,
            persisted.source_duration,
        ) == (123_456, 234_567, 1_234_568, 3_000_003)
        assert persisted.source_asset_version_id == graph.asset_version_id

    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            _clip(
                working.working_composition_id,
                track.track_id,
                uuid4(),
                timeline_start=2_000_001,
            )
        )
        session.flush()


def test_clip_rejects_cross_working_track_and_split_parent(session_factory) -> None:
    first = _seed_graph(session_factory)
    second = _seed_graph(session_factory)
    working_a = _working(first)
    working_b = _working(second)
    track_a = _track(working_a.working_composition_id)
    track_b = _track(working_b.working_composition_id)
    parent = _clip(
        working_a.working_composition_id,
        track_a.track_id,
        first.asset_version_id,
    )
    with session_factory.begin() as session:
        session.add_all([working_a, working_b])
        session.flush()
        session.add_all([track_a, track_b])
        session.flush()
        session.add(parent)

    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            _clip(
                working_a.working_composition_id,
                track_b.track_id,
                first.asset_version_id,
                timeline_start=2_000_000,
            )
        )
        session.flush()
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            _clip(
                working_b.working_composition_id,
                track_b.track_id,
                second.asset_version_id,
                split_from_clip_id=parent.clip_id,
            )
        )
        session.flush()


def test_split_lineage_survives_parent_tombstone(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    track = _track(working.working_composition_id)
    parent = _clip(
        working.working_composition_id,
        track.track_id,
        graph.asset_version_id,
    )
    with session_factory.begin() as session:
        session.add(working)
        session.flush()
        session.add(track)
        session.flush()
        session.add(parent)
    with session_factory.begin() as session:
        persisted = session.get(CompositionClip, parent.clip_id)
        assert persisted is not None
        persisted.deleted_at = datetime.now(UTC)
        session.add(
            _clip(
                working.working_composition_id,
                track.track_id,
                graph.asset_version_id,
                clip_id=uuid4(),
                split_from_clip_id=parent.clip_id,
            )
        )
    with session_factory() as session:
        assert session.get(CompositionClip, parent.clip_id) is not None


def test_overlap_helper_uses_half_open_ranges_and_ignores_tombstones(
    session_factory,
) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    track = _track(working.working_composition_id)
    first = _clip(
        working.working_composition_id,
        track.track_id,
        graph.asset_version_id,
    )
    with session_factory.begin() as session:
        repository = CompositionRepository(session)
        repository.add_working_composition(working)
        repository.add_composition_track(track)
        repository.add_composition_clip(first)
        repository.add_composition_clip(
            _clip(
                working.working_composition_id,
                track.track_id,
                graph.asset_version_id,
                timeline_start=1_000_001,
            )
        )
        with pytest.raises(ValueError, match="겹칠"):
            repository.add_composition_clip(
                _clip(
                    working.working_composition_id,
                    track.track_id,
                    graph.asset_version_id,
                    timeline_start=1_000_000,
                )
            )

    with session_factory.begin() as session:
        persisted = session.get(CompositionClip, first.clip_id)
        assert persisted is not None
        persisted.deleted_at = datetime.now(UTC)
    with session_factory.begin() as session:
        CompositionRepository(session).add_composition_clip(
            _clip(
                working.working_composition_id,
                track.track_id,
                graph.asset_version_id,
                timeline_start=0,
            )
        )


def test_repository_reads_tracks_and_clips_deterministically(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    track_b = _track(working.working_composition_id, 1)
    track_a = _track(working.working_composition_id, 0)
    clips = [
        _clip(
            working.working_composition_id,
            track_a.track_id,
            graph.asset_version_id,
            timeline_start=start,
        )
        for start in (2_000_002, 0, 1_000_001)
    ]
    with session_factory.begin() as session:
        session.add(working)
        session.flush()
        session.add_all([track_b, track_a])
        session.flush()
        session.add_all(clips)
    with session_factory() as session:
        repository = CompositionRepository(session)
        assert [
            item.track_order
            for item in repository.list_active_composition_tracks(working.working_composition_id)
        ] == [0, 1]
        assert [
            item.timeline_start
            for item in repository.list_active_composition_clips(track_a.track_id)
        ] == [0, 1_000_001, 2_000_002]


def test_revision_update_is_optimistic_and_failure_changes_zero_rows(
    session_factory,
) -> None:
    graph = _seed_graph(session_factory)
    working = _working(graph)
    with session_factory.begin() as session:
        CompositionRepository(session).add_working_composition(working)
    with session_factory.begin() as session:
        repository = CompositionRepository(session)
        assert (
            repository.increment_working_revision(
                working.working_composition_id, expected_revision=0
            )
            == 1
        )
    with session_factory.begin() as session:
        repository = CompositionRepository(session)
        assert (
            repository.increment_working_revision(
                working.working_composition_id, expected_revision=0
            )
            is None
        )
    with session_factory() as session:
        persisted = session.get(WorkingComposition, working.working_composition_id)
        assert persisted is not None
        assert persisted.revision == 1


def test_snapshot_arrangement_constraints_and_exact_lineage(session_factory) -> None:
    first = _seed_graph(session_factory)
    second = _seed_graph(session_factory)
    track = CompositionSnapshotTrack(
        snapshot_track_id=uuid4(),
        composition_snapshot_id=first.snapshot_id,
        canonical_track_id=uuid4(),
        track_type="audio",
        name="Frozen Track",
        track_order=0,
    )
    clip = CompositionSnapshotClip(
        composition_snapshot_id=first.snapshot_id,
        snapshot_track_id=track.snapshot_track_id,
        canonical_clip_id=uuid4(),
        source_asset_version_id=first.asset_version_id,
        timeline_start=123,
        source_in=456,
        source_out=1_000_457,
        source_duration=2_000_000,
        split_from_clip_id=uuid4(),
    )
    with session_factory.begin() as session:
        session.add(track)
        session.flush()
        session.add(clip)
    with session_factory() as session:
        persisted = session.get(CompositionSnapshotClip, clip.snapshot_clip_id)
        assert persisted is not None
        assert persisted.source_asset_version_id == first.asset_version_id
        assert persisted.canonical_clip_id == clip.canonical_clip_id

    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            CompositionSnapshotClip(
                composition_snapshot_id=second.snapshot_id,
                snapshot_track_id=track.snapshot_track_id,
                canonical_clip_id=uuid4(),
                source_asset_version_id=second.asset_version_id,
                timeline_start=0,
                source_in=0,
                source_out=1,
                source_duration=1,
            )
        )
        session.flush()


@pytest.mark.parametrize("collision", ["canonical", "order"])
def test_snapshot_track_identity_and_order_are_snapshot_local_unique(
    session_factory, collision: str
) -> None:
    graph = _seed_graph(session_factory)
    canonical_id = uuid4()
    first = CompositionSnapshotTrack(
        composition_snapshot_id=graph.snapshot_id,
        canonical_track_id=canonical_id,
        track_type="audio",
        name="One",
        track_order=0,
    )
    with session_factory.begin() as session:
        session.add(first)
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            CompositionSnapshotTrack(
                composition_snapshot_id=graph.snapshot_id,
                canonical_track_id=canonical_id if collision == "canonical" else uuid4(),
                track_type="audio",
                name="Two",
                track_order=1 if collision == "canonical" else 0,
            )
        )
        session.flush()


def test_snapshot_items_remain_compatible_and_arrangement_is_fk_separate(
    session_factory,
) -> None:
    graph = _seed_graph(session_factory)
    with session_factory() as session:
        items = list(session.scalars(select(SnapshotItem)))
        assert len(items) == 1
        assert items[0].asset_version_id == graph.asset_version_id

    mutable_tables = {
        "working_compositions",
        "composition_tracks",
        "composition_clips",
    }
    for model in (CompositionSnapshotTrack, CompositionSnapshotClip):
        targets = {foreign_key.column.table.name for foreign_key in model.__table__.foreign_keys}
        assert targets.isdisjoint(mutable_tables)


def test_repository_has_no_transaction_or_immutable_mutation_ownership() -> None:
    source = python_inspect.getsource(CompositionRepository)
    assert ".commit(" not in source
    assert ".rollback(" not in source
    for method_name in (
        "update_snapshot",
        "delete_snapshot",
        "update_snapshot_track",
        "delete_snapshot_track",
        "update_snapshot_clip",
        "delete_snapshot_clip",
    ):
        assert not hasattr(CompositionRepository, method_name)


def test_service_owned_rollback_leaves_no_partial_rows(session_factory) -> None:
    graph = _seed_graph(session_factory)
    working_id = uuid4()
    track_id = uuid4()
    with (
        pytest.raises(RuntimeError, match="forced failure"),
        session_factory.begin() as session,
    ):
        repository = CompositionRepository(session)
        repository.add_working_composition(_working(graph, working_composition_id=working_id))
        repository.add_composition_track(_track(working_id, track_id=track_id))
        repository.add_composition_clip(_clip(working_id, track_id, graph.asset_version_id))
        raise RuntimeError("forced failure")
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id) is None
        assert session.get(CompositionTrack, track_id) is None
        assert session.scalar(select(CompositionClip)) is None


def test_required_query_patterns_have_indexes() -> None:
    expected = {
        "working_compositions": {"uq_working_compositions_project"},
        "composition_tracks": {"ix_composition_tracks_active_order"},
        "composition_clips": {
            "ix_composition_clips_active_timeline",
            "ix_composition_clips_source_asset_version",
            "ix_composition_clips_split_parent",
        },
        "composition_snapshot_tracks": {"ix_composition_snapshot_tracks_order"},
        "composition_snapshot_clips": {
            "ix_composition_snapshot_clips_timeline",
            "ix_composition_snapshot_clips_source_asset_version",
        },
    }
    for table_name, names in expected.items():
        table = Base.metadata.tables[table_name]
        actual = {index.name for index in table.indexes} | {
            constraint.name for constraint in table.constraints
        }
        assert names <= actual


@pytest.mark.parametrize(
    ("table_name", "sql", "index_name"),
    [
        (
            "WORKING_COMPOSITIONS",
            "SELECT * FROM working_compositions WHERE project_id = ?",
            "SQLITE_AUTOINDEX_WORKING_COMPOSITIONS",
        ),
        (
            "COMPOSITION_TRACKS",
            (
                "SELECT * FROM composition_tracks WHERE working_composition_id = ? "
                "AND deleted_at IS NULL ORDER BY track_order, track_id"
            ),
            "IX_COMPOSITION_TRACKS_ACTIVE_ORDER",
        ),
        (
            "COMPOSITION_CLIPS",
            (
                "SELECT * FROM composition_clips WHERE track_id = ? "
                "AND deleted_at IS NULL ORDER BY timeline_start, clip_id"
            ),
            "IX_COMPOSITION_CLIPS_ACTIVE_TIMELINE",
        ),
        (
            "COMPOSITION_SNAPSHOT_TRACKS",
            (
                "SELECT * FROM composition_snapshot_tracks "
                "WHERE composition_snapshot_id = ? "
                "ORDER BY track_order, snapshot_track_id"
            ),
            "IX_COMPOSITION_SNAPSHOT_TRACKS_ORDER",
        ),
        (
            "COMPOSITION_SNAPSHOT_CLIPS",
            (
                "SELECT * FROM composition_snapshot_clips "
                "WHERE snapshot_track_id = ? "
                "ORDER BY timeline_start, snapshot_clip_id"
            ),
            "IX_COMPOSITION_SNAPSHOT_CLIPS_TIMELINE",
        ),
    ],
)
def test_required_query_plans_use_indexes_without_temp_sort(
    session_factory, table_name: str, sql: str, index_name: str
) -> None:
    engine = session_factory.kw["bind"]
    with engine.connect() as connection:
        details = [
            str(row[3]).upper()
            for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}", (uuid4().hex,))
        ]
    combined = " ".join(details)
    assert f"SEARCH {table_name} USING" in combined
    assert index_name in combined
    assert "TEMP B-TREE" not in combined
    assert f"SCAN {table_name}" not in combined
