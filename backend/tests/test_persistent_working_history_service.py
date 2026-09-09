"""Backend-authoritative persistent WorkingComposition history tests."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from backend.models.workspace import (
    CompositionClip,
    CompositionTrack,
    WorkingComposition,
    WorkingCompositionHistoryEntry,
)
from backend.repositories.workspace import CompositionHistoryRepository
from backend.services.workspace import WorkingCompositionError, WorkingCompositionErrorCode
from backend.tests.test_working_composition_service import (
    _create_clip,
    _create_track,
    _initialize,
    graph,
    schema_template,
    service,
    session_factory,
    working_client,
)

__all__ = ["graph", "schema_template", "service", "session_factory", "working_client"]


def test_gain_fade_history_is_persistent_strict_lifo_and_idempotent(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]

    empty = service.get_history_state(
        graph.project_id,
        working_composition_id=working_id,
        effective_owner_id=graph.owner_id,
    )
    assert (empty["cursor"], empty["command_count"], empty["can_undo"]) == (0, 0, False)

    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("3.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="history-gain",
    )
    service.set_clip_fade(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        fade_in=Decimal("0.25"),
        fade_out=Decimal("0.5"),
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="history-fade",
    )
    undone = service.undo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="history-undo-fade",
    )
    replay = service.undo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="history-undo-fade",
    )
    assert undone.completed_revision == replay.completed_revision == 5
    assert replay.replayed is True
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.gain_db, clip.fade_in, clip.fade_out) == (Decimal("3.00"), 0, 0)
        assert session.get(WorkingComposition, working_id).revision == 5

    redone = service.redo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="history-redo-fade",
    )
    assert redone.completed_revision == 6
    with session_factory() as session:
        clip = session.get(CompositionClip, clip_id)
        assert (clip.fade_in, clip.fade_out) == (250_000, 500_000)


def test_track_master_history_is_typed_idempotent_and_strict_lifo(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("2.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="mixed-clip",
    )
    track = service.set_track_mixer(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        gain_db=Decimal("3.00"),
        pan=Decimal("-0.50"),
        muted=True,
        solo=False,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="mixed-track",
    )
    replay = service.set_track_mixer(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        gain_db=Decimal("3.00"),
        pan=Decimal("-0.50"),
        muted=True,
        solo=False,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="mixed-track",
    )
    assert replay.replayed and replay.completed_revision == track.completed_revision == 4
    service.set_master_gain(
        graph.project_id,
        working_composition_id=working_id,
        master_gain_db=Decimal("-4.00"),
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="mixed-master",
    )
    with session_factory() as session:
        entries = (
            session.query(WorkingCompositionHistoryEntry)
            .order_by(WorkingCompositionHistoryEntry.sequence)
            .all()
        )
        assert [(e.command_type, e.target_type, e.target_id, e.clip_id) for e in entries] == [
            ("CLIP_GAIN", "CLIP", clip_id, clip_id),
            ("TRACK_MIXER", "TRACK", track_id, None),
            ("MASTER_GAIN", "WORKING_COMPOSITION", working_id, None),
        ]
    revisions = [5, 6, 7]
    for index, expected in enumerate(revisions):
        service.undo_history(
            graph.project_id,
            working_composition_id=working_id,
            expected_revision=expected,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"mixed-undo-{index}",
        )
    with session_factory() as session:
        assert session.get(CompositionClip, clip_id).gain_db == Decimal("0.00")
        restored_track = session.get(CompositionTrack, track_id)
        assert (
            restored_track.gain_db,
            restored_track.pan,
            restored_track.muted,
            restored_track.solo,
        ) == (Decimal("0.00"), Decimal("0.00"), False, False)
        assert session.get(WorkingComposition, working_id).master_gain_db == Decimal("0.00")
    for index, expected in enumerate((8, 9, 10)):
        service.redo_history(
            graph.project_id,
            working_composition_id=working_id,
            expected_revision=expected,
            effective_owner_id=graph.owner_id,
            idempotency_key=f"mixed-redo-{index}",
        )
    with session_factory() as session:
        restored_track = session.get(CompositionTrack, track_id)
        assert (restored_track.gain_db, restored_track.pan, restored_track.muted) == (
            Decimal("3.00"),
            Decimal("-0.50"),
            True,
        )
        assert session.get(WorkingComposition, working_id).master_gain_db == Decimal("-4.00")


def test_mixer_history_failure_rolls_back_state_and_revision(
    service, session_factory, graph, monkeypatch
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    monkeypatch.setattr(
        CompositionHistoryRepository,
        "append",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("history failed")),
    )
    with pytest.raises(RuntimeError, match="history failed"):
        service.set_track_mixer(
            graph.project_id,
            working_composition_id=working_id,
            track_id=track_id,
            gain_db=Decimal("4.00"),
            pan=Decimal("0.25"),
            muted=False,
            solo=True,
            expected_revision=1,
            effective_owner_id=graph.owner_id,
            idempotency_key="rollback-track",
        )
    with session_factory() as session:
        track = session.get(CompositionTrack, track_id)
        assert (track.gain_db, track.pan, track.muted, track.solo) == (
            Decimal("0.00"),
            Decimal("0.00"),
            False,
            False,
        )
        assert session.get(WorkingComposition, working_id).revision == 1


def test_track_and_master_mutation_api_and_typed_history_response(
    working_client, service, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    base = f"/api/v1/projects/{graph.project_id}/working-composition"
    track = working_client.patch(
        f"{base}/tracks/{track_id}/mixer",
        json={
            "working_composition_id": str(working_id),
            "expected_revision": 1,
            "gain_db": 2,
            "pan": 0.5,
            "muted": False,
            "solo": True,
        },
        headers={"Idempotency-Key": "api-mixer"},
    )
    assert track.status_code == 200
    master = working_client.patch(
        f"{base}/master-gain",
        json={
            "working_composition_id": str(working_id),
            "expected_revision": 2,
            "master_gain_db": -3,
        },
        headers={"Idempotency-Key": "api-master"},
    )
    assert master.status_code == 200
    invalid = working_client.patch(
        f"{base}/tracks/{track_id}/mixer",
        json={
            "working_composition_id": str(working_id),
            "expected_revision": 3,
            "gain_db": 2,
            "pan": 1.01,
            "muted": False,
            "solo": False,
        },
        headers={"Idempotency-Key": "api-invalid-pan"},
    )
    assert invalid.status_code == 422
    invalid_master = working_client.patch(
        f"{base}/master-gain",
        json={
            "working_composition_id": str(working_id),
            "expected_revision": 3,
            "master_gain_db": 24.01,
        },
        headers={"Idempotency-Key": "api-invalid-master"},
    )
    assert invalid_master.status_code == 422
    undone = working_client.post(
        f"{base}/history/undo",
        json={"working_composition_id": str(working_id), "expected_revision": 3},
        headers={"Idempotency-Key": "api-undo-master"},
    )
    assert undone.status_code == 200
    assert undone.json()["data"] == {
        "target_type": "WORKING_COMPOSITION",
        "target_id": str(working_id),
        "clip_id": None,
        "completed_revision": 4,
        "replayed": False,
    }


def test_snapshot_checkout_restores_frozen_mixer_state(service, session_factory, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    _create_clip(service, graph, working_id, track_id, 1)
    service.set_track_mixer(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        gain_db=Decimal("2.00"),
        pan=Decimal("0.50"),
        muted=False,
        solo=True,
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="freeze-track",
    )
    service.set_master_gain(
        graph.project_id,
        working_composition_id=working_id,
        master_gain_db=Decimal("-3.00"),
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="freeze-master",
    )
    committed = service.commit(
        graph.project_id,
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="freeze-commit",
    )
    snapshot_id = committed.identities["composition_snapshot_id"]
    service.set_track_mixer(
        graph.project_id,
        working_composition_id=working_id,
        track_id=track_id,
        gain_db=Decimal("0"),
        pan=Decimal("0"),
        muted=True,
        solo=False,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="live-track",
    )
    service.set_master_gain(
        graph.project_id,
        working_composition_id=working_id,
        master_gain_db=Decimal("0"),
        expected_revision=6,
        effective_owner_id=graph.owner_id,
        idempotency_key="live-master",
    )
    service.checkout(
        graph.project_id,
        working_composition_id=working_id,
        composition_snapshot_id=snapshot_id,
        expected_revision=7,
        effective_owner_id=graph.owner_id,
        idempotency_key="restore-frozen",
    )
    with session_factory() as session:
        track = session.get(CompositionTrack, track_id)
        assert (track.gain_db, track.pan, track.muted, track.solo) == (
            Decimal("2.00"),
            Decimal("0.50"),
            False,
            True,
        )
        assert session.get(WorkingComposition, working_id).master_gain_db == Decimal("-3.00")


def test_new_forward_command_invalidates_redo_and_empty_history_is_atomic(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("1.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="branch-gain",
    )
    service.undo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="branch-undo",
    )
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("2.00"),
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="branch-replacement",
    )
    state = service.get_history_state(
        graph.project_id,
        working_composition_id=working_id,
        effective_owner_id=graph.owner_id,
    )
    assert (state["cursor"], state["command_count"], state["can_redo"]) == (1, 1, False)
    service.undo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=5,
        effective_owner_id=graph.owner_id,
        idempotency_key="branch-final-undo",
    )
    with pytest.raises(WorkingCompositionError) as caught:
        service.undo_history(
            graph.project_id,
            working_composition_id=working_id,
            expected_revision=6,
            effective_owner_id=graph.owner_id,
            idempotency_key="empty-undo",
        )
    assert caught.value.code is WorkingCompositionErrorCode.WORKING_HISTORY_EMPTY
    with session_factory() as session:
        assert session.get(WorkingComposition, working_id).revision == 6


def _capture(operation):
    try:
        return operation()
    except WorkingCompositionError as error:
        return error


def _assert_one_revision_conflict(results) -> None:
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, WorkingCompositionError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT


def test_multi_user_different_field_and_different_clip_are_aggregate_conflicts(
    service, session_factory, graph
) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_a = _create_track(service, graph, working_id, 0, key="aggregate-track-a").identities[
        "track_id"
    ]
    track_b = _create_track(service, graph, working_id, 1, key="aggregate-track-b").identities[
        "track_id"
    ]
    clip_a = _create_clip(
        service, graph, working_id, track_a, 2, key="aggregate-clip-a"
    ).identities["clip_id"]
    clip_b = _create_clip(
        service, graph, working_id, track_b, 3, key="aggregate-clip-b"
    ).identities["clip_id"]

    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_a,
        gain_db=Decimal("3.00"),
        expected_revision=4,
        effective_owner_id=graph.owner_id,
        idempotency_key="aggregate-gain-a",
    )
    with pytest.raises(WorkingCompositionError) as different_field:
        service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_a,
            fade_in=Decimal("0.25"),
            fade_out=Decimal("0"),
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="aggregate-fade-stale",
        )
    with pytest.raises(WorkingCompositionError) as different_clip:
        service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_b,
            fade_in=Decimal("0.5"),
            fade_out=Decimal("0"),
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="aggregate-other-clip-stale",
        )
    assert (
        different_field.value.code
        is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    )
    assert (
        different_clip.value.code
        is WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
    )
    state = service.get_history_state(
        graph.project_id,
        working_composition_id=working_id,
        effective_owner_id=graph.owner_id,
    )
    assert (state["revision"], state["cursor"], state["command_count"]) == (5, 1, 1)
    with session_factory() as session:
        assert session.get(CompositionClip, clip_a).fade_in == 0
        assert session.get(CompositionClip, clip_b).fade_in == 0


def test_multi_user_concurrent_undo_and_redo_each_move_cursor_once(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("2.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="cursor-race-gain",
    )

    def undo(key):
        return _capture(
            lambda: service.undo_history(
                graph.project_id,
                working_composition_id=working_id,
                expected_revision=3,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        undo_results = list(executor.map(undo, ("cursor-undo-a", "cursor-undo-b")))
    _assert_one_revision_conflict(undo_results)
    assert (
        service.get_history_state(
            graph.project_id, working_composition_id=working_id, effective_owner_id=graph.owner_id
        )["cursor"]
        == 0
    )

    def redo(key):
        return _capture(
            lambda: service.redo_history(
                graph.project_id,
                working_composition_id=working_id,
                expected_revision=4,
                effective_owner_id=graph.owner_id,
                idempotency_key=key,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        redo_results = list(executor.map(redo, ("cursor-redo-a", "cursor-redo-b")))
    _assert_one_revision_conflict(redo_results)
    state = service.get_history_state(
        graph.project_id, working_composition_id=working_id, effective_owner_id=graph.owner_id
    )
    assert (state["revision"], state["cursor"], state["command_count"]) == (5, 1, 1)


def test_multi_user_forward_vs_undo_allows_exactly_one_operation(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("1.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="forward-undo-seed",
    )
    operations = (
        lambda: service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            fade_in=Decimal("0.25"),
            fade_out=Decimal("0"),
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="forward-undo-forward",
        ),
        lambda: service.undo_history(
            graph.project_id,
            working_composition_id=working_id,
            expected_revision=3,
            effective_owner_id=graph.owner_id,
            idempotency_key="forward-undo-undo",
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda operation: _capture(operation), operations))
    _assert_one_revision_conflict(results)
    state = service.get_history_state(
        graph.project_id, working_composition_id=working_id, effective_owner_id=graph.owner_id
    )
    assert state["revision"] == 4
    assert (state["cursor"], state["command_count"]) in {(0, 1), (2, 2)}


def test_multi_user_forward_vs_redo_allows_exactly_one_operation(service, graph) -> None:
    working_id = _initialize(service, graph).identities["working_composition_id"]
    track_id = _create_track(service, graph, working_id, 0).identities["track_id"]
    clip_id = _create_clip(service, graph, working_id, track_id, 1).identities["clip_id"]
    service.set_clip_gain(
        graph.project_id,
        working_composition_id=working_id,
        clip_id=clip_id,
        gain_db=Decimal("1.00"),
        expected_revision=2,
        effective_owner_id=graph.owner_id,
        idempotency_key="forward-redo-seed",
    )
    service.undo_history(
        graph.project_id,
        working_composition_id=working_id,
        expected_revision=3,
        effective_owner_id=graph.owner_id,
        idempotency_key="forward-redo-undo",
    )
    operations = (
        lambda: service.set_clip_fade(
            graph.project_id,
            working_composition_id=working_id,
            clip_id=clip_id,
            fade_in=Decimal("0.25"),
            fade_out=Decimal("0"),
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="forward-redo-forward",
        ),
        lambda: service.redo_history(
            graph.project_id,
            working_composition_id=working_id,
            expected_revision=4,
            effective_owner_id=graph.owner_id,
            idempotency_key="forward-redo-redo",
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda operation: _capture(operation), operations))
    _assert_one_revision_conflict(results)
    state = service.get_history_state(
        graph.project_id, working_composition_id=working_id, effective_owner_id=graph.owner_id
    )
    assert state["revision"] == 5
    assert (state["cursor"], state["command_count"]) == (1, 1)
