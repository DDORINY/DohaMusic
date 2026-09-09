"""Workspace Job aggregate persistence operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from backend.models.workspace.enums import JobStatus
from backend.models.workspace.job import Job, JobInput, JobOutput, ModelUsage
from backend.models.workspace.workspace import MusicProject, Workspace


class JobRepository:
    """Workspace Job aggregate를 commit 없이 현재 transaction에 반영한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_job(self, job: Job) -> Job:
        self.session.add(job)
        self.session.flush()
        return job

    def get_job(self, job_id: UUID) -> Job | None:
        return self.session.get(Job, job_id)

    def get_job_for_owner(self, job_id: UUID, owner_id: UUID) -> Job | None:
        """Owner 범위를 벗어난 Job의 존재를 노출하지 않고 조회한다."""

        statement = (
            select(Job)
            .join(Workspace, Workspace.workspace_id == Job.workspace_id)
            .join(MusicProject, MusicProject.project_id == Job.project_id)
            .where(
                Job.job_id == job_id,
                Workspace.owner_id == owner_id,
                Workspace.deleted_at.is_(None),
                MusicProject.workspace_id == Job.workspace_id,
                MusicProject.deleted_at.is_(None),
            )
        )
        return self.session.scalar(statement)

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        statement = (
            select(Job).order_by(Job.created_at.desc(), Job.job_id).limit(limit).offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_project_jobs(
        self, project_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Job]:
        statement = (
            select(Job)
            .where(Job.project_id == project_id)
            .order_by(Job.created_at.desc(), Job.job_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_jobs_by_status(
        self, status: JobStatus, *, limit: int = 100, offset: int = 0
    ) -> list[Job]:
        statement = (
            select(Job)
            .where(Job.status == status)
            .order_by(Job.created_at, Job.job_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_jobs_after(
        self,
        *,
        owner_id: UUID,
        workspace_id: UUID,
        project_id: UUID | None = None,
        status: JobStatus | None = None,
        job_type: str | None = None,
        last_created_at: datetime | None = None,
        last_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """Owner·Workspace scope의 Job을 DESC keyset으로 조회한다."""

        statement = _build_list_jobs_after_statement(
            owner_id=owner_id,
            workspace_id=workspace_id,
            project_id=project_id,
            status=status,
            job_type=job_type,
            last_created_at=last_created_at,
            last_id=last_id,
            limit=limit,
        )
        return list(self.session.scalars(statement))

    def update_job_status(
        self,
        job: Job,
        status: JobStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> Job:
        job.status = status
        if started_at is not None:
            job.started_at = started_at
        if completed_at is not None:
            job.completed_at = completed_at
        self.session.flush()
        return job

    def claim_next_job(
        self,
        *,
        claimed_by: str,
        claim_token: UUID,
        now: datetime,
        lease_expires_at: datetime,
        job_type: str | None = None,
    ) -> Job | None:
        """공식 queue 순서의 queued Job 하나를 조건부 atomic claim한다."""

        candidate_query = select(Job.job_id).where(
            Job.status == JobStatus.QUEUED,
            Job.cancel_requested_at.is_(None),
            Job.claim_token.is_(None),
            Job.lease_expires_at.is_(None),
        )
        if job_type is not None:
            candidate_query = candidate_query.where(Job.job_type == job_type)
        candidate = self.session.scalar(
            candidate_query.order_by(Job.created_at, Job.job_id).limit(1)
        )
        if candidate is None:
            return None
        statement = (
            update(Job)
            .where(
                Job.job_id == candidate,
                Job.status == JobStatus.QUEUED,
                Job.cancel_requested_at.is_(None),
                Job.claim_token.is_(None),
                Job.lease_expires_at.is_(None),
            )
            .values(
                status=JobStatus.RUNNING,
                claim_token=claim_token,
                claimed_by=claimed_by,
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
                attempt=Job.attempt + 1,
                started_at=now,
            )
            .returning(Job)
        )
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return self.session.scalars(statement).one_or_none()

    def heartbeat_claim(
        self,
        job_id: UUID,
        *,
        claimed_by: str,
        claim_token: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> Job | None:
        statement = (
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.claimed_by == claimed_by,
                Job.claim_token == claim_token,
                or_(Job.heartbeat_at.is_(None), Job.heartbeat_at <= now),
            )
            .values(heartbeat_at=now, lease_expires_at=lease_expires_at)
            .returning(Job)
        )
        return self.session.scalars(statement).one_or_none()

    def recover_expired_claim(
        self,
        *,
        now: datetime,
        job_type: str | None = None,
        requeue: bool = False,
    ) -> Job | None:
        candidate_query = select(Job.job_id, Job.lease_expires_at).where(
            Job.status == JobStatus.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
        )
        if job_type is not None:
            candidate_query = candidate_query.where(Job.job_type == job_type)
        candidate = self.session.execute(
            candidate_query.order_by(Job.lease_expires_at, Job.job_id).limit(1)
        ).one_or_none()
        if candidate is None:
            return None
        values = (
            {
                "status": JobStatus.QUEUED,
                "claim_token": None,
                "claimed_by": None,
                "heartbeat_at": None,
                "lease_expires_at": None,
                "started_at": None,
            }
            if requeue
            else {
                "status": JobStatus.FAILED,
                "completed_at": now,
                "error_code": "WORKER_LEASE_EXPIRED",
                "error_message": "Workspace Job worker lease expired.",
                "error_retryable": True,
            }
        )
        statement = (
            update(Job)
            .where(
                Job.job_id == candidate.job_id,
                Job.status == JobStatus.RUNNING,
                Job.lease_expires_at == candidate.lease_expires_at,
                Job.lease_expires_at < now,
            )
            .values(**values)
            .returning(Job)
        )
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return self.session.scalars(statement).one_or_none()

    def finish_owned_claim(
        self,
        job_id: UUID,
        *,
        claimed_by: str,
        claim_token: UUID,
        status: JobStatus,
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
        error_retryable: bool | None = None,
    ) -> Job | None:
        conditions = [
            Job.job_id == job_id,
            Job.status == JobStatus.RUNNING,
            Job.claimed_by == claimed_by,
            Job.claim_token == claim_token,
        ]
        if status is JobStatus.SUCCEEDED:
            conditions.append(Job.cancel_requested_at.is_(None))
        statement = (
            update(Job)
            .where(*conditions)
            .values(
                status=status,
                completed_at=now,
                error_code=error_code,
                error_message=error_message,
                error_retryable=error_retryable,
            )
            .returning(Job)
        )
        return self.session.scalars(statement).one_or_none()

    def add_job_input(self, item: JobInput) -> JobInput:
        self.session.add(item)
        self.session.flush()
        return item

    def list_job_inputs(self, job_id: UUID, *, limit: int = 100, offset: int = 0) -> list[JobInput]:
        statement = (
            select(JobInput)
            .where(JobInput.job_id == job_id)
            .order_by(JobInput.input_order, JobInput.job_input_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def job_input_order_exists(self, job_id: UUID, input_order: int) -> bool:
        statement = select(JobInput.job_input_id).where(
            JobInput.job_id == job_id,
            JobInput.input_order == input_order,
        )
        return self.session.scalar(statement.limit(1)) is not None

    def add_job_output(self, item: JobOutput) -> JobOutput:
        self.session.add(item)
        self.session.flush()
        return item

    def list_job_outputs(
        self, job_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[JobOutput]:
        statement = (
            select(JobOutput)
            .where(JobOutput.job_id == job_id)
            .order_by(JobOutput.output_order, JobOutput.job_output_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def job_output_order_exists(self, job_id: UUID, output_order: int) -> bool:
        statement = select(JobOutput.job_output_id).where(
            JobOutput.job_id == job_id,
            JobOutput.output_order == output_order,
        )
        return self.session.scalar(statement.limit(1)) is not None

    def add_model_usage(self, usage: ModelUsage) -> ModelUsage:
        self.session.add(usage)
        self.session.flush()
        return usage

    def list_model_usages(
        self, job_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ModelUsage]:
        statement = (
            select(ModelUsage)
            .where(ModelUsage.job_id == job_id)
            .order_by(ModelUsage.created_at, ModelUsage.model_usage_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))


def _build_list_jobs_after_statement(
    *,
    owner_id: UUID,
    workspace_id: UUID,
    project_id: UUID | None = None,
    status: JobStatus | None = None,
    job_type: str | None = None,
    last_created_at: datetime | None = None,
    last_id: UUID | None = None,
    limit: int = 100,
):
    """Repository 실행과 Query Plan 검증이 공유하는 단일 statement를 만든다."""

    _validate_keyset_position(last_created_at, last_id)
    statement = (
        select(Job)
        .join(Workspace, Workspace.workspace_id == Job.workspace_id)
        .where(
            Workspace.owner_id == owner_id,
            Workspace.workspace_id == workspace_id,
            Workspace.deleted_at.is_(None),
            Job.workspace_id == workspace_id,
        )
    )
    if project_id is not None:
        statement = statement.where(Job.project_id == project_id)
    if status is not None:
        statement = statement.where(Job.status == status)
    if job_type is not None:
        statement = statement.where(Job.job_type == job_type)
    if last_created_at is not None and last_id is not None:
        statement = statement.where(
            or_(
                Job.created_at < last_created_at,
                and_(
                    Job.created_at == last_created_at,
                    Job.job_id < last_id,
                ),
            )
        )
    return statement.order_by(Job.created_at.desc(), Job.job_id.desc()).limit(limit)


def _validate_keyset_position(last_created_at: datetime | None, last_id: UUID | None) -> None:
    if (last_created_at is None) != (last_id is None):
        raise ValueError("keyset position requires both created_at and id")
