"""Workspace, MusicProject와 ProjectAsset 목표 Entity."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
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
    from backend.models.workspace.asset import Asset
    from backend.models.workspace.collaboration import (
        Favorite,
        History,
        RecordingEnrollment,
    )
    from backend.models.workspace.composition import CompositionSnapshot
    from backend.models.workspace.job import Job


class Workspace(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        Index(
            "ix_workspaces_active_keyset",
            "deleted_at",
            "created_at",
            "workspace_id",
        ),
        Index(
            "ix_workspaces_owner_active_keyset",
            "owner_id",
            "deleted_at",
            "created_at",
            "workspace_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String, nullable=False, index=True)

    projects: Mapped[list[MusicProject]] = relationship(back_populates="workspace")
    assets: Mapped[list[Asset]] = relationship(back_populates="workspace")
    recording_enrollments: Mapped[list[RecordingEnrollment]] = relationship(
        back_populates="workspace"
    )
    favorites: Mapped[list[Favorite]] = relationship(back_populates="workspace")
    history_entries: Mapped[list[History]] = relationship(back_populates="workspace")


class MusicProject(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "music_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "export_project_asset_id"],
            ["project_assets.project_id", "project_assets.project_asset_id"],
            name="fk_music_projects_export_project_asset",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "export_project_asset_id",
            name="uq_music_projects_export_project_asset",
        ),
        Index(
            "ix_music_projects_workspace_active_keyset",
            "workspace_id",
            "deleted_at",
            "created_at",
            "project_id",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    export_project_asset_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="projects")
    project_assets: Mapped[list[ProjectAsset]] = relationship(
        back_populates="project",
        foreign_keys="ProjectAsset.project_id",
    )
    export_project_asset: Mapped[ProjectAsset | None] = relationship(
        back_populates="export_owner_project",
        foreign_keys=[project_id, export_project_asset_id],
        primaryjoin=(
            "and_(MusicProject.project_id == ProjectAsset.project_id, "
            "MusicProject.export_project_asset_id == ProjectAsset.project_asset_id)"
        ),
        viewonly=True,
    )
    composition_snapshots: Mapped[list[CompositionSnapshot]] = relationship(
        back_populates="project"
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="project")


class ProjectAsset(CreatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "project_assets"
    __table_args__ = (
        UniqueConstraint("project_id", "asset_id", name="uq_project_assets_project_asset"),
        UniqueConstraint(
            "project_id",
            "project_asset_id",
            name="uq_project_assets_project_identity",
        ),
        Index(
            "ix_project_assets_active_keyset",
            "project_id",
            "display_order",
            "project_asset_id",
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    project_asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("music_projects.project_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.asset_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[MusicProject] = relationship(
        back_populates="project_assets",
        foreign_keys=[project_id],
    )
    export_owner_project: Mapped[MusicProject | None] = relationship(
        back_populates="export_project_asset",
        foreign_keys="[MusicProject.project_id, MusicProject.export_project_asset_id]",
        primaryjoin=(
            "and_(MusicProject.project_id == ProjectAsset.project_id, "
            "MusicProject.export_project_asset_id == ProjectAsset.project_asset_id)"
        ),
        viewonly=True,
    )
    asset: Mapped[Asset] = relationship(back_populates="project_assets")
