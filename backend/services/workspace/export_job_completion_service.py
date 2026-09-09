"""Atomic Project Export Asset and Export Job completion authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.audio.export_quality import (
    MAXIMUM_LUFS,
    MAXIMUM_TRUE_PEAK_DBTP,
    MINIMUM_LUFS,
    TARGET_LUFS,
    ExportQualityDecision,
)
from backend.models.workspace import (
    Asset,
    AssetType,
    AssetVersion,
    ExportPublicationState,
    JobExportResult,
    JobOutput,
    JobStatus,
    ProjectAsset,
)
from backend.repositories.workspace import (
    ArtifactStorageRepository,
    AssetRepository,
    ExportPublicationRepository,
    JobRepository,
    WorkspaceRepository,
)
from backend.services.workspace.trusted_artifact_registration_service import (
    TrustedArtifactRegistrationRequest,
    TrustedArtifactRegistrationService,
)
from backend.storage.artifact_publisher import TrustedPublicationIdentity
from backend.storage.artifact_resolver import SUPPORTED_STORAGE_BACKEND


class ExportJobCompletionErrorCode(StrEnum):
    INVALID_JOB = "INVALID_JOB"
    STALE_CLAIM = "STALE_CLAIM"
    CANCELLED = "CANCELLED"
    INVALID_PUBLICATION = "INVALID_PUBLICATION"
    CORRUPT_EXPORT_ASSET = "CORRUPT_EXPORT_ASSET"
    CONFLICT = "CONFLICT"


class ExportJobCompletionError(RuntimeError):
    def __init__(self, code: ExportJobCompletionErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ExportJobCompletionRequest:
    job_id: UUID
    claimed_by: str
    claim_token: UUID
    render_fingerprint: str
    quality: ExportQualityDecision
    analyzer_name: str
    analyzer_version: str


@dataclass(frozen=True, slots=True)
class ExportJobCompletionResult:
    export_result: JobExportResult
    job_output: JobOutput


class _FirstExportRace(RuntimeError):
    pass


class ExportJobCompletionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        trusted_registration: TrustedArtifactRegistrationService,
    ) -> None:
        self._session_factory = session_factory
        self._registration = trusted_registration

    def complete(self, request: ExportJobCompletionRequest) -> ExportJobCompletionResult:
        replay = self._replay_fresh(request)
        if replay is not None:
            return replay
        with self._session_factory() as session:
            self._preconditions(session, request)
        registration_request = TrustedArtifactRegistrationRequest(
            job_id=request.job_id,
            claimed_by=request.claimed_by,
            claim_token=request.claim_token,
            asset_version_id=UUID(int=0),
        )
        evidence = self._registration.verify_evidence(registration_request)
        for _attempt in range(3):
            try:
                with self._session_factory() as session, session.begin():
                    replay = self._replay(session, request)
                    if replay is not None:
                        return replay
                    job, project, owner_id = self._preconditions(session, request)
                    asset = self._resolve_or_create_export_asset(session, project, owner_id)
                    version = self._create_version(session, asset, job.job_id, owner_id)
                    registration_request = TrustedArtifactRegistrationRequest(
                        job_id=job.job_id,
                        claimed_by=request.claimed_by,
                        claim_token=request.claim_token,
                        asset_version_id=version.asset_version_id,
                        producer_id="canonical-wav-export",
                        run_id=str(job.job_id),
                    )
                    artifact = self._registration.register_in_session(
                        session, registration_request, evidence=evidence
                    ).artifact
                    output = JobRepository(session).add_job_output(
                        JobOutput(
                            job_id=job.job_id,
                            output_order=0,
                            output_role="export",
                            artifact_id=artifact.artifact_id,
                        )
                    )
                    quality = request.quality
                    result = JobExportResult(
                        job_id=job.job_id,
                        composition_snapshot_id=job.composition_snapshot_id,
                        export_format="wav",
                        render_fingerprint=request.render_fingerprint,
                        exported_asset_version_id=version.asset_version_id,
                        exported_artifact_id=artifact.artifact_id,
                        integrated_loudness_lufs=quality.integrated_loudness_lufs,
                        true_peak_dbtp=quality.true_peak_dbtp,
                        target_lufs=TARGET_LUFS,
                        minimum_lufs=MINIMUM_LUFS,
                        maximum_lufs=MAXIMUM_LUFS,
                        maximum_true_peak_dbtp=MAXIMUM_TRUE_PEAK_DBTP,
                        loudness_passed=quality.loudness_passed,
                        true_peak_passed=quality.true_peak_passed,
                        overall_pass=quality.overall_pass,
                        analyzer_name=request.analyzer_name,
                        analyzer_version=request.analyzer_version,
                    )
                    session.add(result)
                    session.flush()
                    publication = ExportPublicationRepository(session).get(job.job_id)
                    completed = ExportPublicationRepository(session).transition(
                        job.job_id,
                        version=publication.version,
                        from_state=ExportPublicationState.PUBLISHED,
                        to_state=ExportPublicationState.COMPLETED,
                        claimed_by=request.claimed_by,
                        claim_token=request.claim_token,
                        artifact_id=artifact.artifact_id,
                    )
                    if completed is None:
                        raise ExportJobCompletionError(
                            ExportJobCompletionErrorCode.INVALID_PUBLICATION
                        )
                    completed_job = JobRepository(session).finish_owned_claim(
                        job.job_id,
                        claimed_by=request.claimed_by,
                        claim_token=request.claim_token,
                        status=JobStatus.SUCCEEDED,
                        now=datetime.now(UTC),
                    )
                    if completed_job is None:
                        raise ExportJobCompletionError(ExportJobCompletionErrorCode.STALE_CLAIM)
                    return ExportJobCompletionResult(result, output)
            except _FirstExportRace:
                continue
            except IntegrityError:
                replay = self._replay_fresh(request)
                if replay is not None:
                    return replay
                continue
        raise ExportJobCompletionError(ExportJobCompletionErrorCode.CONFLICT)

    def replay_completed(self, job_id: UUID) -> ExportJobCompletionResult | None:
        """Read-only replay of an exact, already committed Export authority."""

        with self._session_factory() as session:
            job = JobRepository(session).get_job(job_id)
            if job is None or job.status is not JobStatus.SUCCEEDED:
                return None
            result = session.get(JobExportResult, job_id)
            publication = ExportPublicationRepository(session).get(job_id)
            output = session.scalar(
                select(JobOutput).where(
                    JobOutput.job_id == job_id,
                    JobOutput.output_order == 0,
                )
            )
            project = WorkspaceRepository(session).get_project(job.project_id)
            membership = (
                WorkspaceRepository(session).get_project_asset(project.export_project_asset_id)
                if project is not None and project.export_project_asset_id is not None
                else None
            )
            version = (
                AssetRepository(session).get_asset_version(result.exported_asset_version_id)
                if result is not None
                else None
            )
            artifact = (
                AssetRepository(session).get_artifact(result.exported_artifact_id)
                if result is not None
                else None
            )
            location = (
                ArtifactStorageRepository(session).get_storage_location(result.exported_artifact_id)
                if result is not None
                else None
            )
            identity = TrustedPublicationIdentity.for_wav_export(job_id)
            if (
                result is None
                or publication is None
                or publication.state is not ExportPublicationState.COMPLETED
                or publication.artifact_id is None
                or output is None
                or output.output_role != "export"
                or output.artifact_id != result.exported_artifact_id
                or publication.artifact_id != result.exported_artifact_id
                or job.composition_snapshot_id != result.composition_snapshot_id
                or publication.composition_snapshot_id != result.composition_snapshot_id
                or publication.export_format != result.export_format
                or result.export_format != "wav"
                or project is None
                or membership is None
                or membership.project_id != project.project_id
                or membership.role != "export"
                or version is None
                or version.asset_id != membership.asset_id
                or artifact is None
                or artifact.asset_version_id != version.asset_version_id
                or location is None
                or location.storage_backend != SUPPORTED_STORAGE_BACKEND
                or location.storage_domain != identity.storage_domain
                or location.storage_key != identity.storage_key
                or publication.storage_domain != identity.storage_domain
                or publication.storage_key != identity.storage_key
            ):
                raise ExportJobCompletionError(ExportJobCompletionErrorCode.CONFLICT)
            return ExportJobCompletionResult(result, output)

    def _preconditions(self, session: Session, request: ExportJobCompletionRequest):
        jobs = JobRepository(session)
        job = jobs.get_job(request.job_id)
        if job is None or job.job_type != "export" or job.composition_snapshot_id is None:
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.INVALID_JOB)
        if job.cancel_requested_at is not None:
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.CANCELLED)
        if (
            job.status is not JobStatus.RUNNING
            or job.claimed_by != request.claimed_by
            or job.claim_token != request.claim_token
        ):
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.STALE_CLAIM)
        project = WorkspaceRepository(session).get_project(job.project_id)
        workspace = WorkspaceRepository(session).get_workspace(job.workspace_id)
        publication = ExportPublicationRepository(session).get(job.job_id)
        if project is None or workspace is None or project.workspace_id != workspace.workspace_id:
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.INVALID_JOB)
        if (
            publication is None
            or publication.state is not ExportPublicationState.PUBLISHED
            or publication.composition_snapshot_id != job.composition_snapshot_id
            or publication.expected_sha256 is None
            or publication.expected_size_bytes is None
        ):
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.INVALID_PUBLICATION)
        return job, project, workspace.owner_id

    def _resolve_or_create_export_asset(self, session, project, owner_id):
        workspaces = WorkspaceRepository(session)
        assets = AssetRepository(session)
        if project.export_project_asset_id is not None:
            membership = workspaces.get_project_asset(project.export_project_asset_id)
            asset = assets.get_asset(membership.asset_id) if membership else None
            if (
                membership is None
                or membership.project_id != project.project_id
                or membership.role != "export"
                or asset is None
                or asset.asset_type is not AssetType.EXPORT
                or asset.workspace_id != project.workspace_id
                or asset.owner_id != owner_id
                or asset.lifecycle_status != "active"
            ):
                raise ExportJobCompletionError(ExportJobCompletionErrorCode.CORRUPT_EXPORT_ASSET)
            return asset
        asset = assets.add_asset(
            Asset(
                workspace_id=project.workspace_id,
                owner_id=owner_id,
                asset_type=AssetType.EXPORT,
                lifecycle_status="active",
            )
        )
        membership = workspaces.add_project_asset(
            ProjectAsset(
                project_id=project.project_id,
                asset_id=asset.asset_id,
                role="export",
                display_order=0,
            )
        )
        if (
            workspaces.assign_export_project_asset_if_unset(
                project.project_id, membership.project_asset_id
            )
            is None
        ):
            raise _FirstExportRace()
        return asset

    @staticmethod
    def _create_version(
        session: Session, asset: Asset, job_id: UUID, owner_id: UUID
    ) -> AssetVersion:
        latest = AssetRepository(session).get_latest_asset_version(asset.asset_id)
        version = AssetVersion(
            asset_id=asset.asset_id,
            version_number=1 if latest is None else latest.version_number + 1,
            version_origin="export",
            settings_snapshot={"export_job_id": str(job_id)},
            created_by=owner_id,
        )
        return AssetRepository(session).add_asset_version(version)

    def _replay_fresh(self, request):
        with self._session_factory() as session:
            return self._replay(session, request)

    @staticmethod
    def _replay(session: Session, request: ExportJobCompletionRequest):
        result = session.get(JobExportResult, request.job_id)
        if result is None:
            return None
        job = JobRepository(session).get_job(request.job_id)
        publication = ExportPublicationRepository(session).get(request.job_id)
        output = session.scalar(
            select(JobOutput).where(JobOutput.job_id == request.job_id, JobOutput.output_order == 0)
        )
        if (
            job is None
            or job.status is not JobStatus.SUCCEEDED
            or publication is None
            or publication.state is not ExportPublicationState.COMPLETED
            or output is None
            or output.output_role != "export"
            or output.artifact_id != result.exported_artifact_id
            or publication.artifact_id != result.exported_artifact_id
            or result.render_fingerprint != request.render_fingerprint
        ):
            raise ExportJobCompletionError(ExportJobCompletionErrorCode.CONFLICT)
        return ExportJobCompletionResult(result, output)
