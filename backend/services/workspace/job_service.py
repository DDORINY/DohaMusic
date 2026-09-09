"""Workspace Job lifecycle과 입출력 application use case."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.contracts.vocal_jobs import (
    DOHAVOCAL_PROVIDER_ID,
    VOCAL_JOB_INPUT_ADAPTER,
    VOCAL_JOB_INPUT_ROLES,
    VOCAL_JOB_INPUT_SETTINGS_KEY,
    VOCAL_JOB_TYPES,
    VocalJobInput,
)
from backend.core.cursor_pagination import CURSOR_SORT, CursorCodec, filter_fingerprint
from backend.core.exceptions import (
    ApplicationValidationError,
    CursorConfigurationError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    InvalidLimitError,
    InvalidStateError,
    ResourceConflictError,
    ResourceNotFoundError,
    WorkspaceBootstrapRequiredError,
)
from backend.models.workspace import (
    Job,
    JobInput,
    JobOutput,
    JobStatus,
    ModelUsage,
)
from backend.models.workspace.preview import (
    WorkingPreviewRender,
    WorkingPreviewRenderClip,
    WorkingPreviewRenderTrack,
)
from backend.repositories.idempotency_repository import IdempotencyRepository
from backend.repositories.workspace import (
    AssetRepository,
    CompositionRepository,
    JobRepository,
    WorkingPreviewRepository,
    WorkspaceRepository,
)
from backend.services.workspace.composition_service import (
    _normalize_idempotency_key,
    _validate_json_object,
)

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}
TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
OFFICIAL_JOB_TYPES = frozenset(
    {
        "lyrics_generation",
        "music_generation",
        "stem_separation",
        "voice_conversion",
        "vocal_generation",
        "vocal_correction",
        "vocal_analysis",
        "audio_analysis",
        "mix",
        "export",
        "working_preview",
    }
)
REQUIRED_SNAPSHOT_JOB_TYPES = frozenset({"mix", "export"})
JOB_INPUT_ROLES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "lyrics_generation": (frozenset(), frozenset()),
    "music_generation": (frozenset(), frozenset({"lyrics"})),
    "stem_separation": (frozenset({"source_audio"}), frozenset({"source_audio"})),
    **VOCAL_JOB_INPUT_ROLES,
    "audio_analysis": (frozenset({"source_audio"}), frozenset({"source_audio"})),
    "mix": (
        frozenset({"vocal", "instrumental"}),
        frozenset({"vocal", "instrumental", "stem"}),
    ),
    "export": (frozenset(), frozenset({"mix"})),
    "working_preview": (frozenset(), frozenset()),
}
BYTE_INPUT_ROLES = frozenset(
    {
        "source_audio",
        "melody_reference",
        "timing_reference",
        "source_vocal",
        "voice_reference",
        "vocal",
        "instrumental",
        "stem",
        "mix",
    }
)
SNAPSHOT_ROLES_BY_INPUT_ROLE: dict[str, frozenset[str]] = {
    "lyrics": frozenset({"lyrics"}),
    "lyrics_reference": frozenset({"lyrics"}),
    "melody_reference": frozenset({"music"}),
    "timing_reference": frozenset({"music", "vocal"}),
    "source_audio": frozenset({"music", "stem", "mix"}),
    "source_vocal": frozenset({"vocal"}),
    "voice_reference": frozenset({"vocal"}),
    "vocal": frozenset({"vocal"}),
    "instrumental": frozenset({"music"}),
    "stem": frozenset({"stem"}),
    "mix": frozenset({"mix"}),
}
MAX_JOB_INPUTS = 16
MAX_STAGE_LENGTH = 64
MAX_ERROR_CODE_LENGTH = 64
MAX_ERROR_MESSAGE_LENGTH = 512
MAX_ERROR_DETAILS_ID_LENGTH = 128
MAX_PROVIDER_ID_LENGTH = 128
MAX_MANIFEST_ID_LENGTH = 256
JOB_IDEMPOTENCY_TTL_HOURS = 24
_SENSITIVE_KEYS = frozenset(
    {"authorization", "credential", "password", "secret", "token", "api_key", "api-key"}
)
_UNSAFE_PUBLIC_TEXT = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:home|users?|var|tmp|opt|data|models?)/|"
    r"traceback|stack trace|authorization|bearer\s+|api[_-]?key|credential|"
    r"password|secret|\bpid\b|\bcuda\b|dataset path|model path|command(?: line)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobReferenceInput:
    input_order: int
    asset_version_id: UUID | None = None
    artifact_id: UUID | None = None
    input_role: str | None = None


@dataclass(frozen=True)
class JobReferenceOutput:
    output_order: int
    asset_version_id: UUID | None = None
    artifact_id: UUID | None = None


@dataclass(frozen=True)
class ModelUsageInput:
    provider_id: str
    model_manifest_id: str
    model_id: str
    model_version: str
    api_contract_version: str
    license_status: str
    commercial_usage_status: str
    asset_version_id: UUID | None = None
    checkpoint_version: str | None = None


@dataclass(frozen=True, slots=True)
class JobPage:
    items: tuple[Job, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


@dataclass(frozen=True, slots=True)
class JobAggregate:
    job: Job
    inputs: tuple[JobInput, ...]
    outputs: tuple[JobOutput, ...]
    model_usages: tuple[ModelUsage, ...]


@dataclass(frozen=True, slots=True)
class JobCreation:
    aggregate: JobAggregate
    replayed: bool
    response_status: int


@dataclass(frozen=True, slots=True)
class JobCancelResult:
    job: Job
    response_status: int


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ApplicationValidationError(f"{field_name}은(는) 비어 있을 수 없습니다.")
    return normalized


class JobService:
    """Job 요청 snapshot과 상태 전이를 transaction 단위로 관리한다."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cursor_codec: CursorCodec | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cursor_codec = cursor_codec

    def create_job_for_owner(
        self,
        *,
        effective_owner_id: UUID,
        project_id: UUID,
        job_type: str,
        api_contract_version: str,
        settings_snapshot: dict[str, Any],
        idempotency_key: str,
        inputs: Sequence[JobReferenceInput] = (),
        composition_snapshot_id: UUID | None = None,
        provider_id: str | None = None,
        model_manifest_id: str | None = None,
        job_input: VocalJobInput | dict[str, Any] | None = None,
    ) -> JobCreation:
        """향후 공개 Router가 사용할 owner-scoped Job 생성 경계다."""

        normalized_type = _normalize_job_type(job_type)
        if normalized_type == "working_preview":
            raise ApplicationValidationError(
                "working_preview Job은 전용 WorkingComposition Preview API로 생성해야 합니다."
            )
        normalized_inputs = self._normalize_contract_inputs(normalized_type, inputs)
        normalized_settings = _validate_job_settings(settings_snapshot)
        if normalized_type == "export" and normalized_settings != {"format": "wav"}:
            raise ApplicationValidationError(
                "Export settings_snapshot은 canonical wav format만 허용합니다."
            )
        normalized_key = _normalize_idempotency_key(idempotency_key)
        normalized_provider = _optional_bounded_text(
            provider_id, "Provider ID", MAX_PROVIDER_ID_LENGTH
        )
        normalized_manifest = _optional_bounded_text(
            model_manifest_id, "Model Manifest ID", MAX_MANIFEST_ID_LENGTH
        )
        normalized_settings, normalized_vocal_input = _normalize_vocal_contract(
            job_type=normalized_type,
            provider_id=normalized_provider,
            model_manifest_id=normalized_manifest,
            settings_snapshot=normalized_settings,
            job_input=job_input,
        )
        normalized_contract = _bounded_text(api_contract_version, "API contract version", 64)
        scope = f"workspace:job:create:{effective_owner_id}"
        try:
            with self.session_factory() as session, session.begin():
                project = self._require_project_scope(session, project_id, effective_owner_id)
                fingerprint = _job_create_fingerprint(
                    effective_owner_id=effective_owner_id,
                    workspace_id=project.workspace_id,
                    project_id=project_id,
                    job_type=normalized_type,
                    composition_snapshot_id=composition_snapshot_id,
                    inputs=normalized_inputs,
                    provider_id=normalized_provider,
                    model_manifest_id=normalized_manifest,
                    api_contract_version=normalized_contract,
                    settings_snapshot=normalized_settings,
                )
                idempotency_repository = IdempotencyRepository(session)
                claim = _claim_idempotency(
                    idempotency_repository,
                    scope=scope,
                    key=normalized_key,
                    fingerprint=fingerprint,
                )
                job_repository = JobRepository(session)
                if claim.replayed:
                    return self._replay_creation(job_repository, claim.record)
                snapshot_versions = self._validate_snapshot(
                    session,
                    project_id=project_id,
                    composition_snapshot_id=composition_snapshot_id,
                    required=normalized_type in REQUIRED_SNAPSHOT_JOB_TYPES,
                )
                asset_repository = AssetRepository(session)
                processing_chain_id = getattr(normalized_vocal_input, "processing_chain_id", None)
                if processing_chain_id is not None:
                    processing_chain = CompositionRepository(session).get_processing_chain(
                        processing_chain_id
                    )
                    if (
                        processing_chain is None
                        or processing_chain.created_by != effective_owner_id
                    ):
                        raise ResourceNotFoundError("ProcessingChain")
                resolved_versions: dict[str, UUID] = {}
                direct_references: dict[str, UUID] = {}
                for item in normalized_inputs:
                    version_id = self._validate_contract_reference(
                        session,
                        asset_repository,
                        effective_owner_id=effective_owner_id,
                        workspace_id=project.workspace_id,
                        project_id=project_id,
                        item=item,
                    )
                    resolved_versions[item.input_role or ""] = version_id
                    reference_id = item.artifact_id or item.asset_version_id
                    if reference_id is None:
                        raise ApplicationValidationError("JobInput reference is required.")
                    direct_references[item.input_role or ""] = reference_id
                    if snapshot_versions is not None and not _snapshot_has_input(
                        snapshot_versions, version_id, item.input_role
                    ):
                        raise ApplicationValidationError(
                            "JobInput의 exact AssetVersion이 CompositionSnapshot과 "
                            "일치하지 않습니다."
                        )
                parent_version_id = getattr(normalized_vocal_input, "parent_asset_version_id", None)
                if parent_version_id is not None:
                    validated_parent_version_id = self._validate_contract_reference(
                        session,
                        asset_repository,
                        effective_owner_id=effective_owner_id,
                        workspace_id=project.workspace_id,
                        project_id=project_id,
                        item=JobReferenceInput(
                            input_order=0,
                            input_role="source_vocal",
                            asset_version_id=parent_version_id,
                        ),
                    )
                    source_version_id = resolved_versions.get("source_vocal")
                    if source_version_id is None:
                        raise ApplicationValidationError(
                            "Vocal Job parent lineage에는 source_vocal이 필요합니다."
                        )
                    _validate_vocal_parent_lineage(
                        asset_repository,
                        source_version_id=source_version_id,
                        parent_version_id=validated_parent_version_id,
                    )
                _validate_vocal_input_references(
                    normalized_vocal_input,
                    direct_references=direct_references,
                    resolved_versions=resolved_versions,
                )
                job = job_repository.add_job(
                    Job(
                        project_id=project_id,
                        workspace_id=project.workspace_id,
                        composition_snapshot_id=composition_snapshot_id,
                        job_type=normalized_type,
                        status=JobStatus.QUEUED,
                        provider_id=normalized_provider,
                        api_contract_version=normalized_contract,
                        model_manifest_id=normalized_manifest,
                        progress_percent=Decimal(0),
                        stage=None,
                        settings_snapshot=normalized_settings,
                        retry_of_job_id=None,
                        requested_by=effective_owner_id,
                        cancel_requested_at=None,
                        claim_token=None,
                        claimed_by=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        attempt=0,
                    )
                )
                if normalized_type == "export":
                    from backend.models.workspace import (
                        ExportPublicationState,
                        JobExportPublication,
                    )
                    from backend.repositories.workspace import ExportPublicationRepository
                    from backend.storage.artifact_publisher import TrustedPublicationIdentity

                    if composition_snapshot_id is None:
                        raise ApplicationValidationError(
                            "Export Job에는 CompositionSnapshot이 필요합니다."
                        )
                    identity = TrustedPublicationIdentity.for_wav_export(job.job_id)
                    ExportPublicationRepository(session).add(
                        JobExportPublication(
                            job_id=job.job_id,
                            composition_snapshot_id=composition_snapshot_id,
                            export_format="wav",
                            storage_domain=identity.storage_domain,
                            storage_key=identity.storage_key,
                            state=ExportPublicationState.INTENDED,
                        )
                    )
                for item in normalized_inputs:
                    job_repository.add_job_input(
                        JobInput(
                            job_id=job.job_id,
                            input_order=item.input_order,
                            input_role=item.input_role,
                            asset_version_id=item.asset_version_id,
                            artifact_id=item.artifact_id,
                        )
                    )
                idempotency_repository.complete(
                    claim.record,
                    resource_type="workspace_job",
                    resource_id=str(job.job_id),
                    response_status=201,
                )
                aggregate = self._load_aggregate(job_repository, job)
            return JobCreation(aggregate, replayed=False, response_status=201)
        except IntegrityError:
            raise ResourceConflictError("Workspace Job") from None

    def get_job_aggregate_for_owner(
        self, job_id: UUID, *, effective_owner_id: UUID
    ) -> JobAggregate:
        with self.session_factory() as session:
            repository = JobRepository(session)
            job = repository.get_job_for_owner(job_id, effective_owner_id)
            if job is None:
                raise ResourceNotFoundError("Workspace Job")
            return self._load_aggregate(repository, job)

    def transition_job_for_owner(
        self,
        job_id: UUID,
        *,
        effective_owner_id: UUID,
        status: JobStatus,
        progress_percent: Decimal | None = None,
        stage: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        error_retryable: bool | None = None,
        error_details_id: str | None = None,
    ) -> Job:
        """Completion UoW의 succeeded를 제외한 명시적 상태 전이를 적용한다."""

        if status is JobStatus.SUCCEEDED:
            raise ApplicationValidationError(
                "succeeded 전이는 Completion Unit of Work에서만 허용합니다."
            )
        with self.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = self._require_job_owner(repository, job_id, effective_owner_id)
            self._ensure_mutable(job)
            self._validate_transition(job.status, status)
            if progress_percent is not None:
                self._apply_progress(job, progress_percent)
            if stage is not None:
                job.stage = _safe_public_text(stage, "stage", MAX_STAGE_LENGTH)
            now = datetime.now(UTC)
            repository.update_job_status(
                job,
                status,
                started_at=now if status is JobStatus.RUNNING else None,
                completed_at=now if status in TERMINAL_STATUSES else None,
            )
            if status is JobStatus.FAILED:
                job.error_code = _safe_error_code(error_code)
                job.error_message = _safe_public_text(
                    error_message or "작업이 실패했습니다.",
                    "공개 오류 메시지",
                    MAX_ERROR_MESSAGE_LENGTH,
                )
                job.error_retryable = bool(error_retryable)
                job.error_details_id = _safe_error_details_id(error_details_id)
                session.flush()
            return job

    def update_job_progress_for_owner(
        self,
        job_id: UUID,
        *,
        effective_owner_id: UUID,
        progress_percent: Decimal,
        stage: str | None = None,
    ) -> Job:
        with self.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = self._require_job_owner(repository, job_id, effective_owner_id)
            self._ensure_mutable(job)
            self._apply_progress(job, progress_percent)
            if stage is not None:
                job.stage = _safe_public_text(stage, "stage", MAX_STAGE_LENGTH)
            session.flush()
            return job

    def cancel_job_for_owner(self, job_id: UUID, *, effective_owner_id: UUID) -> JobCancelResult:
        """Cancel action은 상태 자체로 idempotent하며 별도 key를 요구하지 않는다."""

        with self.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = self._require_job_owner(repository, job_id, effective_owner_id)
            if job.status is JobStatus.CANCELLED:
                return JobCancelResult(job, 200)
            if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                raise InvalidStateError("취소할 수 없는 Workspace Job")
            now = datetime.now(UTC)
            if job.status is JobStatus.QUEUED and job.claim_token is None:
                job.cancel_requested_at = now
                repository.update_job_status(job, JobStatus.CANCELLED, completed_at=now)
                return JobCancelResult(job, 200)
            if job.cancel_requested_at is None:
                job.cancel_requested_at = now
                session.flush()
            return JobCancelResult(job, 202)

    def retry_job_for_owner(
        self,
        job_id: UUID,
        *,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> JobCreation:
        normalized_key = _normalize_idempotency_key(idempotency_key)
        scope = f"workspace:job:retry:{effective_owner_id}"
        try:
            with self.session_factory() as session, session.begin():
                repository = JobRepository(session)
                original = self._require_job_owner(repository, job_id, effective_owner_id)
                if original.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
                    raise InvalidStateError("재시도 원본 Workspace Job")
                original_type = _normalize_job_type(original.job_type)
                project = self._require_project_scope(
                    session, original.project_id, effective_owner_id
                )
                if original.workspace_id != project.workspace_id:
                    raise InvalidStateError("재시도 원본 Workspace Job")
                original_rows = repository.list_job_inputs(original.job_id, limit=MAX_JOB_INPUTS)
                original_inputs = self._normalize_contract_inputs(
                    original_type,
                    tuple(
                        JobReferenceInput(
                            input_order=item.input_order,
                            input_role=item.input_role,
                            asset_version_id=item.asset_version_id,
                            artifact_id=item.artifact_id,
                        )
                        for item in original_rows
                    ),
                )
                snapshot_versions = self._validate_snapshot(
                    session,
                    project_id=original.project_id,
                    composition_snapshot_id=original.composition_snapshot_id,
                    required=original_type in REQUIRED_SNAPSHOT_JOB_TYPES,
                )
                asset_repository = AssetRepository(session)
                for item in original_inputs:
                    version_id = self._validate_contract_reference(
                        session,
                        asset_repository,
                        effective_owner_id=effective_owner_id,
                        workspace_id=project.workspace_id,
                        project_id=project.project_id,
                        item=item,
                    )
                    if snapshot_versions is not None and not _snapshot_has_input(
                        snapshot_versions, version_id, item.input_role
                    ):
                        raise ApplicationValidationError(
                            "재시도 JobInput의 exact AssetVersion이 CompositionSnapshot과 "
                            "일치하지 않습니다."
                        )
                fingerprint = _job_retry_fingerprint(
                    effective_owner_id=effective_owner_id,
                    workspace_id=project.workspace_id,
                    original_job_id=original.job_id,
                    project_id=original.project_id,
                    job_type=original_type,
                    composition_snapshot_id=original.composition_snapshot_id,
                    inputs=original_inputs,
                    provider_id=original.provider_id,
                    model_manifest_id=original.model_manifest_id,
                    api_contract_version=original.api_contract_version,
                    settings_snapshot=original.settings_snapshot,
                )
                idempotency_repository = IdempotencyRepository(session)
                claim = _claim_idempotency(
                    idempotency_repository,
                    scope=scope,
                    key=normalized_key,
                    fingerprint=fingerprint,
                )
                if claim.replayed:
                    return self._replay_creation(repository, claim.record)
                retried = repository.add_job(
                    Job(
                        project_id=original.project_id,
                        workspace_id=original.workspace_id,
                        composition_snapshot_id=original.composition_snapshot_id,
                        job_type=original_type,
                        status=JobStatus.QUEUED,
                        provider_id=original.provider_id,
                        api_contract_version=original.api_contract_version,
                        model_manifest_id=original.model_manifest_id,
                        progress_percent=Decimal(0),
                        stage=None,
                        settings_snapshot=dict(original.settings_snapshot),
                        retry_of_job_id=original.job_id,
                        requested_by=effective_owner_id,
                        attempt=0,
                    )
                )
                for item in original_inputs:
                    repository.add_job_input(
                        JobInput(
                            job_id=retried.job_id,
                            input_order=item.input_order,
                            input_role=item.input_role,
                            asset_version_id=item.asset_version_id,
                            artifact_id=item.artifact_id,
                        )
                    )
                if original_type == "working_preview":
                    previews = WorkingPreviewRepository(session)
                    original_render = previews.get_render_for_job(original.job_id)
                    if original_render is None:
                        raise InvalidStateError("재시도 원본 Working Preview manifest")
                    retried_render = previews.add_render(
                        WorkingPreviewRender(
                            project_id=original_render.project_id,
                            working_composition_id=original_render.working_composition_id,
                            rendered_revision=original_render.rendered_revision,
                            workspace_job_id=retried.job_id,
                            preview_asset_id=original_render.preview_asset_id,
                            payload_expires_at=datetime.now(UTC) + timedelta(hours=24),
                        )
                    )
                    previews.add_tracks(
                        [
                            WorkingPreviewRenderTrack(
                                preview_render_id=retried_render.preview_render_id,
                                track_id=item.track_id,
                                track_order=item.track_order,
                            )
                            for item in previews.list_tracks(original_render.preview_render_id)
                        ]
                    )
                    previews.add_clips(
                        [
                            WorkingPreviewRenderClip(
                                preview_render_id=retried_render.preview_render_id,
                                clip_id=item.clip_id,
                                track_id=item.track_id,
                                canonical_order=item.canonical_order,
                                source_asset_version_id=item.source_asset_version_id,
                                source_artifact_id=item.source_artifact_id,
                                source_in_us=item.source_in_us,
                                source_out_us=item.source_out_us,
                                source_duration_us=item.source_duration_us,
                                timeline_start_us=item.timeline_start_us,
                                timeline_duration_us=item.timeline_duration_us,
                                loop_enabled=item.loop_enabled,
                                loop_phase_us=item.loop_phase_us,
                                gain_db=item.gain_db,
                                fade_in_us=item.fade_in_us,
                                fade_out_us=item.fade_out_us,
                            )
                            for item in previews.list_clips(original_render.preview_render_id)
                        ]
                    )
                idempotency_repository.complete(
                    claim.record,
                    resource_type="workspace_job",
                    resource_id=str(retried.job_id),
                    response_status=202,
                )
                aggregate = self._load_aggregate(repository, retried)
            return JobCreation(aggregate, replayed=False, response_status=202)
        except IntegrityError:
            raise ResourceConflictError("Workspace Job retry") from None

    def create_job(
        self,
        *,
        project_id: UUID,
        job_type: str,
        api_contract_version: str,
        settings_snapshot: dict[str, Any],
        requested_by: UUID,
        inputs: Sequence[JobReferenceInput] = (),
        model_usages: Sequence[ModelUsageInput] = (),
        composition_snapshot_id: UUID | None = None,
        provider_id: str | None = None,
        model_manifest_id: str | None = None,
        retry_of_job_id: UUID | None = None,
    ) -> Job:
        normalized_inputs = self._normalize_inputs(inputs)
        try:
            with self.session_factory() as session, session.begin():
                workspace_repository = WorkspaceRepository(session)
                asset_repository = AssetRepository(session)
                composition_repository = CompositionRepository(session)
                job_repository = JobRepository(session)
                project = workspace_repository.get_project(project_id)
                if project is None:
                    raise ResourceNotFoundError("MusicProject")
                if composition_snapshot_id is not None:
                    snapshot = composition_repository.get_snapshot(composition_snapshot_id)
                    if snapshot is None or snapshot.project_id != project_id:
                        raise ApplicationValidationError(
                            "CompositionSnapshot은 같은 Project에 속해야 합니다."
                        )
                if retry_of_job_id is not None:
                    retry_of = job_repository.get_job(retry_of_job_id)
                    if retry_of is None:
                        raise ResourceNotFoundError("재시도 원본 Job")
                    if retry_of.status not in {
                        JobStatus.FAILED,
                        JobStatus.CANCELLED,
                    }:
                        raise InvalidStateError("재시도 원본 Job")
                for item in normalized_inputs:
                    self._validate_reference(
                        asset_repository,
                        project.workspace_id,
                        asset_version_id=item.asset_version_id,
                        artifact_id=item.artifact_id,
                    )
                job = job_repository.add_job(
                    Job(
                        project_id=project_id,
                        workspace_id=project.workspace_id,
                        composition_snapshot_id=composition_snapshot_id,
                        job_type=_required_text(job_type, "Job 유형"),
                        status=JobStatus.QUEUED,
                        provider_id=provider_id.strip() if provider_id else None,
                        api_contract_version=_required_text(
                            api_contract_version, "API contract version"
                        ),
                        model_manifest_id=(
                            model_manifest_id.strip() if model_manifest_id else None
                        ),
                        settings_snapshot=dict(settings_snapshot),
                        retry_of_job_id=retry_of_job_id,
                        requested_by=requested_by,
                    )
                )
                for item in normalized_inputs:
                    job_repository.add_job_input(
                        JobInput(
                            job_id=job.job_id,
                            input_order=item.input_order,
                            asset_version_id=item.asset_version_id,
                            artifact_id=item.artifact_id,
                        )
                    )
                for usage_input in model_usages:
                    usage = self._build_model_usage(
                        job.job_id,
                        usage_input,
                        asset_repository,
                        project.workspace_id,
                    )
                    job_repository.add_model_usage(usage)
            return job
        except IntegrityError:
            raise ResourceConflictError("Workspace Job") from None

    def get_job(self, job_id: UUID) -> Job:
        with self.session_factory() as session:
            job = JobRepository(session).get_job(job_id)
            if job is None:
                raise ResourceNotFoundError("Workspace Job")
            return job

    def list_jobs(
        self,
        *,
        project_id: UUID | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        with self.session_factory() as session:
            repository = JobRepository(session)
            if project_id is not None:
                if WorkspaceRepository(session).get_project(project_id) is None:
                    raise ResourceNotFoundError("MusicProject")
                return repository.list_project_jobs(project_id, limit=limit, offset=offset)
            if status is not None:
                return repository.list_jobs_by_status(status, limit=limit, offset=offset)
            return repository.list_jobs(limit=limit, offset=offset)

    def list_job_page(
        self,
        *,
        effective_owner_id: UUID,
        workspace_id: UUID,
        project_id: UUID | None = None,
        status: JobStatus | None = None,
        job_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JobPage:
        """Owner·Workspace 범위를 고정한 Job DESC cursor page를 반환한다."""

        _validate_page_limit(limit)
        if status is not None and not isinstance(status, JobStatus):
            raise ApplicationValidationError("Job 상태 filter가 유효하지 않습니다.")
        normalized_job_type = _normalize_job_type(job_type) if job_type is not None else None
        codec = self._require_cursor_codec()
        filter_hash = filter_fingerprint(
            {
                "effective_owner_id": str(effective_owner_id),
                "job_type": normalized_job_type,
                "project_id": str(project_id) if project_id is not None else None,
                "sort": CURSOR_SORT,
                "status": status.value if status is not None else None,
                "workspace_id": str(workspace_id),
            }
        )
        position = (
            codec.decode(
                cursor,
                expected_resource="job",
                expected_filter_hash=filter_hash,
                expected_limit=limit,
            )
            if cursor is not None
            else None
        )
        with self.session_factory() as session:
            workspace_repository = WorkspaceRepository(session)
            if (
                workspace_repository.get_workspace_for_owner(
                    workspace_id,
                    effective_owner_id,
                )
                is None
            ):
                raise ResourceNotFoundError("Workspace")
            if project_id is not None:
                project = workspace_repository.get_project(project_id)
                if project is None or project.workspace_id != workspace_id:
                    raise ResourceNotFoundError("MusicProject")
            rows = JobRepository(session).list_jobs_after(
                owner_id=effective_owner_id,
                workspace_id=workspace_id,
                project_id=project_id,
                status=status,
                job_type=normalized_job_type,
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
                resource="job",
                last_created_at=last_item.created_at,
                last_id=last_item.job_id,
                filter_hash=filter_hash,
                limit=limit,
            )
        return JobPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
        )

    def _require_cursor_codec(self) -> CursorCodec:
        if self.cursor_codec is None:
            raise CursorConfigurationError()
        return self.cursor_codec

    def update_job_status(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        error_retryable: bool | None = None,
        stage: str | None = None,
        progress_percent: Decimal | None = None,
    ) -> Job:
        if status is JobStatus.SUCCEEDED:
            raise ApplicationValidationError(
                "성공 전이는 complete_job_with_outputs를 사용해야 합니다."
            )
        if progress_percent is not None and not Decimal(0) <= progress_percent <= Decimal(100):
            raise ApplicationValidationError("진행률은 0에서 100 사이여야 합니다.")
        with self.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = repository.get_job(job_id)
            if job is None:
                raise ResourceNotFoundError("Workspace Job")
            self._validate_transition(job.status, status)
            now = datetime.now(UTC)
            repository.update_job_status(
                job,
                status,
                started_at=now if status is JobStatus.RUNNING else None,
                completed_at=now if status in TERMINAL_STATUSES else None,
            )
            job.stage = stage.strip() if stage else job.stage
            if progress_percent is not None:
                job.progress_percent = progress_percent
            if status is JobStatus.FAILED:
                job.error_code = _required_text(error_code or "JOB_FAILED", "오류 코드")
                job.error_message = _required_text(
                    error_message or "작업이 실패했습니다.", "오류 메시지"
                )
                job.error_retryable = bool(error_retryable)
            session.flush()
        return job

    def add_job_output(self, job_id: UUID, output: JobReferenceOutput) -> JobOutput:
        normalized = self._normalize_outputs([output])[0]
        try:
            with self.session_factory() as session, session.begin():
                repository = JobRepository(session)
                job = repository.get_job(job_id)
                if job is None:
                    raise ResourceNotFoundError("Workspace Job")
                if job.status is not JobStatus.RUNNING:
                    raise InvalidStateError("Workspace Job")
                project = WorkspaceRepository(session).get_project(job.project_id)
                if project is None:
                    raise ResourceNotFoundError("MusicProject")
                self._validate_reference(
                    AssetRepository(session),
                    project.workspace_id,
                    asset_version_id=normalized.asset_version_id,
                    artifact_id=normalized.artifact_id,
                )
                if repository.job_output_order_exists(job_id, normalized.output_order):
                    raise ResourceConflictError("JobOutput 순서")
                created = repository.add_job_output(
                    JobOutput(
                        job_id=job_id,
                        output_order=normalized.output_order,
                        asset_version_id=normalized.asset_version_id,
                        artifact_id=normalized.artifact_id,
                    )
                )
            return created
        except IntegrityError:
            raise ResourceConflictError("JobOutput") from None

    def complete_job_with_outputs(self, job_id: UUID, outputs: Sequence[JobReferenceOutput]) -> Job:
        normalized_outputs = self._normalize_outputs(outputs)
        if not normalized_outputs:
            raise ApplicationValidationError("성공 Job에는 출력이 필요합니다.")
        try:
            with self.session_factory() as session, session.begin():
                job_repository = JobRepository(session)
                job = job_repository.get_job(job_id)
                if job is None:
                    raise ResourceNotFoundError("Workspace Job")
                self._validate_transition(job.status, JobStatus.SUCCEEDED)
                project = WorkspaceRepository(session).get_project(job.project_id)
                if project is None:
                    raise ResourceNotFoundError("MusicProject")
                asset_repository = AssetRepository(session)
                for output in normalized_outputs:
                    self._validate_reference(
                        asset_repository,
                        project.workspace_id,
                        asset_version_id=output.asset_version_id,
                        artifact_id=output.artifact_id,
                    )
                    if job_repository.job_output_order_exists(job_id, output.output_order):
                        raise ResourceConflictError("JobOutput 순서")
                    job_repository.add_job_output(
                        JobOutput(
                            job_id=job_id,
                            output_order=output.output_order,
                            asset_version_id=output.asset_version_id,
                            artifact_id=output.artifact_id,
                        )
                    )
                job.progress_percent = Decimal(100)
                job_repository.update_job_status(
                    job,
                    JobStatus.SUCCEEDED,
                    completed_at=datetime.now(UTC),
                )
            return job
        except IntegrityError:
            raise ResourceConflictError("Job 완료 결과") from None

    def record_model_usage(self, job_id: UUID, usage_input: ModelUsageInput) -> ModelUsage:
        try:
            with self.session_factory() as session, session.begin():
                job_repository = JobRepository(session)
                job = job_repository.get_job(job_id)
                if job is None:
                    raise ResourceNotFoundError("Workspace Job")
                if job.status in TERMINAL_STATUSES:
                    raise InvalidStateError("Workspace Job")
                project = WorkspaceRepository(session).get_project(job.project_id)
                if project is None:
                    raise ResourceNotFoundError("MusicProject")
                usage = self._build_model_usage(
                    job_id,
                    usage_input,
                    AssetRepository(session),
                    project.workspace_id,
                )
                for existing in job_repository.list_model_usages(job_id):
                    if (
                        existing.model_manifest_id == usage.model_manifest_id
                        and existing.asset_version_id == usage.asset_version_id
                    ):
                        raise ResourceConflictError("ModelUsage")
                job_repository.add_model_usage(usage)
            return usage
        except IntegrityError:
            raise ResourceConflictError("ModelUsage") from None

    def list_model_usages(
        self, job_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ModelUsage]:
        with self.session_factory() as session:
            repository = JobRepository(session)
            if repository.get_job(job_id) is None:
                raise ResourceNotFoundError("Workspace Job")
            return repository.list_model_usages(job_id, limit=limit, offset=offset)

    @staticmethod
    def _require_project_scope(session: Session, project_id: UUID, effective_owner_id: UUID):
        repository = WorkspaceRepository(session)
        if not repository.list_workspaces(owner_id=effective_owner_id, limit=1):
            raise WorkspaceBootstrapRequiredError()
        project = repository.get_project(project_id)
        if project is None or project.lifecycle_status != "active":
            raise ResourceNotFoundError("MusicProject")
        workspace = repository.get_workspace_for_owner(project.workspace_id, effective_owner_id)
        if workspace is None or workspace.lifecycle_status != "active":
            raise ResourceNotFoundError("MusicProject")
        return project

    @staticmethod
    def _require_job_owner(
        repository: JobRepository, job_id: UUID, effective_owner_id: UUID
    ) -> Job:
        job = repository.get_job_for_owner(job_id, effective_owner_id)
        if job is None:
            raise ResourceNotFoundError("Workspace Job")
        return job

    @staticmethod
    def _load_aggregate(repository: JobRepository, job: Job) -> JobAggregate:
        return JobAggregate(
            job=job,
            inputs=tuple(repository.list_job_inputs(job.job_id, limit=MAX_JOB_INPUTS)),
            outputs=tuple(repository.list_job_outputs(job.job_id, limit=100)),
            model_usages=tuple(repository.list_model_usages(job.job_id, limit=100)),
        )

    @staticmethod
    def _replay_creation(repository: JobRepository, record) -> JobCreation:
        if record.resource_type != "workspace_job" or record.resource_id is None:
            raise IdempotencyConflictError()
        try:
            job_id = UUID(record.resource_id)
        except ValueError:
            raise IdempotencyConflictError() from None
        job = repository.get_job(job_id)
        if job is None:
            raise IdempotencyConflictError()
        return JobCreation(
            JobService._load_aggregate(repository, job),
            replayed=True,
            response_status=record.response_status or 200,
        )

    @staticmethod
    def _validate_snapshot(
        session: Session,
        *,
        project_id: UUID,
        composition_snapshot_id: UUID | None,
        required: bool,
    ) -> set[tuple[UUID, str]] | None:
        if composition_snapshot_id is None:
            if required:
                raise ApplicationValidationError(
                    "이 Job 유형에는 CompositionSnapshot이 필요합니다."
                )
            return None
        repository = CompositionRepository(session)
        snapshot = repository.get_snapshot(composition_snapshot_id)
        if snapshot is None or snapshot.project_id != project_id:
            raise ResourceNotFoundError("CompositionSnapshot")
        return {
            (item.asset_version_id, item.item_role)
            for item in repository.list_snapshot_items(composition_snapshot_id, limit=64)
        }

    @staticmethod
    def _normalize_contract_inputs(
        job_type: str, inputs: Sequence[JobReferenceInput]
    ) -> list[JobReferenceInput]:
        if len(inputs) > MAX_JOB_INPUTS:
            raise ApplicationValidationError(f"JobInput은 최대 {MAX_JOB_INPUTS}개까지 허용합니다.")
        required_roles, allowed_roles = JOB_INPUT_ROLES[job_type]
        normalized: list[JobReferenceInput] = []
        orders: set[int] = set()
        roles: set[str] = set()
        for item in inputs:
            if type(item.input_order) is not int or item.input_order < 0:
                raise ApplicationValidationError("JobInput 순서는 0 이상 정수여야 합니다.")
            if item.input_order in orders:
                raise ResourceConflictError("JobInput 순서")
            role = _bounded_text(item.input_role, "JobInput 역할", 64)
            if role not in allowed_roles:
                raise ApplicationValidationError("Job 유형에 허용되지 않은 input_role입니다.")
            if role in roles:
                raise ApplicationValidationError("같은 input_role은 중복할 수 없습니다.")
            if (item.asset_version_id is None) == (item.artifact_id is None):
                raise ApplicationValidationError(
                    "JobInput에는 AssetVersion 또는 Artifact 중 하나만 필요합니다."
                )
            if role in BYTE_INPUT_ROLES and item.artifact_id is None:
                raise ApplicationValidationError(
                    "byte-level JobInput은 명시적 artifact_id가 필요합니다."
                )
            orders.add(item.input_order)
            roles.add(role)
            normalized.append(
                JobReferenceInput(
                    input_order=item.input_order,
                    asset_version_id=item.asset_version_id,
                    artifact_id=item.artifact_id,
                    input_role=role,
                )
            )
        missing = required_roles - roles
        if missing:
            raise ApplicationValidationError(
                "필수 JobInput 역할이 누락되었습니다: " + ", ".join(sorted(missing))
            )
        return sorted(normalized, key=lambda item: item.input_order)

    @staticmethod
    def _validate_contract_reference(
        session: Session,
        repository: AssetRepository,
        *,
        effective_owner_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        item: JobReferenceInput,
    ) -> UUID:
        if item.asset_version_id is not None:
            version = repository.get_asset_version(item.asset_version_id)
            if version is None:
                raise ResourceNotFoundError("AssetVersion")
        else:
            artifact = repository.get_artifact(item.artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("Artifact")
            version = repository.get_asset_version(artifact.asset_version_id)
            if version is None:
                raise ResourceNotFoundError("AssetVersion")
        asset = repository.get_asset(version.asset_id)
        if (
            asset is None
            or asset.deleted_at is not None
            or asset.owner_id != effective_owner_id
            or (asset.workspace_id is not None and asset.workspace_id != workspace_id)
        ):
            raise ResourceNotFoundError("JobInput")
        project_asset = WorkspaceRepository(session).find_project_asset(project_id, asset.asset_id)
        if project_asset is None:
            raise ResourceNotFoundError("ProjectAsset")
        return version.asset_version_id

    @staticmethod
    def _ensure_mutable(job: Job) -> None:
        if job.status in TERMINAL_STATUSES:
            raise InvalidStateError("종료된 Workspace Job")

    @staticmethod
    def _apply_progress(job: Job, progress_percent: Decimal) -> None:
        if not isinstance(progress_percent, Decimal):
            raise ApplicationValidationError("진행률은 Decimal 값이어야 합니다.")
        if not Decimal(0) <= progress_percent <= Decimal(100):
            raise ApplicationValidationError("진행률은 0에서 100 사이여야 합니다.")
        current = job.progress_percent or Decimal(0)
        if progress_percent < current:
            raise ApplicationValidationError("진행률은 감소할 수 없습니다.")
        job.progress_percent = progress_percent

    @staticmethod
    def _validate_transition(current: JobStatus, target: JobStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidStateError("Workspace Job")

    @staticmethod
    def _normalize_inputs(
        inputs: Sequence[JobReferenceInput],
    ) -> list[JobReferenceInput]:
        orders: set[int] = set()
        normalized: list[JobReferenceInput] = []
        for item in inputs:
            if item.input_order < 0:
                raise ApplicationValidationError("JobInput 순서는 0 이상이어야 합니다.")
            if item.input_order in orders:
                raise ResourceConflictError("JobInput 순서")
            if (item.asset_version_id is None) == (item.artifact_id is None):
                raise ApplicationValidationError(
                    "JobInput에는 AssetVersion 또는 Artifact 중 하나만 필요합니다."
                )
            orders.add(item.input_order)
            normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_outputs(
        outputs: Sequence[JobReferenceOutput],
    ) -> list[JobReferenceOutput]:
        orders: set[int] = set()
        normalized: list[JobReferenceOutput] = []
        for output in outputs:
            if output.output_order < 0:
                raise ApplicationValidationError("JobOutput 순서는 0 이상이어야 합니다.")
            if output.output_order in orders:
                raise ResourceConflictError("JobOutput 순서")
            if (output.asset_version_id is None) == (output.artifact_id is None):
                raise ApplicationValidationError(
                    "JobOutput에는 AssetVersion 또는 Artifact 중 하나만 필요합니다."
                )
            orders.add(output.output_order)
            normalized.append(output)
        return normalized

    @staticmethod
    def _validate_reference(
        repository: AssetRepository,
        workspace_id: UUID,
        *,
        asset_version_id: UUID | None,
        artifact_id: UUID | None,
    ) -> None:
        if asset_version_id is not None:
            version = repository.get_asset_version(asset_version_id)
            if version is None:
                raise ResourceNotFoundError("AssetVersion")
        else:
            artifact = repository.get_artifact(artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("Artifact")
            version = repository.get_asset_version(artifact.asset_version_id)
            if version is None:
                raise ResourceNotFoundError("AssetVersion")
        asset = repository.get_asset(version.asset_id)
        if asset is None:
            raise ResourceNotFoundError("Asset")
        if asset.workspace_id is not None and asset.workspace_id != workspace_id:
            raise ApplicationValidationError("Job 입출력의 Workspace 범위가 일치하지 않습니다.")

    @staticmethod
    def _build_model_usage(
        job_id: UUID,
        value: ModelUsageInput,
        asset_repository: AssetRepository,
        workspace_id: UUID,
    ) -> ModelUsage:
        if value.asset_version_id is not None:
            JobService._validate_reference(
                asset_repository,
                workspace_id,
                asset_version_id=value.asset_version_id,
                artifact_id=None,
            )
        return ModelUsage(
            job_id=job_id,
            asset_version_id=value.asset_version_id,
            provider_id=_required_text(value.provider_id, "Provider ID"),
            model_manifest_id=_required_text(value.model_manifest_id, "Model Manifest ID"),
            model_id=_required_text(value.model_id, "Model ID"),
            model_version=_required_text(value.model_version, "Model version"),
            checkpoint_version=(
                value.checkpoint_version.strip() if value.checkpoint_version else None
            ),
            api_contract_version=_required_text(value.api_contract_version, "API contract version"),
            license_status=_required_text(value.license_status, "License 상태"),
            commercial_usage_status=_required_text(value.commercial_usage_status, "상업 이용 상태"),
        )


def _validate_page_limit(limit: object) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidLimitError()


def _optional_filter_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApplicationValidationError(f"{field_name}가 유효하지 않습니다.")
    return _required_text(value, field_name)


def _normalize_job_type(value: object) -> str:
    normalized = _bounded_text(value, "Job 유형", 64)
    if normalized not in OFFICIAL_JOB_TYPES:
        raise ApplicationValidationError("지원하지 않는 Job 유형입니다.")
    return normalized


def _bounded_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ApplicationValidationError(f"{field_name} 형식이 유효하지 않습니다.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ApplicationValidationError(f"{field_name} 길이가 유효하지 않습니다.")
    return normalized


def _optional_bounded_text(value: object, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field_name, maximum)


def _safe_public_text(value: object, field_name: str, maximum: int) -> str:
    normalized = _bounded_text(value, field_name, maximum)
    if any(ord(character) < 32 and character not in "\t" for character in normalized):
        raise ApplicationValidationError(f"{field_name}에 제어 문자를 사용할 수 없습니다.")
    if _UNSAFE_PUBLIC_TEXT.search(normalized):
        raise ApplicationValidationError(f"{field_name}에 민감한 실행 정보를 포함할 수 없습니다.")
    return normalized


def _safe_error_code(value: object) -> str:
    normalized = _bounded_text(value or "JOB_FAILED", "오류 코드", MAX_ERROR_CODE_LENGTH)
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized) is None:
        raise ApplicationValidationError("오류 코드는 안전한 대문자 식별자여야 합니다.")
    return normalized


def _safe_error_details_id(value: object) -> str | None:
    normalized = _optional_bounded_text(value, "오류 상세 참조", MAX_ERROR_DETAILS_ID_LENGTH)
    if normalized is None:
        return None
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", normalized) is None:
        raise ApplicationValidationError("오류 상세 참조는 opaque ID여야 합니다.")
    return normalized


def _validate_job_settings(value: object) -> dict[str, Any]:
    normalized = _validate_json_object(value, "settings_snapshot")

    def inspect(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized_key = key.strip().lower().replace("-", "_")
                if normalized_key in _SENSITIVE_KEYS or any(
                    normalized_key.endswith(f"_{suffix}")
                    for suffix in (
                        "authorization",
                        "credential",
                        "password",
                        "secret",
                        "token",
                        "api_key",
                    )
                ):
                    raise ApplicationValidationError(
                        "settings_snapshot에 비밀정보 필드를 포함할 수 없습니다."
                    )
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)

    inspect(normalized)
    return normalized


def _normalize_vocal_contract(
    *,
    job_type: str,
    provider_id: str | None,
    model_manifest_id: str | None,
    settings_snapshot: dict[str, Any],
    job_input: VocalJobInput | dict[str, Any] | None,
) -> tuple[dict[str, Any], VocalJobInput | None]:
    if VOCAL_JOB_INPUT_SETTINGS_KEY in settings_snapshot:
        raise ApplicationValidationError(
            "settings_snapshot에 예약된 Vocal Job 입력 키를 사용할 수 없습니다."
        )
    if job_type not in VOCAL_JOB_TYPES:
        if job_input is not None:
            raise ApplicationValidationError(
                "Vocal Job이 아닌 요청에는 job_input을 사용할 수 없습니다."
            )
        return settings_snapshot, None

    structured_contract_required = (
        job_type != "voice_conversion"
        or provider_id == DOHAVOCAL_PROVIDER_ID
        or job_input is not None
    )
    if not structured_contract_required:
        return settings_snapshot, None
    if provider_id != DOHAVOCAL_PROVIDER_ID:
        raise ApplicationValidationError(
            "구조화된 Vocal Job은 dohavocal Provider를 사용해야 합니다."
        )
    if model_manifest_id is None:
        raise ApplicationValidationError("구조화된 Vocal Job에는 Model Manifest ID가 필요합니다.")
    if job_input is None:
        raise ApplicationValidationError("구조화된 Vocal Job에는 job_input이 필요합니다.")
    try:
        normalized_input = VOCAL_JOB_INPUT_ADAPTER.validate_python(job_input)
    except ValidationError:
        raise ApplicationValidationError(
            "Vocal Job의 job_input 계약이 유효하지 않습니다."
        ) from None
    if normalized_input.job_type != job_type:
        raise ApplicationValidationError("job_type과 job_input.job_type이 일치해야 합니다.")
    stored_settings = dict(settings_snapshot)
    stored_settings[VOCAL_JOB_INPUT_SETTINGS_KEY] = normalized_input.model_dump(mode="json")
    return stored_settings, normalized_input


def _validate_vocal_input_references(
    job_input: VocalJobInput | None,
    *,
    direct_references: dict[str, UUID],
    resolved_versions: dict[str, UUID],
) -> None:
    if job_input is None:
        return
    if job_input.job_type == "vocal_generation":
        for role in (
            "lyrics_reference",
            "melody_reference",
            "timing_reference",
            "voice_reference",
        ):
            expected = getattr(job_input, role)
            actual = direct_references.get(role)
            if expected is None and actual is None:
                continue
            if expected != actual:
                raise ApplicationValidationError(
                    f"job_input.{role}와 JobInput reference가 일치해야 합니다."
                )
        return
    if resolved_versions.get("source_vocal") != job_input.source_asset_version_id:
        raise ApplicationValidationError(
            "job_input.source_asset_version_id와 source_vocal lineage가 일치해야 합니다."
        )
    if job_input.job_type == "voice_conversion" and (
        direct_references.get("voice_reference") != job_input.voice_reference_artifact_id
    ):
        raise ApplicationValidationError(
            "job_input.voice_reference_artifact_id와 voice_reference가 일치해야 합니다."
        )


def _validate_vocal_parent_lineage(
    repository: AssetRepository,
    *,
    source_version_id: UUID,
    parent_version_id: UUID,
) -> None:
    source = repository.get_asset_version(source_version_id)
    parent = repository.get_asset_version(parent_version_id)
    if source is None or parent is None or source.asset_id != parent.asset_id:
        raise ApplicationValidationError(
            "parent_asset_version_id는 source AssetVersion 계보에 속해야 합니다."
        )

    visited: set[UUID] = set()
    current = parent
    while current.asset_version_id != source_version_id:
        if current.asset_version_id in visited or current.parent_asset_version_id is None:
            raise ApplicationValidationError(
                "parent_asset_version_id는 source AssetVersion의 파생본이어야 합니다."
            )
        visited.add(current.asset_version_id)
        ancestor = repository.get_asset_version(current.parent_asset_version_id)
        if ancestor is None or ancestor.asset_id != source.asset_id:
            raise ApplicationValidationError("parent_asset_version_id의 계보가 유효하지 않습니다.")
        current = ancestor


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_has_input(
    snapshot_items: set[tuple[UUID, str]],
    asset_version_id: UUID,
    input_role: str | None,
) -> bool:
    if input_role is None:
        return False
    allowed_snapshot_roles = SNAPSHOT_ROLES_BY_INPUT_ROLE.get(input_role, frozenset())
    return any(
        (asset_version_id, snapshot_role) in snapshot_items
        for snapshot_role in allowed_snapshot_roles
    )


def _job_create_fingerprint(
    *,
    effective_owner_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    job_type: str,
    composition_snapshot_id: UUID | None,
    inputs: Sequence[JobReferenceInput],
    provider_id: str | None,
    model_manifest_id: str | None,
    api_contract_version: str,
    settings_snapshot: dict[str, Any],
) -> str:
    return _canonical_fingerprint(
        {
            "api_contract_version": api_contract_version,
            "composition_snapshot_id": (
                str(composition_snapshot_id) if composition_snapshot_id is not None else None
            ),
            "effective_owner_id": str(effective_owner_id),
            "inputs": [
                {
                    "artifact_id": str(item.artifact_id) if item.artifact_id else None,
                    "asset_version_id": (
                        str(item.asset_version_id) if item.asset_version_id else None
                    ),
                    "input_order": item.input_order,
                    "input_role": item.input_role,
                }
                for item in inputs
            ],
            "job_type": job_type,
            "model_manifest_id": model_manifest_id,
            "project_id": str(project_id),
            "provider_id": provider_id,
            "settings_snapshot": settings_snapshot,
            "workspace_id": str(workspace_id),
        }
    )


def _job_retry_fingerprint(
    *,
    effective_owner_id: UUID,
    workspace_id: UUID,
    original_job_id: UUID,
    project_id: UUID,
    job_type: str,
    composition_snapshot_id: UUID | None,
    inputs: Sequence[JobReferenceInput],
    provider_id: str | None,
    model_manifest_id: str | None,
    api_contract_version: str,
    settings_snapshot: dict[str, Any],
) -> str:
    return _canonical_fingerprint(
        {
            "api_contract_version": api_contract_version,
            "composition_snapshot_id": (
                str(composition_snapshot_id) if composition_snapshot_id is not None else None
            ),
            "effective_owner_id": str(effective_owner_id),
            "inputs": [
                {
                    "artifact_id": str(item.artifact_id) if item.artifact_id else None,
                    "asset_version_id": (
                        str(item.asset_version_id) if item.asset_version_id else None
                    ),
                    "input_order": item.input_order,
                    "input_role": item.input_role,
                }
                for item in inputs
            ],
            "job_type": job_type,
            "model_manifest_id": model_manifest_id,
            "original_job_id": str(original_job_id),
            "project_id": str(project_id),
            "provider_id": provider_id,
            "settings_snapshot": settings_snapshot,
            "workspace_id": str(workspace_id),
        }
    )


def _claim_idempotency(
    repository: IdempotencyRepository,
    *,
    scope: str,
    key: str,
    fingerprint: str,
):
    try:
        return repository.claim(
            scope=scope,
            key=key,
            fingerprint=fingerprint,
            now=datetime.now(UTC),
            ttl_hours=JOB_IDEMPOTENCY_TTL_HOURS,
        )
    except ValueError as error:
        if str(error) == "IDEMPOTENCY_CONFLICT":
            raise IdempotencyConflictError() from None
        raise IdempotencyInProgressError() from None
