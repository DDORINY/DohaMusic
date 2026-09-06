"""Pipeline orchestration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse

from backend.api.dependencies import get_pipeline_service
from backend.schemas.pipeline import (
    PipelineCancelRead,
    PipelineCreate,
    PipelineFileRead,
    PipelineJobRead,
    PipelineRetryRead,
)
from backend.services.pipeline_service import PipelineService

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
ServiceDependency = Annotated[PipelineService, Depends(get_pipeline_service)]


@router.post("", response_model=PipelineJobRead, status_code=status.HTTP_202_ACCEPTED)
def create_pipeline(request: PipelineCreate, service: ServiceDependency) -> PipelineJobRead:
    return PipelineJobRead.model_validate(service.create(request))


@router.get("/{job_id}", response_model=PipelineJobRead)
def get_pipeline(job_id: str, service: ServiceDependency) -> PipelineJobRead:
    return PipelineJobRead.model_validate(service.get(job_id))


@router.post("/{job_id}/cancel", response_model=PipelineCancelRead)
def cancel_pipeline(job_id: str, service: ServiceDependency) -> PipelineCancelRead:
    job = service.cancel(job_id)
    return PipelineCancelRead(
        job_id=job.id,
        status=job.status,
        cancel_requested_at=job.cancel_requested_at,
        cancelled_at=job.cancelled_at,
        message=(
            "음악 만들기가 취소되었습니다."
            if job.status == "CANCELLED"
            else "음악 만들기 취소를 요청했습니다."
        ),
    )


@router.post(
    "/{job_id}/retry",
    response_model=PipelineRetryRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_pipeline(job_id: str, service: ServiceDependency) -> PipelineRetryRead:
    job = service.retry(job_id)
    return PipelineRetryRead(
        source_job_id=job_id,
        job=PipelineJobRead.model_validate(job),
    )


@router.get("/{job_id}/files", response_model=list[PipelineFileRead])
def list_pipeline_files(job_id: str, service: ServiceDependency) -> list[PipelineFileRead]:
    return [PipelineFileRead.model_validate(item) for item in service.list_files(job_id)]


@router.get(
    "/{job_id}/files/{file_id}/content",
    operation_id="get_pipeline_file_content",
    response_class=FileResponse,
)
@router.head(
    "/{job_id}/files/{file_id}/content",
    operation_id="head_pipeline_file_content",
    response_class=FileResponse,
)
def get_pipeline_file_content(
    job_id: str,
    file_id: str,
    request: Request,
    service: ServiceDependency,
) -> FileResponse:
    access = service.access_audio_file(
        job_id,
        file_id,
        download=False,
        range_header=request.headers.get("range"),
    )
    return FileResponse(
        access.path,
        media_type=access.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{job_id}/files/{file_id}/download",
    operation_id="download_pipeline_file",
    response_class=FileResponse,
)
@router.head(
    "/{job_id}/files/{file_id}/download",
    operation_id="head_download_pipeline_file",
    response_class=FileResponse,
)
def download_pipeline_file(
    job_id: str,
    file_id: str,
    request: Request,
    service: ServiceDependency,
) -> FileResponse:
    access = service.access_audio_file(
        job_id,
        file_id,
        download=True,
        range_header=request.headers.get("range"),
    )
    return FileResponse(
        access.path,
        media_type=access.mime_type,
        filename=access.filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
