"""Durable Export publication intent, publication, and recovery authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.audio.export_delivery_validator import (
    ExportDeliveryFormat,
    export_delivery_media_type,
    parse_export_delivery_format,
)
from backend.models.workspace.enums import JobStatus
from backend.models.workspace.export import ExportPublicationState, JobExportPublication
from backend.repositories.workspace.export_publication_repository import (
    ExportPublicationRepository,
)
from backend.repositories.workspace.job_repository import JobRepository
from backend.storage.artifact_publisher import (
    ArtifactPublishError,
    ArtifactPublishErrorCode,
    LocalArtifactPublisher,
    PublicationOutcome,
    PublishOrAdoptResult,
    TrustedPublicationIdentity,
)


class ExportPublicationErrorCode(StrEnum):
    INVALID_JOB = "INVALID_JOB"
    CONFLICT = "CONFLICT"
    STALE_CLAIM = "STALE_CLAIM"
    CANCELLED = "CANCELLED"
    INVALID_STATE = "INVALID_STATE"
    INVALID_INTEGRITY = "INVALID_INTEGRITY"
    STORAGE_MISMATCH = "STORAGE_MISMATCH"


class ExportPublicationError(RuntimeError):
    def __init__(self, code: ExportPublicationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class PublicationRecoveryResult:
    publication: JobExportPublication
    storage_outcome: PublicationOutcome | None


class ExportPublicationService:
    """Own transactions while repositories remain commit/rollback free."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        publisher: LocalArtifactPublisher,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher

    def ensure_intent(
        self, *, job_id: UUID, composition_snapshot_id: UUID, export_format: str = "wav"
    ) -> JobExportPublication:
        try:
            parsed_format = parse_export_delivery_format(export_format)
        except Exception:
            raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT) from None
        normalized_format = parsed_format.value
        identity = TrustedPublicationIdentity.for_export(job_id, normalized_format)
        try:
            with self._session_factory() as session, session.begin():
                jobs = JobRepository(session)
                job = jobs.get_job(job_id)
                if job is None or job.composition_snapshot_id != composition_snapshot_id:
                    raise ExportPublicationError(ExportPublicationErrorCode.INVALID_JOB)
                repository = ExportPublicationRepository(session)
                existing = repository.get(job_id)
                if existing is not None:
                    self._assert_binding(
                        existing, composition_snapshot_id, normalized_format, identity
                    )
                    return existing
                return repository.add(
                    JobExportPublication(
                        job_id=job_id,
                        composition_snapshot_id=composition_snapshot_id,
                        export_format=normalized_format,
                        storage_domain=identity.storage_domain,
                        storage_key=identity.storage_key,
                        state=ExportPublicationState.INTENDED,
                    )
                )
        except IntegrityError:
            with self._session_factory() as session:
                existing = ExportPublicationRepository(session).get(job_id)
                if existing is None:
                    raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT) from None
                self._assert_binding(existing, composition_snapshot_id, normalized_format, identity)
                return existing

    def set_expected_integrity(
        self,
        *,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        sha256: str,
        size_bytes: int,
    ) -> JobExportPublication:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256) or size_bytes < 0:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_INTEGRITY)
        with self._session_factory() as session, session.begin():
            self._assert_claim(JobRepository(session), job_id, claimed_by, claim_token)
            repository = ExportPublicationRepository(session)
            publication = self._require(repository, job_id)
            if publication.expected_sha256 is not None:
                if (
                    publication.expected_sha256 == sha256
                    and publication.expected_size_bytes == size_bytes
                ):
                    return publication
                raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)
            updated = repository.set_expected_integrity(
                job_id,
                version=publication.version,
                claimed_by=claimed_by,
                claim_token=claim_token,
                sha256=sha256,
                size_bytes=size_bytes,
            )
            if updated is None:
                raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)
            return updated

    def publish_or_recover(
        self,
        *,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        staged_payload: Path | None = None,
    ) -> PublicationRecoveryResult:
        publication = self._load_owned(job_id, claimed_by, claim_token)
        if publication.state is ExportPublicationState.RECONCILIATION_REQUIRED:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_STATE)
        if publication.expected_sha256 is None or publication.expected_size_bytes is None:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_INTEGRITY)
        identity = self._identity(publication)
        try:
            if publication.state is ExportPublicationState.INTENDED and staged_payload is not None:
                result = self._publisher.publish_or_adopt(
                    staged_payload,
                    identity=identity,
                    artifact_kind="audio",
                    expected_media_type=export_delivery_media_type(
                        ExportDeliveryFormat(publication.export_format)
                    ),
                    expected_sha256=publication.expected_sha256,
                    expected_size_bytes=publication.expected_size_bytes,
                )
            else:
                with self._publisher.open_trusted_publication(
                    identity,
                    artifact_kind="audio",
                    expected_media_type=export_delivery_media_type(
                        ExportDeliveryFormat(publication.export_format)
                    ),
                    expected_sha256=publication.expected_sha256,
                    expected_size_bytes=publication.expected_size_bytes,
                ) as (result, _stream):
                    pass
        except ArtifactPublishError as error:
            if (
                error.code is ArtifactPublishErrorCode.PUBLICATION_NOT_FOUND
                and publication.state is ExportPublicationState.INTENDED
                and staged_payload is None
            ):
                return PublicationRecoveryResult(publication, None)
            self._mark_reconciliation(job_id, claimed_by, claim_token)
            raise ExportPublicationError(ExportPublicationErrorCode.STORAGE_MISMATCH) from None
        if publication.state is ExportPublicationState.PUBLISHED:
            return PublicationRecoveryResult(publication, result.outcome)
        updated = self._transition_owned(
            job_id,
            claimed_by,
            claim_token,
            from_state=ExportPublicationState.INTENDED,
            to_state=ExportPublicationState.PUBLISHED,
        )
        return PublicationRecoveryResult(updated, result.outcome)

    def mark_completed(
        self, *, job_id: UUID, claimed_by: str, claim_token: UUID, artifact_id: UUID
    ) -> JobExportPublication:
        return self._transition_owned(
            job_id,
            claimed_by,
            claim_token,
            from_state=ExportPublicationState.PUBLISHED,
            to_state=ExportPublicationState.COMPLETED,
            artifact_id=artifact_id,
        )

    @contextmanager
    def open_trusted_publication(
        self,
        *,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
    ) -> Iterator[tuple[PublishOrAdoptResult, BinaryIO]]:
        """Open and verify the claim-owned deterministic publication."""

        publication = self._load_owned(job_id, claimed_by, claim_token)
        if publication.state not in {
            ExportPublicationState.PUBLISHED,
            ExportPublicationState.COMPLETED,
        }:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_STATE)
        if publication.expected_sha256 is None or publication.expected_size_bytes is None:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_INTEGRITY)
        try:
            with self._publisher.open_trusted_publication(
                self._identity(publication),
                artifact_kind="audio",
                expected_media_type=export_delivery_media_type(
                    ExportDeliveryFormat(publication.export_format)
                ),
                expected_sha256=publication.expected_sha256,
                expected_size_bytes=publication.expected_size_bytes,
            ) as opened:
                yield opened
        except ArtifactPublishError:
            raise ExportPublicationError(ExportPublicationErrorCode.STORAGE_MISMATCH) from None

    def _load_owned(self, job_id: UUID, claimed_by: str, claim_token: UUID) -> JobExportPublication:
        with self._session_factory() as session:
            self._assert_claim(JobRepository(session), job_id, claimed_by, claim_token)
            return self._require(ExportPublicationRepository(session), job_id)

    def _transition_owned(
        self,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        *,
        from_state: ExportPublicationState,
        to_state: ExportPublicationState,
        artifact_id: UUID | None = None,
    ) -> JobExportPublication:
        with self._session_factory() as session, session.begin():
            self._assert_claim(JobRepository(session), job_id, claimed_by, claim_token)
            repository = ExportPublicationRepository(session)
            publication = self._require(repository, job_id)
            if publication.state is to_state and (
                artifact_id is None or publication.artifact_id == artifact_id
            ):
                return publication
            updated = repository.transition(
                job_id,
                version=publication.version,
                from_state=from_state,
                to_state=to_state,
                claimed_by=claimed_by,
                claim_token=claim_token,
                artifact_id=artifact_id,
            )
            if updated is None:
                raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)
            return updated

    def _mark_reconciliation(self, job_id: UUID, claimed_by: str, claim_token: UUID) -> None:
        with self._session_factory() as session, session.begin():
            self._assert_claim(JobRepository(session), job_id, claimed_by, claim_token)
            repository = ExportPublicationRepository(session)
            publication = self._require(repository, job_id)
            if publication.state is ExportPublicationState.RECONCILIATION_REQUIRED:
                return
            updated = repository.transition(
                job_id,
                version=publication.version,
                from_state=publication.state,
                to_state=ExportPublicationState.RECONCILIATION_REQUIRED,
                claimed_by=claimed_by,
                claim_token=claim_token,
            )
            if updated is None:
                raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)

    @staticmethod
    def _assert_claim(
        jobs: JobRepository, job_id: UUID, claimed_by: str, claim_token: UUID
    ) -> None:
        job = jobs.get_job(job_id)
        if job is None or job.status is not JobStatus.RUNNING:
            raise ExportPublicationError(ExportPublicationErrorCode.STALE_CLAIM)
        if job.claimed_by != claimed_by or job.claim_token != claim_token:
            raise ExportPublicationError(ExportPublicationErrorCode.STALE_CLAIM)
        if job.cancel_requested_at is not None:
            raise ExportPublicationError(ExportPublicationErrorCode.CANCELLED)

    @staticmethod
    def _require(repository: ExportPublicationRepository, job_id: UUID) -> JobExportPublication:
        publication = repository.get(job_id)
        if publication is None:
            raise ExportPublicationError(ExportPublicationErrorCode.INVALID_STATE)
        return publication

    @staticmethod
    def _identity(publication: JobExportPublication) -> TrustedPublicationIdentity:
        identity = TrustedPublicationIdentity.for_export(
            publication.job_id, publication.export_format
        )
        if (
            identity.storage_domain != publication.storage_domain
            or identity.storage_key != publication.storage_key
        ):
            raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)
        return identity

    @staticmethod
    def _assert_binding(
        publication: JobExportPublication,
        snapshot_id: UUID,
        export_format: str,
        identity: TrustedPublicationIdentity,
    ) -> None:
        if (
            publication.composition_snapshot_id != snapshot_id
            or publication.export_format != export_format
            or publication.storage_domain != identity.storage_domain
            or publication.storage_key != identity.storage_key
        ):
            raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)
