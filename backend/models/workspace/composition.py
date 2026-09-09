"""Composition Snapshot과 Processing 목표 Entity."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base
from backend.models.workspace.identifiers import generate_uuid
from backend.models.workspace.mixins import (
    CreatedAtMixin,
    SoftDeleteMixin,
    TimestampMixin,
)

if TYPE_CHECKING:
    from backend.models.workspace.asset import AssetVersion
    from backend.models.workspace.job import Job
    from backend.models.workspace.workspace import MusicProject


class CompositionSnapshot(CreatedAtMixin, Base):
    __tablename__ = "composition_snapshots"
    __table_args__ = (
        CheckConstraint("snapshot_version >= 1", name="ck_composition_snapshots_positive_version"),
        UniqueConstraint("project_id", "snapshot_version", name="uq_composition_snapshots_version"),
        Index("ix_composition_snapshots_project_created", "project_id", "created_at"),
        Index(
            "uq_composition_snapshots_project_identity",
            "project_id",
            "composition_snapshot_id",
            unique=True,
        ),
    )

    composition_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("music_projects.project_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_chain_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_chains.processing_chain_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mix_settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    master_gain_db: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    provider_versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_manifest_ids: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)

    project: Mapped[MusicProject] = relationship(back_populates="composition_snapshots")
    processing_chain: Mapped[ProcessingChain | None] = relationship(
        back_populates="composition_snapshots"
    )
    items: Mapped[list[SnapshotItem]] = relationship(back_populates="composition_snapshot")
    jobs: Mapped[list[Job]] = relationship(back_populates="composition_snapshot")


class ProjectCompositionSelection(TimestampMixin, Base):
    """Project의 명시적 current CompositionSnapshot 선택 상태."""

    __tablename__ = "project_composition_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["music_projects.project_id"],
            ondelete="CASCADE",
            name="fk_project_composition_selections_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "selected_composition_snapshot_id"],
            [
                "composition_snapshots.project_id",
                "composition_snapshots.composition_snapshot_id",
            ],
            ondelete="RESTRICT",
            name="fk_project_composition_selections_same_project_snapshot",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    selected_composition_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, unique=True
    )


class WorkingComposition(TimestampMixin, Base):
    """Project마다 하나인 mutable composition draft authority."""

    __tablename__ = "working_compositions"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_working_compositions_non_negative_revision"),
        UniqueConstraint("project_id", name="uq_working_compositions_project"),
        ForeignKeyConstraint(
            ["project_id"],
            ["music_projects.project_id"],
            ondelete="RESTRICT",
            name="fk_working_compositions_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "base_composition_snapshot_id"],
            [
                "composition_snapshots.project_id",
                "composition_snapshots.composition_snapshot_id",
            ],
            ondelete="RESTRICT",
            name="fk_working_compositions_same_project_base_snapshot",
        ),
        Index(
            "ix_working_compositions_base_snapshot",
            "base_composition_snapshot_id",
        ),
    )

    working_composition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    base_composition_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    mix_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    master_gain_db: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CompositionTrack(TimestampMixin, SoftDeleteMixin, Base):
    """WorkingComposition 안에서 유지되는 mutable canonical Track."""

    __tablename__ = "composition_tracks"
    __table_args__ = (
        CheckConstraint("track_type = 'audio'", name="ck_composition_tracks_audio_type"),
        CheckConstraint("track_order >= 0", name="ck_composition_tracks_non_negative_order"),
        UniqueConstraint(
            "working_composition_id",
            "track_id",
            name="uq_composition_tracks_working_identity",
        ),
        Index(
            "uq_composition_tracks_active_order",
            "working_composition_id",
            "track_order",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_composition_tracks_active_order",
            "working_composition_id",
            "deleted_at",
            "track_order",
            "track_id",
        ),
    )

    track_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    working_composition_id: Mapped[UUID] = mapped_column(
        ForeignKey("working_compositions.working_composition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    track_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    track_order: Mapped[int] = mapped_column(Integer, nullable=False)
    gain_db: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    pan: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    solo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )


class CompositionClip(TimestampMixin, SoftDeleteMixin, Base):
    """Exact AssetVersion 구간을 배치하는 mutable canonical Clip."""

    __tablename__ = "composition_clips"
    __table_args__ = (
        CheckConstraint("timeline_start >= 0", name="ck_composition_clips_non_negative_start"),
        CheckConstraint("source_in >= 0", name="ck_composition_clips_non_negative_source_in"),
        CheckConstraint("source_duration > 0", name="ck_composition_clips_positive_duration"),
        CheckConstraint("source_out > source_in", name="ck_composition_clips_non_empty_range"),
        CheckConstraint(
            "timeline_duration > 0", name="ck_composition_clips_positive_timeline_duration"
        ),
        CheckConstraint("loop_phase >= 0", name="ck_composition_clips_non_negative_loop_phase"),
        CheckConstraint(
            "(loop_enabled AND loop_phase < source_out - source_in) OR "
            "(NOT loop_enabled AND timeline_duration = source_out - source_in "
            "AND loop_phase = 0)",
            name="ck_composition_clips_loop_geometry",
        ),
        CheckConstraint(
            "source_out <= source_duration",
            name="ck_composition_clips_range_within_source",
        ),
        CheckConstraint(
            "gain_db >= -24.00 AND gain_db <= 24.00",
            name="ck_composition_clips_gain_db_range",
        ),
        CheckConstraint(
            "fade_in >= 0",
            name="ck_composition_clips_fade_in_non_negative",
        ),
        CheckConstraint(
            "fade_out >= 0 AND fade_in + fade_out <= timeline_duration",
            name="ck_composition_clips_fade_range",
        ),
        UniqueConstraint(
            "working_composition_id",
            "clip_id",
            name="uq_composition_clips_working_identity",
        ),
        ForeignKeyConstraint(
            ["working_composition_id"],
            ["working_compositions.working_composition_id"],
            ondelete="RESTRICT",
            name="fk_composition_clips_working_composition",
        ),
        ForeignKeyConstraint(
            ["working_composition_id", "track_id"],
            [
                "composition_tracks.working_composition_id",
                "composition_tracks.track_id",
            ],
            ondelete="RESTRICT",
            name="fk_composition_clips_same_working_track",
        ),
        ForeignKeyConstraint(
            ["working_composition_id", "split_from_clip_id"],
            ["composition_clips.working_composition_id", "composition_clips.clip_id"],
            ondelete="RESTRICT",
            name="fk_composition_clips_same_working_split_parent",
        ),
        Index(
            "ix_composition_clips_active_timeline",
            "track_id",
            "deleted_at",
            "timeline_start",
            "clip_id",
        ),
        Index(
            "ix_composition_clips_source_asset_version",
            "source_asset_version_id",
        ),
        Index("ix_composition_clips_split_parent", "split_from_clip_id"),
    )

    clip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    working_composition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    track_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.asset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    timeline_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_in: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_out: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_duration: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timeline_duration: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda context: (
            context.get_current_parameters()["source_out"]
            - context.get_current_parameters()["source_in"]
        ),
    )
    loop_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    loop_phase: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    gain_db: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    fade_in: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    fade_out: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    split_from_clip_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class CompositionSnapshotTrack(Base):
    """Snapshot에 고정한 Track arrangement와 canonical lineage."""

    __tablename__ = "composition_snapshot_tracks"
    __table_args__ = (
        CheckConstraint("track_type = 'audio'", name="ck_composition_snapshot_tracks_audio_type"),
        CheckConstraint(
            "track_order >= 0",
            name="ck_composition_snapshot_tracks_non_negative_order",
        ),
        UniqueConstraint(
            "composition_snapshot_id",
            "snapshot_track_id",
            name="uq_composition_snapshot_tracks_snapshot_identity",
        ),
        UniqueConstraint(
            "composition_snapshot_id",
            "canonical_track_id",
            name="uq_composition_snapshot_tracks_canonical_identity",
        ),
        UniqueConstraint(
            "composition_snapshot_id",
            "track_order",
            name="uq_composition_snapshot_tracks_order",
        ),
        Index(
            "ix_composition_snapshot_tracks_order",
            "composition_snapshot_id",
            "track_order",
            "snapshot_track_id",
        ),
    )

    snapshot_track_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    composition_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("composition_snapshots.composition_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    canonical_track_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    track_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    track_order: Mapped[int] = mapped_column(Integer, nullable=False)
    gain_db: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    pan: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    solo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )


class CompositionSnapshotClip(Base):
    """Snapshot에 고정한 Clip arrangement와 exact source lineage."""

    __tablename__ = "composition_snapshot_clips"
    __table_args__ = (
        CheckConstraint(
            "timeline_start >= 0",
            name="ck_composition_snapshot_clips_non_negative_start",
        ),
        CheckConstraint(
            "source_in >= 0",
            name="ck_composition_snapshot_clips_non_negative_source_in",
        ),
        CheckConstraint(
            "source_duration > 0",
            name="ck_composition_snapshot_clips_positive_duration",
        ),
        CheckConstraint(
            "source_out > source_in",
            name="ck_composition_snapshot_clips_non_empty_range",
        ),
        CheckConstraint(
            "timeline_duration > 0", name="ck_composition_snapshot_clips_positive_timeline_duration"
        ),
        CheckConstraint(
            "loop_phase >= 0", name="ck_composition_snapshot_clips_non_negative_loop_phase"
        ),
        CheckConstraint(
            "(loop_enabled AND loop_phase < source_out - source_in) OR "
            "(NOT loop_enabled AND timeline_duration = source_out - source_in "
            "AND loop_phase = 0)",
            name="ck_composition_snapshot_clips_loop_geometry",
        ),
        CheckConstraint(
            "source_out <= source_duration",
            name="ck_composition_snapshot_clips_range_within_source",
        ),
        CheckConstraint(
            "gain_db >= -24.00 AND gain_db <= 24.00",
            name="ck_composition_snapshot_clips_gain_db_range",
        ),
        CheckConstraint(
            "fade_in >= 0",
            name="ck_composition_snapshot_clips_fade_in_non_negative",
        ),
        CheckConstraint(
            "fade_out >= 0 AND fade_in + fade_out <= timeline_duration",
            name="ck_composition_snapshot_clips_fade_range",
        ),
        UniqueConstraint(
            "composition_snapshot_id",
            "canonical_clip_id",
            name="uq_composition_snapshot_clips_canonical_identity",
        ),
        ForeignKeyConstraint(
            ["composition_snapshot_id", "snapshot_track_id"],
            [
                "composition_snapshot_tracks.composition_snapshot_id",
                "composition_snapshot_tracks.snapshot_track_id",
            ],
            ondelete="RESTRICT",
            name="fk_composition_snapshot_clips_same_snapshot_track",
        ),
        Index(
            "ix_composition_snapshot_clips_timeline",
            "snapshot_track_id",
            "timeline_start",
            "snapshot_clip_id",
        ),
        Index(
            "ix_composition_snapshot_clips_source_asset_version",
            "source_asset_version_id",
        ),
    )

    snapshot_clip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    composition_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("composition_snapshots.composition_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_track_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    canonical_clip_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.asset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    timeline_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_in: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_out: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_duration: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timeline_duration: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda context: (
            context.get_current_parameters()["source_out"]
            - context.get_current_parameters()["source_in"]
        ),
    )
    loop_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    loop_phase: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    gain_db: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    fade_in: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    fade_out: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    split_from_clip_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class SnapshotItem(CreatedAtMixin, Base):
    __tablename__ = "snapshot_items"
    __table_args__ = (
        UniqueConstraint(
            "composition_snapshot_id",
            "item_role",
            "sort_order",
            name="uq_snapshot_items_role_order",
        ),
        UniqueConstraint(
            "composition_snapshot_id",
            "asset_version_id",
            "item_role",
            name="uq_snapshot_items_version_role",
        ),
    )

    snapshot_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    composition_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("composition_snapshots.composition_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.asset_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    item_role: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    composition_snapshot: Mapped[CompositionSnapshot] = relationship(back_populates="items")
    asset_version: Mapped[AssetVersion] = relationship(back_populates="snapshot_items")


class ProcessingChain(CreatedAtMixin, Base):
    __tablename__ = "processing_chains"
    __table_args__ = (
        UniqueConstraint("name", "chain_version", name="uq_processing_chains_version"),
    )

    processing_chain_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    chain_version: Mapped[str] = mapped_column(String, nullable=False)
    chain_checksum: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)

    steps: Mapped[list[ProcessingStep]] = relationship(back_populates="processing_chain")
    asset_versions: Mapped[list[AssetVersion]] = relationship(back_populates="processing_chain")
    composition_snapshots: Mapped[list[CompositionSnapshot]] = relationship(
        back_populates="processing_chain"
    )


class ProcessingStep(CreatedAtMixin, Base):
    __tablename__ = "processing_steps"
    __table_args__ = (
        CheckConstraint("step_order >= 1", name="ck_processing_steps_positive_order"),
        UniqueConstraint(
            "processing_chain_id",
            "step_order",
            name="uq_processing_steps_chain_order",
        ),
    )

    processing_step_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    processing_chain_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_chains.processing_chain_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    processing_chain: Mapped[ProcessingChain] = relationship(back_populates="steps")
