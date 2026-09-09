"""Persistence operations for the product undo/redo journal."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models.workspace.composition_history import (
    WorkingCompositionHistoryEntry,
    WorkingCompositionHistoryState,
)


class CompositionHistoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def state(self, working_composition_id: UUID) -> WorkingCompositionHistoryState:
        state = self.session.get(WorkingCompositionHistoryState, working_composition_id)
        if state is None:
            state = WorkingCompositionHistoryState(
                working_composition_id=working_composition_id, cursor=0
            )
            self.session.add(state)
            self.session.flush()
        return state

    def read_state(self, working_composition_id: UUID) -> tuple[int, int]:
        state = self.session.get(WorkingCompositionHistoryState, working_composition_id)
        cursor = 0 if state is None else state.cursor
        maximum = self.session.scalar(
            select(WorkingCompositionHistoryEntry.sequence)
            .where(WorkingCompositionHistoryEntry.working_composition_id == working_composition_id)
            .order_by(WorkingCompositionHistoryEntry.sequence.desc())
            .limit(1)
        )
        return cursor, maximum or 0

    def append(
        self,
        *,
        working_composition_id: UUID,
        command_type: str,
        clip_id: UUID | None = None,
        target_type: str = "CLIP",
        target_id: UUID | None = None,
        before_state: Mapping[str, object],
        after_state: Mapping[str, object],
    ) -> None:
        command_targets = {
            "CLIP_GAIN": "CLIP",
            "CLIP_FADE": "CLIP",
            "CLIP_LOOP": "CLIP",
            "TRACK_MIXER": "TRACK",
            "MASTER_GAIN": "WORKING_COMPOSITION",
        }
        if command_targets.get(command_type) != target_type:
            raise ValueError("WORKING_HISTORY_TARGET_INVALID")
        resolved_target_id = target_id or clip_id
        if resolved_target_id is None:
            raise ValueError("WORKING_HISTORY_TARGET_INVALID")
        if target_type == "CLIP" and clip_id != resolved_target_id:
            raise ValueError("WORKING_HISTORY_TARGET_INVALID")
        if target_type != "CLIP" and clip_id is not None:
            raise ValueError("WORKING_HISTORY_TARGET_INVALID")
        state = self.state(working_composition_id)
        self.session.execute(
            delete(WorkingCompositionHistoryEntry).where(
                WorkingCompositionHistoryEntry.working_composition_id == working_composition_id,
                WorkingCompositionHistoryEntry.sequence > state.cursor,
            )
        )
        state.cursor += 1
        self.session.add(
            WorkingCompositionHistoryEntry(
                working_composition_id=working_composition_id,
                sequence=state.cursor,
                command_type=command_type,
                target_type=target_type,
                target_id=resolved_target_id,
                clip_id=clip_id,
                before_state=dict(before_state),
                after_state=dict(after_state),
            )
        )
        self.session.flush()

    def current_undo(self, working_composition_id: UUID) -> WorkingCompositionHistoryEntry | None:
        state = self.state(working_composition_id)
        if state.cursor == 0:
            return None
        return self.session.scalar(
            select(WorkingCompositionHistoryEntry).where(
                WorkingCompositionHistoryEntry.working_composition_id == working_composition_id,
                WorkingCompositionHistoryEntry.sequence == state.cursor,
            )
        )

    def current_redo(self, working_composition_id: UUID) -> WorkingCompositionHistoryEntry | None:
        state = self.state(working_composition_id)
        return self.session.scalar(
            select(WorkingCompositionHistoryEntry).where(
                WorkingCompositionHistoryEntry.working_composition_id == working_composition_id,
                WorkingCompositionHistoryEntry.sequence == state.cursor + 1,
            )
        )

    def move_cursor(self, working_composition_id: UUID, delta: int) -> None:
        state = self.state(working_composition_id)
        state.cursor += delta
        self.session.flush()

    def clear(self, working_composition_id: UUID) -> None:
        self.session.execute(
            delete(WorkingCompositionHistoryEntry).where(
                WorkingCompositionHistoryEntry.working_composition_id == working_composition_id
            )
        )
        state = self.state(working_composition_id)
        state.cursor = 0
        self.session.flush()
