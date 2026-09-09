"""Durable Export publication persistence with compare-and-set transitions."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from backend.models.workspace.enums import JobStatus
from backend.models.workspace.export import ExportPublicationState, JobExportPublication
from backend.models.workspace.job import Job


class ExportPublicationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: UUID) -> JobExportPublication | None:
        return self.session.get(JobExportPublication, job_id)

    def add(self, publication: JobExportPublication) -> JobExportPublication:
        self.session.add(publication)
        self.session.flush()
        return publication

    def set_expected_integrity(
        self,
        job_id: UUID,
        *,
        version: int,
        claimed_by: str,
        claim_token: UUID,
        sha256: str,
        size_bytes: int,
    ) -> JobExportPublication | None:
        statement = (
            update(JobExportPublication)
            .where(
                JobExportPublication.job_id == job_id,
                JobExportPublication.version == version,
                JobExportPublication.state == ExportPublicationState.INTENDED,
                JobExportPublication.expected_sha256.is_(None),
                JobExportPublication.expected_size_bytes.is_(None),
                self._owned_claim(job_id, claimed_by, claim_token),
            )
            .values(
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
                version=JobExportPublication.version + 1,
            )
            .returning(JobExportPublication)
        )
        return self.session.scalars(statement).one_or_none()

    def transition(
        self,
        job_id: UUID,
        *,
        version: int,
        from_state: ExportPublicationState,
        to_state: ExportPublicationState,
        claimed_by: str,
        claim_token: UUID,
        artifact_id: UUID | None = None,
    ) -> JobExportPublication | None:
        values: dict[str, object] = {
            "state": to_state,
            "version": JobExportPublication.version + 1,
        }
        if artifact_id is not None:
            values["artifact_id"] = artifact_id
        statement = (
            update(JobExportPublication)
            .where(
                JobExportPublication.job_id == job_id,
                JobExportPublication.version == version,
                JobExportPublication.state == from_state,
                self._owned_claim(job_id, claimed_by, claim_token),
            )
            .values(**values)
            .returning(JobExportPublication)
        )
        return self.session.scalars(statement).one_or_none()

    def bind_artifact(
        self,
        job_id: UUID,
        *,
        version: int,
        claimed_by: str,
        claim_token: UUID,
        artifact_id: UUID,
    ) -> JobExportPublication | None:
        statement = (
            update(JobExportPublication)
            .where(
                JobExportPublication.job_id == job_id,
                JobExportPublication.version == version,
                JobExportPublication.state == ExportPublicationState.PUBLISHED,
                JobExportPublication.artifact_id.is_(None),
                self._owned_claim(job_id, claimed_by, claim_token),
            )
            .values(
                artifact_id=artifact_id,
                version=JobExportPublication.version + 1,
            )
            .returning(JobExportPublication)
        )
        return self.session.scalars(statement).one_or_none()

    @staticmethod
    def _owned_claim(job_id: UUID, claimed_by: str, claim_token: UUID):
        return exists(
            select(Job.job_id).where(
                Job.job_id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.claimed_by == claimed_by,
                Job.claim_token == claim_token,
                Job.cancel_requested_at.is_(None),
            )
        )
