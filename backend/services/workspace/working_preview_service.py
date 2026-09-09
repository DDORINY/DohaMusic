"""Revision-pinned WorkingComposition preview Job lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.audio.working_preview_renderer import MAX_PREVIEW_OUTPUT_BYTES
from backend.core.exceptions import IdempotencyConflictError, IdempotencyInProgressError
from backend.models.workspace import Asset, AssetType, Job, JobOutput, JobStatus
from backend.models.workspace.asset import AssetVersion
from backend.models.workspace.identifiers import generate_uuid
from backend.models.workspace.preview import (
    WorkingPreviewAsset,
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
from backend.services.workspace.artifact_ingestion_service import (
    ArtifactIngestionError,
    ArtifactIngestionRequest,
    ArtifactIngestionService,
    PreparedArtifactIngestion,
)
from backend.services.workspace.trusted_media_metadata_service import (
    TrustedMediaMetadataError,
    TrustedMediaMetadataService,
)

WORKING_PREVIEW_JOB_TYPE = "working_preview"
WORKING_PREVIEW_OUTPUT_ROLE = "working_preview"
WORKING_PREVIEW_CONTRACT_VERSION = "working-preview.v1"
WORKING_PREVIEW_RETENTION_HOURS = 24
MAX_PREVIEW_TRACKS = 64
MAX_PREVIEW_CLIPS = 512
MAX_PREVIEW_DURATION_US = 30 * 60 * 1_000_000
_AUDIO_ASSET_TYPES = frozenset(
    {AssetType.MUSIC, AssetType.VOCAL, AssetType.STEM, AssetType.RECORDING, AssetType.MIX}
)


class WorkingPreviewErrorCode(StrEnum):
    NOT_FOUND = "WORKING_COMPOSITION_NOT_FOUND"
    REVISION_CONFLICT = "WORKING_COMPOSITION_REVISION_CONFLICT"
    EMPTY = "WORKING_PREVIEW_EMPTY"
    LIMIT_EXCEEDED = "WORKING_PREVIEW_LIMIT_EXCEEDED"
    SOURCE_UNAVAILABLE = "WORKING_PREVIEW_SOURCE_UNAVAILABLE"
    MANIFEST_CONFLICT = "WORKING_PREVIEW_MANIFEST_CONFLICT"
    JOB_STATE_CONFLICT = "WORKING_PREVIEW_JOB_STATE_CONFLICT"
    OUTPUT_INVALID = "WORKING_PREVIEW_OUTPUT_INVALID"


_MESSAGES = {
    WorkingPreviewErrorCode.NOT_FOUND: "WorkingComposition을 찾을 수 없습니다.",
    WorkingPreviewErrorCode.REVISION_CONFLICT: "WorkingComposition revision이 일치하지 않습니다.",
    WorkingPreviewErrorCode.EMPTY: "빈 WorkingComposition은 Preview할 수 없습니다.",
    WorkingPreviewErrorCode.LIMIT_EXCEEDED: "Working Preview resource limit을 초과했습니다.",
    WorkingPreviewErrorCode.SOURCE_UNAVAILABLE: "Preview source Artifact를 사용할 수 없습니다.",
    WorkingPreviewErrorCode.MANIFEST_CONFLICT: (
        "Working Preview manifest가 현재 Project와 일치하지 않습니다."
    ),
    WorkingPreviewErrorCode.JOB_STATE_CONFLICT: (
        "Working Preview Job 상태가 완료 조건과 일치하지 않습니다."
    ),
    WorkingPreviewErrorCode.OUTPUT_INVALID: "Working Preview output을 안전하게 등록할 수 없습니다.",
}


class WorkingPreviewError(RuntimeError):
    def __init__(self, code: WorkingPreviewErrorCode) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkingPreviewCreation:
    job_id: UUID
    preview_render_id: UUID
    working_composition_id: UUID
    rendered_revision: int
    status: JobStatus
    replayed: bool
    response_status: int = 202


@dataclass(frozen=True, slots=True)
class WorkingPreviewCompletion:
    job_id: UUID
    preview_render_id: UUID
    preview_asset_id: UUID
    preview_asset_version_id: UUID
    artifact_id: UUID
    rendered_revision: int


class WorkingPreviewService:
    """Create immutable manifests and atomically publish successful WAV results."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        ingestion_service: ArtifactIngestionService | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.ingestion_service = ingestion_service
        self.clock = clock

    def create_for_owner(
        self,
        *,
        project_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingPreviewCreation:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key is invalid")
        try:
            with self.session_factory() as session, session.begin():
                workspace_repository = WorkspaceRepository(session)
                project = workspace_repository.get_project(project_id)
                if project is None or project.lifecycle_status != "active":
                    raise WorkingPreviewError(WorkingPreviewErrorCode.NOT_FOUND)
                workspace = workspace_repository.get_workspace_for_owner(
                    project.workspace_id, effective_owner_id
                )
                if workspace is None or workspace.lifecycle_status != "active":
                    raise WorkingPreviewError(WorkingPreviewErrorCode.NOT_FOUND)

                compositions = CompositionRepository(session)
                working = compositions.get_project_working_composition(project_id)
                if working is None:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.NOT_FOUND)
                fingerprint = _fingerprint(
                    project_id, working.working_composition_id, expected_revision
                )

                idempotency = IdempotencyRepository(session)
                try:
                    claim = idempotency.claim(
                        scope=f"working-preview:create:{effective_owner_id}",
                        key=key,
                        fingerprint=fingerprint,
                        now=self.clock(),
                        ttl_hours=WORKING_PREVIEW_RETENTION_HOURS,
                    )
                except ValueError as error:
                    if str(error) == "IDEMPOTENCY_CONFLICT":
                        raise IdempotencyConflictError() from None
                    if str(error) == "IDEMPOTENCY_IN_PROGRESS":
                        raise IdempotencyInProgressError() from None
                    raise WorkingPreviewError(WorkingPreviewErrorCode.MANIFEST_CONFLICT) from error
                if claim.replayed:
                    job_id = UUID(str(claim.record.resource_id))
                    job = JobRepository(session).get_job_for_owner(job_id, effective_owner_id)
                    render = WorkingPreviewRepository(session).get_render_for_job(job_id)
                    if job is None or render is None:
                        raise WorkingPreviewError(WorkingPreviewErrorCode.MANIFEST_CONFLICT)
                    return _creation(job, render, replayed=True)

                if working.revision != expected_revision:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.REVISION_CONFLICT)
                tracks = compositions.list_active_composition_tracks(working.working_composition_id)
                clips = compositions.list_working_composition_clips(working.working_composition_id)
                duration_us = max(
                    (clip.timeline_start + clip.timeline_duration for clip in clips),
                    default=0,
                )
                if not tracks or not clips or duration_us <= 0:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.EMPTY)
                if (
                    len(tracks) > MAX_PREVIEW_TRACKS
                    or len(clips) > MAX_PREVIEW_CLIPS
                    or duration_us > MAX_PREVIEW_DURATION_US
                ):
                    raise WorkingPreviewError(WorkingPreviewErrorCode.LIMIT_EXCEEDED)

                exact_sources = [
                    self._resolve_exact_source(
                        session,
                        project_id=project_id,
                        workspace_id=project.workspace_id,
                        owner_id=effective_owner_id,
                        asset_version_id=clip.source_asset_version_id,
                        frozen_duration_us=clip.source_duration,
                    )
                    for clip in clips
                ]
                preview_repository = WorkingPreviewRepository(session)
                binding = preview_repository.get_asset_binding(project_id)
                assets = AssetRepository(session)
                if binding is None:
                    preview_asset = assets.add_asset(
                        Asset(
                            workspace_id=project.workspace_id,
                            owner_id=effective_owner_id,
                            asset_type=AssetType.MIX,
                            selected_asset_version_id=None,
                            lifecycle_status="active",
                        )
                    )
                    binding = preview_repository.add_asset_binding(
                        WorkingPreviewAsset(project_id=project_id, asset_id=preview_asset.asset_id)
                    )
                else:
                    preview_asset = assets.get_asset(binding.asset_id)
                    if (
                        preview_asset is None
                        or preview_asset.owner_id != effective_owner_id
                        or preview_asset.workspace_id != project.workspace_id
                        or preview_asset.asset_type is not AssetType.MIX
                        or preview_asset.lifecycle_status != "active"
                    ):
                        raise WorkingPreviewError(WorkingPreviewErrorCode.MANIFEST_CONFLICT)

                job = JobRepository(session).add_job(
                    Job(
                        project_id=project_id,
                        workspace_id=project.workspace_id,
                        composition_snapshot_id=None,
                        job_type=WORKING_PREVIEW_JOB_TYPE,
                        status=JobStatus.QUEUED,
                        provider_id=None,
                        api_contract_version=WORKING_PREVIEW_CONTRACT_VERSION,
                        model_manifest_id=None,
                        progress_percent=0,
                        stage=None,
                        settings_snapshot={"manifest_schema": 5},
                        retry_of_job_id=None,
                        requested_by=effective_owner_id,
                        attempt=0,
                    )
                )
                render = preview_repository.add_render(
                    WorkingPreviewRender(
                        project_id=project_id,
                        working_composition_id=working.working_composition_id,
                        rendered_revision=expected_revision,
                        master_gain_db=working.master_gain_db,
                        workspace_job_id=job.job_id,
                        preview_asset_id=preview_asset.asset_id,
                        payload_expires_at=self.clock()
                        + timedelta(hours=WORKING_PREVIEW_RETENTION_HOURS),
                    )
                )
                preview_repository.add_tracks(
                    [
                        WorkingPreviewRenderTrack(
                            preview_render_id=render.preview_render_id,
                            track_id=track.track_id,
                            track_order=track.track_order,
                            gain_db=track.gain_db,
                            pan=track.pan,
                            muted=track.muted,
                            solo=track.solo,
                        )
                        for track in tracks
                    ]
                )
                preview_repository.add_clips(
                    [
                        WorkingPreviewRenderClip(
                            preview_render_id=render.preview_render_id,
                            clip_id=clip.clip_id,
                            track_id=clip.track_id,
                            canonical_order=order,
                            source_asset_version_id=clip.source_asset_version_id,
                            source_artifact_id=source.artifact_id,
                            source_in_us=clip.source_in,
                            source_out_us=clip.source_out,
                            source_duration_us=clip.source_duration,
                            timeline_start_us=clip.timeline_start,
                            timeline_duration_us=clip.timeline_duration,
                            loop_enabled=clip.loop_enabled,
                            loop_phase_us=clip.loop_phase,
                            gain_db=clip.gain_db,
                            fade_in_us=clip.fade_in,
                            fade_out_us=clip.fade_out,
                        )
                        for order, (clip, source) in enumerate(
                            zip(clips, exact_sources, strict=True)
                        )
                    ]
                )
                idempotency.complete(
                    claim.record,
                    resource_type="working_preview_job",
                    resource_id=str(job.job_id),
                    response_status=202,
                )
                return _creation(job, render, replayed=False)
        except IntegrityError as error:
            raise WorkingPreviewError(WorkingPreviewErrorCode.MANIFEST_CONFLICT) from error

    def complete_render(
        self,
        *,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        output_path: Path,
    ) -> WorkingPreviewCompletion:
        if self.ingestion_service is None:
            raise WorkingPreviewError(WorkingPreviewErrorCode.OUTPUT_INVALID)
        asset_version_id = generate_uuid()
        artifact_id: UUID | None = None
        prepared: PreparedArtifactIngestion | None = None
        try:
            with self.session_factory() as session:
                job = JobRepository(session).get_job(job_id)
                render = WorkingPreviewRepository(session).get_render_for_job(job_id)
                if job is None or render is None:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.NOT_FOUND)
                if (
                    job.status is not JobStatus.RUNNING
                    or job.claimed_by != claimed_by
                    or job.claim_token != claim_token
                    or job.cancel_requested_at is not None
                    or render.preview_asset_version_id is not None
                ):
                    raise WorkingPreviewError(WorkingPreviewErrorCode.JOB_STATE_CONFLICT)
                owner_id = job.requested_by
                preview_asset_id = render.preview_asset_id
                rendered_revision = render.rendered_revision
                preview_render_id = render.preview_render_id
                manifest_clips = WorkingPreviewRepository(session).list_clips(preview_render_id)
                expected_duration_us = max(
                    (
                        item.timeline_start_us + item.source_out_us - item.source_in_us
                        for item in manifest_clips
                    ),
                    default=0,
                )
            prepared = self.ingestion_service.prepare(
                ArtifactIngestionRequest(
                    asset_version_id=asset_version_id,
                    artifact_kind="audio",
                    storage_domain="music",
                    temporary_path=output_path,
                    expected_media_type="audio/wav",
                    producer_type="workspace",
                    producer_id=None,
                    run_id=str(job_id),
                )
            )
            if (
                prepared.published.media.duration_us != expected_duration_us
                or not 0 < prepared.published.size_bytes <= MAX_PREVIEW_OUTPUT_BYTES
            ):
                raise WorkingPreviewError(WorkingPreviewErrorCode.OUTPUT_INVALID)
            artifact_id = prepared.artifact_id
            with self.session_factory() as session, session.begin():
                jobs = JobRepository(session)
                job = jobs.get_job(job_id)
                render = WorkingPreviewRepository(session).get_render_for_job(job_id)
                if job is None or render is None:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.NOT_FOUND)
                if (
                    job.status is not JobStatus.RUNNING
                    or job.claimed_by != claimed_by
                    or job.claim_token != claim_token
                    or job.cancel_requested_at is not None
                    or render.preview_asset_version_id is not None
                ):
                    raise WorkingPreviewError(WorkingPreviewErrorCode.JOB_STATE_CONFLICT)
                assets = AssetRepository(session)
                latest = assets.get_latest_asset_version(preview_asset_id)
                version_number = 1 if latest is None else latest.version_number + 1
                version = assets.add_asset_version(
                    AssetVersion(
                        asset_version_id=asset_version_id,
                        asset_id=preview_asset_id,
                        version_number=version_number,
                        version_origin="working_preview",
                        parent_asset_version_id=None,
                        processing_chain_id=None,
                        provider_id=None,
                        model_manifest_id=None,
                        settings_snapshot={"preview_render_id": str(preview_render_id)},
                        created_by=owner_id,
                    )
                )
                artifact = self.ingestion_service.register_prepared(session, prepared)
                self.ingestion_service.verify_registered(session, artifact, prepared)
                jobs.add_job_output(
                    JobOutput(
                        job_id=job_id,
                        output_order=0,
                        output_role=WORKING_PREVIEW_OUTPUT_ROLE,
                        asset_version_id=None,
                        artifact_id=artifact.artifact_id,
                    )
                )
                WorkingPreviewRepository(session).attach_asset_version(
                    preview_render_id, version.asset_version_id
                )
                finished = jobs.finish_owned_claim(
                    job_id,
                    claimed_by=claimed_by,
                    claim_token=claim_token,
                    status=JobStatus.SUCCEEDED,
                    now=self.clock(),
                )
                if finished is None:
                    raise WorkingPreviewError(WorkingPreviewErrorCode.JOB_STATE_CONFLICT)
                finished.progress_percent = 100
            self.ingestion_service.finalize_prepared(prepared)
            return WorkingPreviewCompletion(
                job_id=job_id,
                preview_render_id=preview_render_id,
                preview_asset_id=preview_asset_id,
                preview_asset_version_id=asset_version_id,
                artifact_id=artifact_id,
                rendered_revision=rendered_revision,
            )
        except WorkingPreviewError:
            if prepared is not None:
                self.ingestion_service.compensate_prepared(prepared, reason_code="PREVIEW_FAILED")
            raise
        except (ArtifactIngestionError, IntegrityError) as error:
            if prepared is not None:
                self.ingestion_service.compensate_prepared(prepared, reason_code="PREVIEW_FAILED")
            raise WorkingPreviewError(WorkingPreviewErrorCode.OUTPUT_INVALID) from error

    def expire_due_preview_payloads(self, *, limit: int = 100) -> int:
        """Make due Preview payloads inaccessible while retaining immutable provenance."""

        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        changed = 0
        with self.session_factory() as session, session.begin():
            renders = WorkingPreviewRepository(session).list_due_completed(
                self.clock(), limit=limit
            )
            assets = AssetRepository(session)
            for render in renders:
                if render.preview_asset_version_id is None:
                    continue
                for artifact in assets.list_version_artifacts(
                    render.preview_asset_version_id, limit=100
                ):
                    if artifact.retention_status == "active":
                        artifact.retention_status = "expired"
                        changed += 1
            session.flush()
        return changed

    @staticmethod
    def _resolve_exact_source(
        session: Session,
        *,
        project_id: UUID,
        workspace_id: UUID,
        owner_id: UUID,
        asset_version_id: UUID,
        frozen_duration_us: int,
    ):
        assets = AssetRepository(session)
        version = assets.get_asset_version(asset_version_id)
        asset = assets.get_asset(version.asset_id) if version is not None else None
        if (
            asset is None
            or asset.owner_id != owner_id
            or asset.workspace_id not in {None, workspace_id}
            or asset.lifecycle_status != "active"
            or asset.asset_type not in _AUDIO_ASSET_TYPES
            or not WorkspaceRepository(session).project_asset_exists(project_id, asset.asset_id)
        ):
            raise WorkingPreviewError(WorkingPreviewErrorCode.SOURCE_UNAVAILABLE)
        try:
            source = TrustedMediaMetadataService(assets).resolve_clip_source(asset_version_id)
        except TrustedMediaMetadataError as error:
            raise WorkingPreviewError(WorkingPreviewErrorCode.SOURCE_UNAVAILABLE) from error
        if source.duration_us != frozen_duration_us:
            raise WorkingPreviewError(WorkingPreviewErrorCode.SOURCE_UNAVAILABLE)
        return source


def _creation(job: Job, render: WorkingPreviewRender, *, replayed: bool) -> WorkingPreviewCreation:
    return WorkingPreviewCreation(
        job_id=job.job_id,
        preview_render_id=render.preview_render_id,
        working_composition_id=render.working_composition_id,
        rendered_revision=render.rendered_revision,
        status=job.status,
        replayed=replayed,
    )


def _fingerprint(project_id: UUID, working_composition_id: UUID, expected_revision: int) -> str:
    payload = json.dumps(
        {
            "expected_revision": expected_revision,
            "project_id": str(project_id),
            "working_composition_id": str(working_composition_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
