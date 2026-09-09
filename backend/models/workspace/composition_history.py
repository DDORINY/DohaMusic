"""Persistent product undo/redo history for a WorkingComposition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base
from backend.models.workspace.identifiers import generate_uuid
from backend.models.workspace.mixins import CreatedAtMixin, TimestampMixin


class WorkingCompositionHistoryState(TimestampMixin, Base):
    __tablename__ = "working_composition_history_states"
    __table_args__ = (CheckConstraint("cursor >= 0", name="ck_working_history_state_cursor"),)

    working_composition_id: Mapped[UUID] = mapped_column(
        ForeignKey("working_compositions.working_composition_id", ondelete="CASCADE"),
        primary_key=True,
    )
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkingCompositionHistoryEntry(CreatedAtMixin, Base):
    __tablename__ = "working_composition_history_entries"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_working_history_entry_sequence"),
        UniqueConstraint("working_composition_id", "sequence", name="uq_working_history_sequence"),
        Index("ix_working_history_cursor", "working_composition_id", "sequence"),
    )

    history_entry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    working_composition_id: Mapped[UUID] = mapped_column(
        ForeignKey("working_compositions.working_composition_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    clip_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    before_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
