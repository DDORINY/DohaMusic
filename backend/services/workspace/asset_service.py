"""Asset, 불변 AssetVersion과 Artifact application use case."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.cursor_pagination import CURSOR_SORT, CursorCodec, filter_fingerprint
from backend.core.exceptions import (
    ApplicationValidationError,
    CursorConfigurationError,
    InvalidLimitError,
    InvalidStateError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from backend.models.workspace import (
    Artifact,
    Asset,
    AssetRelation,
    AssetType,
    AssetVersion,
)
from backend.repositories.workspace import (
    AssetRepository,
    CompositionRepository,
    WorkspaceRepository,
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ApplicationValidationError(f"{field_name}은(는) 비어 있을 수 없습니다.")
    return normalized


@dataclass(frozen=True, slots=True)
class AssetCursorPage:
    items: tuple[Asset, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


class AssetService:
    """Asset aggregate의 transaction과 불변 Version 생성 규칙을 소유한다."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cursor_codec: CursorCodec | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cursor_codec = cursor_codec

    def create_asset(
        self,
        *,
        owner_id: UUID,
        asset_type: AssetType,
        workspace_id: UUID | None = None,
        lifecycle_status: str = "active",
    ) -> Asset:
        normalized_status = _required_text(lifecycle_status, "Asset 상태")
        try:
            with self.session_factory() as session, session.begin():
                if (
                    workspace_id is not None
                    and WorkspaceRepository(session).get_workspace(workspace_id) is None
                ):
                    raise ResourceNotFoundError("Workspace")
                asset = AssetRepository(session).add_asset(
                    Asset(
                        workspace_id=workspace_id,
                        owner_id=owner_id,
                        asset_type=asset_type,
                        lifecycle_status=normalized_status,
                    )
                )
            return asset
        except IntegrityError:
            raise ResourceConflictError("Asset") from None

    def get_asset(self, asset_id: UUID) -> Asset:
        with self.session_factory() as session:
            asset = AssetRepository(session).get_asset(asset_id)
            if asset is None:
                raise ResourceNotFoundError("Asset")
            return asset

    def list_assets(
        self,
        *,
        owner_id: UUID | None = None,
        workspace_id: UUID | None = None,
        asset_type: AssetType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Asset]:
        with self.session_factory() as session:
            repository = AssetRepository(session)
            if workspace_id is not None:
                return repository.list_workspace_assets(
                    workspace_id,
                    asset_type=asset_type,
                    limit=limit,
                    offset=offset,
                )
            return repository.list_assets(
                owner_id=owner_id,
                asset_type=asset_type,
                limit=limit,
                offset=offset,
            )

    def list_asset_page(
        self,
        *,
        owner_id: UUID,
        workspace_id: UUID | None = None,
        asset_type: AssetType | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AssetCursorPage:
        """신뢰된 Owner scope의 활성 Asset을 DESC cursor page로 조회한다."""

        _validate_page_limit(limit)
        codec = self._require_cursor_codec()
        filter_hash = filter_fingerprint(
            {
                "asset_type": asset_type.value if asset_type is not None else None,
                "include_deleted": False,
                "owner_id": str(owner_id),
                "sort": CURSOR_SORT,
                "workspace_id": str(workspace_id) if workspace_id is not None else None,
            }
        )
        position = (
            codec.decode(
                cursor,
                expected_resource="asset",
                expected_filter_hash=filter_hash,
                expected_limit=limit,
            )
            if cursor is not None
            else None
        )
        with self.session_factory() as session:
            if workspace_id is not None:
                workspace = WorkspaceRepository(session).get_workspace(workspace_id)
                if workspace is None or workspace.owner_id != owner_id:
                    raise ResourceNotFoundError("Workspace")
            rows = AssetRepository(session).list_assets_after(
                owner_id=owner_id,
                workspace_id=workspace_id,
                asset_type=asset_type,
                last_created_at=(position.last_created_at if position else None),
                last_id=(position.last_id if position else None),
                limit=limit + 1,
            )
        has_more = len(rows) > limit
        items = tuple(rows[:limit])
        next_cursor = None
        if has_more:
            last_item = items[-1]
            next_cursor = codec.encode(
                resource="asset",
                last_created_at=last_item.created_at,
                last_id=last_item.asset_id,
                filter_hash=filter_hash,
                limit=limit,
            )
        return AssetCursorPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
        )

    def _require_cursor_codec(self) -> CursorCodec:
        if self.cursor_codec is None:
            raise CursorConfigurationError()
        return self.cursor_codec

    def update_asset_metadata(self, asset_id: UUID, *, lifecycle_status: str) -> Asset:
        normalized_status = _required_text(lifecycle_status, "Asset 상태")
        with self.session_factory() as session, session.begin():
            repository = AssetRepository(session)
            asset = repository.get_asset(asset_id)
            if asset is None:
                raise ResourceNotFoundError("Asset")
            asset.lifecycle_status = normalized_status
            session.flush()
        return asset

    def select_asset_version(self, asset_id: UUID, asset_version_id: UUID | None) -> Asset:
        with self.session_factory() as session, session.begin():
            repository = AssetRepository(session)
            asset = repository.get_asset(asset_id)
            if asset is None:
                raise ResourceNotFoundError("Asset")
            if asset_version_id is not None:
                version = repository.get_asset_version(asset_version_id)
                if version is None or version.asset_id != asset_id:
                    raise ApplicationValidationError("선택 Version은 같은 Asset에 속해야 합니다.")
            asset.selected_asset_version_id = asset_version_id
            session.flush()
        return asset

    def delete_asset(self, asset_id: UUID) -> Asset:
        with self.session_factory() as session, session.begin():
            repository = AssetRepository(session)
            asset = repository.get_asset(asset_id)
            if asset is None:
                raise ResourceNotFoundError("Asset")
            if any(
                item.deleted_at is None
                and item.project.export_project_asset_id == item.project_asset_id
                for item in asset.project_assets
            ):
                raise InvalidStateError("Project Export Asset")
            repository.soft_delete_asset(asset)
        return asset

    def create_asset_version(
        self,
        *,
        asset_id: UUID,
        version_origin: str,
        settings_snapshot: dict[str, Any],
        created_by: UUID,
        version_number: int | None = None,
        parent_asset_version_id: UUID | None = None,
        processing_chain_id: UUID | None = None,
        provider_id: str | None = None,
        model_manifest_id: str | None = None,
    ) -> AssetVersion:
        normalized_origin = _required_text(version_origin, "Version 생성 원인")
        try:
            with self.session_factory() as session, session.begin():
                repository = AssetRepository(session)
                asset = repository.get_asset(asset_id)
                if asset is None:
                    raise ResourceNotFoundError("Asset")
                if parent_asset_version_id is not None:
                    parent = repository.get_asset_version(parent_asset_version_id)
                    if parent is None:
                        raise ResourceNotFoundError("상위 AssetVersion")
                    if parent.asset_id != asset_id:
                        raise ApplicationValidationError(
                            "상위 Version은 같은 Asset에 속해야 합니다."
                        )
                if (
                    processing_chain_id is not None
                    and CompositionRepository(session).get_processing_chain(processing_chain_id)
                    is None
                ):
                    raise ResourceNotFoundError("ProcessingChain")
                resolved_number = version_number
                if resolved_number is None:
                    latest = repository.get_latest_asset_version(asset_id)
                    resolved_number = 1 if latest is None else latest.version_number + 1
                if resolved_number < 1:
                    raise ApplicationValidationError("version_number는 1 이상이어야 합니다.")
                if repository.version_number_exists(asset_id, resolved_number):
                    raise ResourceConflictError("AssetVersion 번호")
                version = repository.add_asset_version(
                    AssetVersion(
                        asset_id=asset_id,
                        version_number=resolved_number,
                        version_origin=normalized_origin,
                        parent_asset_version_id=parent_asset_version_id,
                        processing_chain_id=processing_chain_id,
                        provider_id=(provider_id.strip() if provider_id else None),
                        model_manifest_id=(
                            model_manifest_id.strip() if model_manifest_id else None
                        ),
                        settings_snapshot=dict(settings_snapshot),
                        created_by=created_by,
                    )
                )
            return version
        except IntegrityError:
            raise ResourceConflictError("AssetVersion") from None

    def get_asset_version(self, asset_version_id: UUID) -> AssetVersion:
        with self.session_factory() as session:
            version = AssetRepository(session).get_asset_version(asset_version_id)
            if version is None:
                raise ResourceNotFoundError("AssetVersion")
            return version

    def list_asset_versions(
        self,
        asset_id: UUID,
        *,
        limit: int | None = 100,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[AssetVersion]:
        with self.session_factory() as session:
            repository = AssetRepository(session)
            if repository.get_asset(asset_id) is None:
                raise ResourceNotFoundError("Asset")
            return repository.list_asset_versions(
                asset_id,
                limit=limit,
                offset=offset,
                newest_first=newest_first,
            )

    def get_latest_asset_version(self, asset_id: UUID) -> AssetVersion | None:
        with self.session_factory() as session:
            repository = AssetRepository(session)
            if repository.get_asset(asset_id) is None:
                raise ResourceNotFoundError("Asset")
            return repository.get_latest_asset_version(asset_id)

    def register_artifact(
        self,
        *,
        asset_version_id: UUID,
        artifact_kind: str,
        media_type: str,
        size_bytes: int,
        artifact_checksum: str,
        producer_type: str,
        retention_status: str,
        checksum_algorithm: str = "sha256",
        producer_id: str | None = None,
        run_id: str | None = None,
    ) -> Artifact:
        normalized_algorithm = checksum_algorithm.strip().lower()
        normalized_checksum = artifact_checksum.strip().lower()
        if normalized_algorithm != "sha256" or not SHA256_PATTERN.fullmatch(normalized_checksum):
            raise ApplicationValidationError("Artifact checksum은 sha256 64자리 16진수여야 합니다.")
        if size_bytes < 0:
            raise ApplicationValidationError("Artifact size는 0 이상이어야 합니다.")
        try:
            with self.session_factory() as session, session.begin():
                repository = AssetRepository(session)
                if repository.get_asset_version(asset_version_id) is None:
                    raise ResourceNotFoundError("AssetVersion")
                artifact = repository.add_artifact(
                    Artifact(
                        asset_version_id=asset_version_id,
                        artifact_kind=_required_text(artifact_kind, "Artifact 유형"),
                        media_type=_required_text(media_type, "Media type"),
                        size_bytes=size_bytes,
                        checksum_algorithm=normalized_algorithm,
                        artifact_checksum=normalized_checksum,
                        producer_type=_required_text(producer_type, "생성 주체 유형"),
                        producer_id=producer_id.strip() if producer_id else None,
                        run_id=run_id.strip() if run_id else None,
                        retention_status=_required_text(retention_status, "Artifact 보존 상태"),
                    )
                )
            return artifact
        except IntegrityError:
            raise ResourceConflictError("Artifact") from None

    def get_artifact(self, artifact_id: UUID) -> Artifact:
        with self.session_factory() as session:
            artifact = AssetRepository(session).get_artifact(artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("Artifact")
            return artifact

    def list_artifacts(
        self, asset_version_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Artifact]:
        with self.session_factory() as session:
            repository = AssetRepository(session)
            if repository.get_asset_version(asset_version_id) is None:
                raise ResourceNotFoundError("AssetVersion")
            return repository.list_version_artifacts(asset_version_id, limit=limit, offset=offset)

    def create_asset_relation(
        self,
        *,
        relation_type: str,
        source_asset_id: UUID | None = None,
        target_asset_id: UUID | None = None,
        source_asset_version_id: UUID | None = None,
        target_asset_version_id: UUID | None = None,
    ) -> AssetRelation:
        asset_pair = source_asset_id is not None and target_asset_id is not None
        version_pair = source_asset_version_id is not None and target_asset_version_id is not None
        if asset_pair == version_pair:
            raise ApplicationValidationError(
                "Asset 쌍 또는 AssetVersion 쌍 중 정확히 하나가 필요합니다."
            )
        if source_asset_id == target_asset_id and asset_pair:
            raise ApplicationValidationError("Asset 자기 관계는 허용되지 않습니다.")
        if source_asset_version_id == target_asset_version_id and version_pair:
            raise ApplicationValidationError("AssetVersion 자기 관계는 허용되지 않습니다.")
        normalized_type = _required_text(relation_type, "관계 유형")
        try:
            with self.session_factory() as session, session.begin():
                repository = AssetRepository(session)
                if asset_pair:
                    for asset_id in (source_asset_id, target_asset_id):
                        if repository.get_asset(asset_id) is None:
                            raise ResourceNotFoundError("Asset")
                else:
                    for version_id in (
                        source_asset_version_id,
                        target_asset_version_id,
                    ):
                        version = repository.get_asset_version(version_id)
                        if version is None:
                            raise ResourceNotFoundError("AssetVersion")
                        if repository.get_asset(version.asset_id) is None:
                            raise ResourceNotFoundError("Asset")
                if repository.relation_exists(
                    relation_type=normalized_type,
                    source_asset_id=source_asset_id,
                    target_asset_id=target_asset_id,
                    source_asset_version_id=source_asset_version_id,
                    target_asset_version_id=target_asset_version_id,
                ):
                    raise ResourceConflictError("AssetRelation")
                relation = repository.add_asset_relation(
                    AssetRelation(
                        relation_type=normalized_type,
                        source_asset_id=source_asset_id,
                        target_asset_id=target_asset_id,
                        source_asset_version_id=source_asset_version_id,
                        target_asset_version_id=target_asset_version_id,
                    )
                )
            return relation
        except IntegrityError:
            raise ResourceConflictError("AssetRelation") from None

    def list_asset_relations(
        self,
        *,
        asset_id: UUID | None = None,
        asset_version_id: UUID | None = None,
        relation_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetRelation]:
        with self.session_factory() as session:
            return AssetRepository(session).list_asset_relations(
                asset_id=asset_id,
                asset_version_id=asset_version_id,
                relation_type=relation_type,
                limit=limit,
                offset=offset,
            )


def _validate_page_limit(limit: object) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidLimitError()
