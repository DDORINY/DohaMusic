"""Workspace Resource Router의 공통 transport helper."""

from __future__ import annotations

from fastapi import Request

from backend.core.exceptions import (
    AppError,
    ApplicationValidationError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    InvalidStateError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from backend.services.workspace import (
    TrustedMediaMetadataError,
    WorkingCompositionError,
    WorkingCompositionErrorCode,
    WorkingPreviewError,
    WorkingPreviewErrorCode,
    WorkspaceService,
)


def require_bootstrapped_workspace(service: WorkspaceService) -> None:
    if not service.list_workspaces(limit=1):
        raise AppError(
            code="WORKSPACE_BOOTSTRAP_REQUIRED",
            message="기본 Workspace Bootstrap이 필요합니다.",
            status_code=409,
        )


def reject_owner_input(request: Request) -> None:
    """소유권 필드는 공개 Resource API 입력으로 받지 않는다."""

    if {"owner_id", "created_by"}.intersection(request.query_params):
        raise invalid_input("소유권 필드는 API 입력으로 지정할 수 없습니다.")


def workspace_not_found() -> AppError:
    return AppError(
        code="WORKSPACE_NOT_FOUND",
        message="Workspace를 찾을 수 없습니다.",
        status_code=404,
    )


def project_not_found() -> AppError:
    return AppError(
        code="PROJECT_NOT_FOUND",
        message="Project를 찾을 수 없습니다.",
        status_code=404,
    )


def workspace_name_conflict() -> AppError:
    return AppError(
        code="WORKSPACE_NAME_CONFLICT",
        message="같은 이름의 Workspace가 이미 존재합니다.",
        status_code=409,
    )


def project_title_conflict() -> AppError:
    return AppError(
        code="PROJECT_TITLE_CONFLICT",
        message="같은 제목의 Project가 이미 존재합니다.",
        status_code=409,
    )


def asset_not_found() -> AppError:
    return AppError(
        code="ASSET_NOT_FOUND",
        message="Asset을 찾을 수 없습니다.",
        status_code=404,
    )


def asset_conflict() -> AppError:
    return AppError(
        code="ASSET_CONFLICT",
        message="Asset 요청이 현재 상태와 충돌합니다.",
        status_code=409,
    )


def asset_version_not_found() -> AppError:
    return AppError(
        code="ASSET_VERSION_NOT_FOUND",
        message="AssetVersion을 찾을 수 없습니다.",
        status_code=404,
    )


def asset_version_conflict() -> AppError:
    return AppError(
        code="ASSET_VERSION_CONFLICT",
        message="AssetVersion 요청이 현재 상태와 충돌합니다.",
        status_code=409,
    )


def project_asset_not_found() -> AppError:
    return AppError(
        code="PROJECT_ASSET_NOT_FOUND",
        message="ProjectAsset 연결을 찾을 수 없습니다.",
        status_code=404,
    )


def project_asset_conflict() -> AppError:
    return AppError(
        code="PROJECT_ASSET_CONFLICT",
        message="Project와 Asset이 이미 연결돼 있습니다.",
        status_code=409,
    )


def invalid_input(message: str = "요청 입력값이 유효하지 않습니다.") -> AppError:
    return AppError(code="INVALID_INPUT", message=message, status_code=422)


def relative_request_url(request: Request) -> str:
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


def relative_next_url(request: Request, next_cursor: str | None) -> str | None:
    if next_cursor is None:
        return None
    url = request.url.include_query_params(cursor=next_cursor)
    return url.path + (f"?{url.query}" if url.query else "")


def map_workspace_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        return workspace_not_found()
    if isinstance(exc, ResourceConflictError):
        return workspace_name_conflict()
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_project_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        return project_not_found()
    if isinstance(exc, ResourceConflictError):
        return project_title_conflict()
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_project_asset_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "MusicProject":
            return project_not_found()
        if exc.resource_name == "Asset":
            return asset_not_found()
        if exc.resource_name == "ProjectAsset":
            return project_asset_not_found()
    if isinstance(exc, ResourceConflictError) and exc.resource_name == "ProjectAsset":
        return project_asset_conflict()
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_asset_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "Workspace":
            return workspace_not_found()
        if exc.resource_name == "Asset":
            return asset_not_found()
    if isinstance(exc, ResourceConflictError) and exc.resource_name == "Asset":
        return asset_conflict()
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_asset_version_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "Asset":
            return asset_not_found()
        if "AssetVersion" in exc.resource_name:
            return asset_version_not_found()
        if exc.resource_name == "ProcessingChain":
            return invalid_input("참조한 ProcessingChain을 찾을 수 없습니다.")
    if isinstance(exc, ResourceConflictError) and exc.resource_name.startswith("AssetVersion"):
        return asset_version_conflict()
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_composition_snapshot_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "MusicProject":
            return project_not_found()
        if exc.resource_name == "AssetVersion":
            return asset_version_not_found()
        if exc.resource_name == "ProjectAsset":
            return project_asset_not_found()
        if exc.resource_name == "CompositionSnapshot":
            return AppError(
                code="COMPOSITION_SNAPSHOT_NOT_FOUND",
                message="CompositionSnapshot을 찾을 수 없습니다.",
                status_code=404,
            )
        if exc.resource_name == "ProcessingChain":
            return invalid_input("참조한 ProcessingChain을 찾을 수 없습니다.")
    if isinstance(exc, ResourceConflictError):
        return AppError(
            code="COMPOSITION_SNAPSHOT_CONFLICT",
            message="CompositionSnapshot 요청이 현재 상태와 충돌합니다.",
            status_code=409,
        )
    if isinstance(exc, IdempotencyConflictError):
        return AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="같은 Idempotency-Key가 다른 요청에 사용됐습니다.",
            status_code=409,
        )
    if isinstance(exc, IdempotencyInProgressError):
        return AppError(
            code="IDEMPOTENCY_IN_PROGRESS",
            message="같은 Idempotency-Key 요청이 처리 중입니다.",
            status_code=409,
        )
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc


def map_working_composition_error(exc: Exception) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "MusicProject":
            return project_not_found()
        if exc.resource_name == "CompositionSnapshot":
            return AppError(
                code="COMPOSITION_SNAPSHOT_NOT_FOUND",
                message="CompositionSnapshot을 찾을 수 없습니다.",
                status_code=404,
            )
    if isinstance(exc, WorkingCompositionError):
        not_found = {
            WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND,
            WorkingCompositionErrorCode.TRACK_NOT_FOUND,
            WorkingCompositionErrorCode.CLIP_NOT_FOUND,
        }
        validation = {
            WorkingCompositionErrorCode.INVALID_CLIP_RANGE,
            WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE,
            WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE,
            WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE,
            WorkingCompositionErrorCode.MASTER_GAIN_OUT_OF_RANGE,
        }
        return AppError(
            code=exc.code.value,
            message=str(exc),
            status_code=(404 if exc.code in not_found else 422 if exc.code in validation else 409),
        )
    if isinstance(exc, IdempotencyConflictError):
        return AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="같은 Idempotency-Key가 다른 요청에 사용됐습니다.",
            status_code=409,
        )
    if isinstance(exc, IdempotencyInProgressError):
        return AppError(
            code="IDEMPOTENCY_IN_PROGRESS",
            message="같은 Idempotency-Key 요청이 처리 중입니다.",
            status_code=409,
        )
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    if isinstance(exc, TrustedMediaMetadataError):
        return AppError(code=exc.code.value, message=str(exc), status_code=409)
    raise exc


def map_working_preview_error(exc: Exception) -> AppError:
    if isinstance(exc, WorkingPreviewError):
        not_found = {WorkingPreviewErrorCode.NOT_FOUND}
        validation = {
            WorkingPreviewErrorCode.EMPTY,
            WorkingPreviewErrorCode.LIMIT_EXCEEDED,
            WorkingPreviewErrorCode.SOURCE_UNAVAILABLE,
            WorkingPreviewErrorCode.OUTPUT_INVALID,
        }
        return AppError(
            code=exc.code.value,
            message=str(exc),
            status_code=404 if exc.code in not_found else 422 if exc.code in validation else 409,
        )
    if isinstance(exc, ValueError):
        return invalid_input(str(exc))
    return map_working_composition_error(exc)


def map_job_error(exc: Exception, *, action: str | None = None) -> AppError:
    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_name == "Workspace Job":
            return AppError(
                code="JOB_NOT_FOUND",
                message="Job을 찾을 수 없습니다.",
                status_code=404,
            )
        if exc.resource_name == "Workspace":
            return workspace_not_found()
        if exc.resource_name == "MusicProject":
            return project_not_found()
        if exc.resource_name == "CompositionSnapshot":
            return AppError(
                code="COMPOSITION_SNAPSHOT_NOT_FOUND",
                message="CompositionSnapshot을 찾을 수 없습니다.",
                status_code=404,
            )
        if exc.resource_name in {"AssetVersion", "JobInput"}:
            return asset_version_not_found()
        if exc.resource_name == "Artifact":
            return AppError(
                code="ARTIFACT_NOT_FOUND",
                message="Artifact를 찾을 수 없습니다.",
                status_code=404,
            )
        if exc.resource_name == "ProjectAsset":
            return project_asset_not_found()
    if isinstance(exc, InvalidStateError):
        if action == "cancel":
            return AppError(
                code="JOB_NOT_CANCELLABLE",
                message="현재 상태의 Job은 취소할 수 없습니다.",
                status_code=409,
            )
        if action == "retry":
            return AppError(
                code="JOB_NOT_RETRYABLE",
                message="현재 상태의 Job은 재시도할 수 없습니다.",
                status_code=409,
            )
        return AppError(
            code="JOB_CONFLICT",
            message="Job 요청이 현재 상태와 충돌합니다.",
            status_code=409,
        )
    if isinstance(exc, ResourceConflictError):
        return AppError(
            code="JOB_CONFLICT",
            message="Job 요청이 현재 상태와 충돌합니다.",
            status_code=409,
        )
    if isinstance(exc, IdempotencyConflictError):
        return AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="같은 Idempotency-Key가 다른 요청에 사용됐습니다.",
            status_code=409,
        )
    if isinstance(exc, IdempotencyInProgressError):
        return AppError(
            code="IDEMPOTENCY_IN_PROGRESS",
            message="같은 Idempotency-Key 요청이 처리 중입니다.",
            status_code=409,
        )
    if isinstance(exc, ApplicationValidationError):
        return invalid_input(exc.message)
    raise exc
