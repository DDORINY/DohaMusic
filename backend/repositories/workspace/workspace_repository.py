"""Workspace, MusicProject와 ProjectAsset persistence operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from backend.models.workspace.mixins import utc_now
from backend.models.workspace.workspace import MusicProject, ProjectAsset, Workspace


class WorkspaceRepository:
    """Workspace aggregate를 commit 없이 현재 transaction에 반영한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_workspace(self, workspace: Workspace) -> Workspace:
        self.session.add(workspace)
        self.session.flush()
        return workspace

    def get_workspace(
        self, workspace_id: UUID, *, include_deleted: bool = False
    ) -> Workspace | None:
        statement = select(Workspace).where(Workspace.workspace_id == workspace_id)
        if not include_deleted:
            statement = statement.where(Workspace.deleted_at.is_(None))
        return self.session.scalar(statement)

    def get_workspace_for_owner(
        self,
        workspace_id: UUID,
        owner_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> Workspace | None:
        statement = select(Workspace).where(
            Workspace.workspace_id == workspace_id,
            Workspace.owner_id == owner_id,
        )
        if not include_deleted:
            statement = statement.where(Workspace.deleted_at.is_(None))
        return self.session.scalar(statement)

    def list_workspaces(
        self,
        *,
        owner_id: UUID | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Workspace]:
        statement = select(Workspace)
        if owner_id is not None:
            statement = statement.where(Workspace.owner_id == owner_id)
        if not include_deleted:
            statement = statement.where(Workspace.deleted_at.is_(None))
        statement = (
            statement.order_by(Workspace.created_at.desc(), Workspace.workspace_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_workspaces_after(
        self,
        *,
        owner_id: UUID | None = None,
        last_created_at: datetime | None = None,
        last_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Workspace]:
        """외부 API cursor를 위한 안정적인 DESC keyset 조회."""

        _validate_keyset_position(last_created_at, last_id)
        statement = select(Workspace).where(Workspace.deleted_at.is_(None))
        if owner_id is not None:
            statement = statement.where(Workspace.owner_id == owner_id)
        if last_created_at is not None and last_id is not None:
            statement = statement.where(
                or_(
                    Workspace.created_at < last_created_at,
                    and_(
                        Workspace.created_at == last_created_at,
                        Workspace.workspace_id < last_id,
                    ),
                )
            )
        statement = statement.order_by(
            Workspace.created_at.desc(), Workspace.workspace_id.desc()
        ).limit(limit)
        return list(self.session.scalars(statement))

    def workspace_name_exists(
        self, owner_id: UUID, name: str, *, include_deleted: bool = False
    ) -> bool:
        statement = select(Workspace.workspace_id).where(
            Workspace.owner_id == owner_id,
            Workspace.name == name,
        )
        if not include_deleted:
            statement = statement.where(Workspace.deleted_at.is_(None))
        return self.session.scalar(statement.limit(1)) is not None

    def soft_delete_workspace(self, workspace: Workspace) -> Workspace:
        workspace.deleted_at = utc_now()
        self.session.flush()
        return workspace

    def add_project(self, project: MusicProject) -> MusicProject:
        self.session.add(project)
        self.session.flush()
        return project

    def get_project(
        self, project_id: UUID, *, include_deleted: bool = False
    ) -> MusicProject | None:
        statement = select(MusicProject).where(MusicProject.project_id == project_id)
        if not include_deleted:
            statement = statement.where(MusicProject.deleted_at.is_(None))
        return self.session.scalar(statement)

    def assign_export_project_asset_if_unset(
        self, project_id: UUID, project_asset_id: UUID
    ) -> MusicProject | None:
        statement = (
            update(MusicProject)
            .where(
                MusicProject.project_id == project_id,
                MusicProject.export_project_asset_id.is_(None),
                MusicProject.deleted_at.is_(None),
            )
            .values(export_project_asset_id=project_asset_id)
            .returning(MusicProject)
        )
        return self.session.scalars(statement).one_or_none()

    def list_projects(
        self,
        workspace_id: UUID,
        *,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MusicProject]:
        statement = select(MusicProject).where(MusicProject.workspace_id == workspace_id)
        if not include_deleted:
            statement = statement.where(MusicProject.deleted_at.is_(None))
        statement = (
            statement.order_by(MusicProject.created_at.desc(), MusicProject.project_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_projects_after(
        self,
        workspace_id: UUID,
        *,
        last_created_at: datetime | None = None,
        last_id: UUID | None = None,
        limit: int = 100,
    ) -> list[MusicProject]:
        """Workspace filter를 고정한 Project DESC keyset 조회."""

        _validate_keyset_position(last_created_at, last_id)
        statement = select(MusicProject).where(
            MusicProject.workspace_id == workspace_id,
            MusicProject.deleted_at.is_(None),
        )
        if last_created_at is not None and last_id is not None:
            statement = statement.where(
                or_(
                    MusicProject.created_at < last_created_at,
                    and_(
                        MusicProject.created_at == last_created_at,
                        MusicProject.project_id < last_id,
                    ),
                )
            )
        statement = statement.order_by(
            MusicProject.created_at.desc(), MusicProject.project_id.desc()
        ).limit(limit)
        return list(self.session.scalars(statement))

    def project_title_exists(
        self,
        workspace_id: UUID,
        title: str,
        *,
        include_deleted: bool = False,
    ) -> bool:
        statement = select(MusicProject.project_id).where(
            MusicProject.workspace_id == workspace_id,
            MusicProject.title == title,
        )
        if not include_deleted:
            statement = statement.where(MusicProject.deleted_at.is_(None))
        return self.session.scalar(statement.limit(1)) is not None

    def soft_delete_project(self, project: MusicProject) -> MusicProject:
        project.deleted_at = utc_now()
        self.session.flush()
        return project

    def add_project_asset(self, project_asset: ProjectAsset) -> ProjectAsset:
        self.session.add(project_asset)
        self.session.flush()
        return project_asset

    def get_project_asset(
        self, project_asset_id: UUID, *, include_deleted: bool = False
    ) -> ProjectAsset | None:
        statement = select(ProjectAsset).where(ProjectAsset.project_asset_id == project_asset_id)
        if not include_deleted:
            statement = statement.where(ProjectAsset.deleted_at.is_(None))
        return self.session.scalar(statement)

    def list_project_assets(
        self,
        project_id: UUID,
        *,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectAsset]:
        statement = select(ProjectAsset).where(ProjectAsset.project_id == project_id)
        if not include_deleted:
            statement = statement.where(ProjectAsset.deleted_at.is_(None))
        statement = (
            statement.order_by(ProjectAsset.display_order, ProjectAsset.project_asset_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_project_assets_after(
        self,
        project_id: UUID,
        *,
        last_display_order: int | None = None,
        last_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ProjectAsset]:
        """Project에 연결된 활성 Asset을 display order ASC keyset으로 조회한다."""

        _validate_display_order_position(last_display_order, last_id)
        statement = select(ProjectAsset).where(
            ProjectAsset.project_id == project_id,
            ProjectAsset.deleted_at.is_(None),
        )
        if last_display_order is not None and last_id is not None:
            statement = statement.where(
                or_(
                    ProjectAsset.display_order > last_display_order,
                    and_(
                        ProjectAsset.display_order == last_display_order,
                        ProjectAsset.project_asset_id > last_id,
                    ),
                )
            )
        statement = statement.order_by(
            ProjectAsset.display_order.asc(), ProjectAsset.project_asset_id.asc()
        ).limit(limit)
        return list(self.session.scalars(statement))

    def project_asset_exists(
        self,
        project_id: UUID,
        asset_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> bool:
        statement = select(ProjectAsset.project_asset_id).where(
            ProjectAsset.project_id == project_id,
            ProjectAsset.asset_id == asset_id,
        )
        if not include_deleted:
            statement = statement.where(ProjectAsset.deleted_at.is_(None))
        return self.session.scalar(statement.limit(1)) is not None

    def find_project_asset(
        self,
        project_id: UUID,
        asset_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> ProjectAsset | None:
        statement = select(ProjectAsset).where(
            ProjectAsset.project_id == project_id,
            ProjectAsset.asset_id == asset_id,
        )
        if not include_deleted:
            statement = statement.where(ProjectAsset.deleted_at.is_(None))
        return self.session.scalar(statement.limit(1))

    def remove_project_asset(self, project_asset: ProjectAsset) -> ProjectAsset:
        project_asset.deleted_at = utc_now()
        self.session.flush()
        return project_asset

    def set_project_asset_display_order(
        self, project_asset: ProjectAsset, display_order: int
    ) -> ProjectAsset:
        project_asset.display_order = display_order
        self.session.flush()
        return project_asset


def _validate_keyset_position(last_created_at: datetime | None, last_id: UUID | None) -> None:
    if (last_created_at is None) != (last_id is None):
        raise ValueError("keyset position requires both created_at and id")


def _validate_display_order_position(last_display_order: int | None, last_id: UUID | None) -> None:
    if (last_display_order is None) != (last_id is None):
        raise ValueError("keyset position requires both display_order and id")
    if last_display_order is not None and (
        type(last_display_order) is not int or last_display_order < 0
    ):
        raise ValueError("display_order must be a non-negative integer")
