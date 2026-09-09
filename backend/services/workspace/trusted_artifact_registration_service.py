"""Register an exact durable publication without publishing a second payload."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.models.workspace import Artifact, ExportPublicationState, JobStatus
from backend.models.workspace.identifiers import generate_uuid
from backend.repositories.workspace import ArtifactStorageRepository, AssetRepository, JobRepository
from backend.repositories.workspace.export_publication_repository import (
    ExportPublicationRepository,
)
from backend.services.workspace.artifact_ingestion_service import (
    ArtifactIngestionRequest,
    ArtifactIngestionService,
    PreparedArtifactIngestion,
)
from backend.storage.artifact_publisher import (
    ArtifactPublishError,
    LocalArtifactPublisher,
    PublishedLocalPayload,
    TrustedPublicationIdentity,
)
from backend.storage.artifact_resolver import SUPPORTED_STORAGE_BACKEND


class TrustedArtifactRegistrationOutcome(StrEnum):
    REGISTERED_NEW = "registered_new"
    REPLAYED_EXISTING = "replayed_existing"


class TrustedArtifactRegistrationErrorCode(StrEnum):
    INVALID_LEDGER = "INVALID_LEDGER"
    STALE_CLAIM = "STALE_CLAIM"
    PUBLICATION_VERIFICATION_FAILED = "PUBLICATION_VERIFICATION_FAILED"
    ARTIFACT_BINDING_CONFLICT = "ARTIFACT_BINDING_CONFLICT"
    REGISTRATION_FAILED = "REGISTRATION_FAILED"


class TrustedArtifactRegistrationError(RuntimeError):
    def __init__(self, code: TrustedArtifactRegistrationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class TrustedArtifactRegistrationRequest:
    job_id: UUID
    claimed_by: str
    claim_token: UUID
    asset_version_id: UUID
    producer_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedArtifactRegistrationResult:
    artifact: Artifact
    outcome: TrustedArtifactRegistrationOutcome


class TrustedArtifactRegistrationService:
    """Promote a ledger-owned payload into the normal Artifact catalog."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        publisher: LocalArtifactPublisher,
        ingestion_service: ArtifactIngestionService,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._ingestion = ingestion_service

    def register(
        self, request: TrustedArtifactRegistrationRequest
    ) -> TrustedArtifactRegistrationResult:
        evidence = self._verified_evidence(request)
        try:
            with self._session_factory() as session, session.begin():
                return self.register_in_session(session, request, evidence=evidence)
        except TrustedArtifactRegistrationError:
            raise
        except IntegrityError:
            return self._replay_after_race(request, evidence)
        except Exception:
            raise TrustedArtifactRegistrationError(
                TrustedArtifactRegistrationErrorCode.REGISTRATION_FAILED
            ) from None

    def verify_evidence(self, request: TrustedArtifactRegistrationRequest) -> PublishedLocalPayload:
        """Verify the durable payload before a caller-owned database UoW begins."""

        return self._verified_evidence(request)

    def register_in_session(
        self,
        session: Session,
        request: TrustedArtifactRegistrationRequest,
        *,
        evidence: PublishedLocalPayload,
    ) -> TrustedArtifactRegistrationResult:
        """Register an already verified publication in the caller's transaction."""

        self._assert_claim(session, request)
        publications = ExportPublicationRepository(session)
        publication = publications.get(request.job_id)
        self._assert_publication(publication, evidence.storage_key)
        if publication.artifact_id is not None:
            artifact = self._verify_replay(
                session, publication.artifact_id, request.asset_version_id, evidence
            )
            return TrustedArtifactRegistrationResult(
                artifact, TrustedArtifactRegistrationOutcome.REPLAYED_EXISTING
            )
        artifact_id = generate_uuid()
        prepared = self._prepared(request, artifact_id, evidence)
        artifact = self._ingestion.register_prepared(session, prepared)
        self._ingestion.verify_registered(session, artifact, prepared)
        bound = publications.bind_artifact(
            request.job_id,
            version=publication.version,
            claimed_by=request.claimed_by,
            claim_token=request.claim_token,
            artifact_id=artifact_id,
        )
        if bound is None:
            raise TrustedArtifactRegistrationError(
                TrustedArtifactRegistrationErrorCode.ARTIFACT_BINDING_CONFLICT
            )
        return TrustedArtifactRegistrationResult(
            artifact, TrustedArtifactRegistrationOutcome.REGISTERED_NEW
        )

    def _verified_evidence(
        self, request: TrustedArtifactRegistrationRequest
    ) -> PublishedLocalPayload:
        with self._session_factory() as session:
            self._assert_claim(session, request)
            publication = ExportPublicationRepository(session).get(request.job_id)
            identity = TrustedPublicationIdentity.for_wav_export(request.job_id)
            self._assert_publication(publication, identity.storage_key)
            expected_sha256 = publication.expected_sha256
            expected_size = publication.expected_size_bytes
            if expected_sha256 is None or expected_size is None:
                raise TrustedArtifactRegistrationError(
                    TrustedArtifactRegistrationErrorCode.INVALID_LEDGER
                )
        try:
            with self._publisher.open_trusted_publication(
                identity,
                artifact_kind="audio",
                expected_media_type="audio/wav",
                expected_sha256=expected_sha256,
                expected_size_bytes=expected_size,
            ) as (verified, _stream):
                return PublishedLocalPayload(
                    path=verified.path,
                    storage_key=identity.storage_key,
                    size_bytes=verified.size_bytes,
                    checksum=verified.checksum,
                    media=verified.media,
                    file_identity=verified.file_identity,
                    source_path=verified.path,
                    source_identity=verified.file_identity,
                )
        except ArtifactPublishError:
            raise TrustedArtifactRegistrationError(
                TrustedArtifactRegistrationErrorCode.PUBLICATION_VERIFICATION_FAILED
            ) from None

    def _prepared(
        self,
        request: TrustedArtifactRegistrationRequest,
        artifact_id: UUID,
        evidence: PublishedLocalPayload,
    ) -> PreparedArtifactIngestion:
        return PreparedArtifactIngestion(
            ArtifactIngestionRequest(
                asset_version_id=request.asset_version_id,
                artifact_kind="audio",
                producer_type="workspace",
                storage_domain="music",
                temporary_path=evidence.path,
                producer_id=request.producer_id,
                run_id=request.run_id,
                expected_media_type="audio/wav",
                expected_sha256=evidence.checksum,
                original_filename="export.wav",
            ),
            artifact_id,
            evidence,
        )

    def _verify_replay(
        self,
        session: Session,
        artifact_id: UUID,
        asset_version_id: UUID,
        evidence: PublishedLocalPayload,
    ) -> Artifact:
        artifact = AssetRepository(session).get_artifact(artifact_id)
        location = ArtifactStorageRepository(session).get_storage_location(artifact_id)
        if (
            artifact is None
            or location is None
            or artifact.asset_version_id != asset_version_id
            or location.storage_backend != SUPPORTED_STORAGE_BACKEND
            or location.storage_domain != "music"
            or location.storage_key != evidence.storage_key
            or artifact.artifact_checksum != evidence.checksum
            or artifact.size_bytes != evidence.size_bytes
            or artifact.media_type != evidence.media.media_type
        ):
            raise TrustedArtifactRegistrationError(
                TrustedArtifactRegistrationErrorCode.ARTIFACT_BINDING_CONFLICT
            )
        self._ingestion.verify_registered(
            session,
            artifact,
            self._prepared(
                TrustedArtifactRegistrationRequest(
                    job_id=UUID(int=0),
                    claimed_by="replay",
                    claim_token=UUID(int=0),
                    asset_version_id=artifact.asset_version_id,
                ),
                artifact.artifact_id,
                evidence,
            ),
        )
        return artifact

    def _replay_after_race(
        self,
        request: TrustedArtifactRegistrationRequest,
        evidence: PublishedLocalPayload,
    ) -> TrustedArtifactRegistrationResult:
        with self._session_factory() as session:
            self._assert_claim(session, request)
            publication = ExportPublicationRepository(session).get(request.job_id)
            self._assert_publication(publication, evidence.storage_key)
            if publication.artifact_id is None:
                raise TrustedArtifactRegistrationError(
                    TrustedArtifactRegistrationErrorCode.ARTIFACT_BINDING_CONFLICT
                )
            artifact = self._verify_replay(
                session,
                publication.artifact_id,
                request.asset_version_id,
                evidence,
            )
            return TrustedArtifactRegistrationResult(
                artifact, TrustedArtifactRegistrationOutcome.REPLAYED_EXISTING
            )

    @staticmethod
    def _assert_claim(session: Session, request: TrustedArtifactRegistrationRequest) -> None:
        job = JobRepository(session).get_job(request.job_id)
        if (
            job is None
            or job.status is not JobStatus.RUNNING
            or job.claimed_by != request.claimed_by
            or job.claim_token != request.claim_token
            or job.cancel_requested_at is not None
        ):
            raise TrustedArtifactRegistrationError(TrustedArtifactRegistrationErrorCode.STALE_CLAIM)

    @staticmethod
    def _assert_publication(publication, storage_key: str) -> None:
        identity = (
            TrustedPublicationIdentity.for_wav_export(publication.job_id) if publication else None
        )
        if (
            publication is None
            or publication.state is not ExportPublicationState.PUBLISHED
            or identity is None
            or publication.storage_domain != identity.storage_domain
            or publication.storage_key != identity.storage_key
            or publication.storage_key != storage_key
        ):
            raise TrustedArtifactRegistrationError(
                TrustedArtifactRegistrationErrorCode.INVALID_LEDGER
            )
