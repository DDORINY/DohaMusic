"""Backend-authoritative persistent WorkingComposition history tests."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from backend.models.workspace import CompositionClip, WorkingComposition
from backend.services.workspace import WorkingCompositionError, WorkingCompositionErrorCode
from backend.tests.test_working_composition_service import (
    _create_clip,
    _create_track,
    _initialize,
    graph,
    schema_template,
    service,
    session_factory,
)

__all__ = ["graph", "schema_template", "service", "session_factory"]


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
