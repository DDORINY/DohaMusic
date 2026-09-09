"""Claim-owned Working Preview manifest execution boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.audio.working_preview_renderer import (
    PreviewRenderClip,
    PreviewRenderError,
    PreviewRenderTrack,
    WorkingCompositionPreviewRenderer,
)
from backend.models.workspace import JobStatus
from backend.repositories.workspace import JobRepository, WorkingPreviewRepository
from backend.services.workspace.artifact_application_service import (
    ArtifactAccessError,
    ArtifactApplicationService,
)
from backend.services.workspace.working_preview_service import (
    WorkingPreviewCompletion,
    WorkingPreviewError,
    WorkingPreviewErrorCode,
    WorkingPreviewService,
)


class WorkingPreviewWorkerService:
    """Render only a caller-owned running claim; never re-read WorkingComposition."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        artifacts: ArtifactApplicationService,
        previews: WorkingPreviewService,
        renderer: WorkingCompositionPreviewRenderer,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._artifacts = artifacts
        self._previews = previews
        self._renderer = renderer
        self._clock = clock

    def execute_owned_claim(
        self, *, job_id: UUID, claimed_by: str, claim_token: UUID
    ) -> WorkingPreviewCompletion:
        with self._session_factory() as session:
            jobs = JobRepository(session)
            job = jobs.get_job(job_id)
            render = WorkingPreviewRepository(session).get_render_for_job(job_id)
            if (
                job is None
                or render is None
                or job.status is not JobStatus.RUNNING
                or job.claimed_by != claimed_by
                or job.claim_token != claim_token
                or job.cancel_requested_at is not None
            ):
                raise WorkingPreviewError(WorkingPreviewErrorCode.JOB_STATE_CONFLICT)
            owner_id = job.requested_by
            tracks = WorkingPreviewRepository(session).list_tracks(render.preview_render_id)
            clips = WorkingPreviewRepository(session).list_clips(render.preview_render_id)
            track_orders = {item.track_id: item.track_order for item in tracks}
            manifest_schema = job.settings_snapshot.get("manifest_schema")
            render_clips = tuple(
                PreviewRenderClip(
                    clip_id=item.clip_id,
                    track_order=track_orders[item.track_id],
                    canonical_order=item.canonical_order,
                    artifact_id=item.source_artifact_id,
                    source_in_us=item.source_in_us,
                    source_out_us=item.source_out_us,
                    timeline_start_us=item.timeline_start_us,
                    timeline_duration_us=item.timeline_duration_us,
                    loop_enabled=item.loop_enabled,
                    loop_phase_us=item.loop_phase_us,
                    manifest_schema=manifest_schema,
                    gain_db=item.gain_db,
                    fade_in_us=item.fade_in_us,
                    fade_out_us=item.fade_out_us,
                )
                for item in clips
            )
            render_tracks = tuple(
                PreviewRenderTrack(
                    track_order=item.track_order,
                    gain_db=item.gain_db,
                    pan=item.pan,
                    muted=item.muted,
                    solo=item.solo,
                )
                for item in tracks
            )

        @contextmanager
        def open_artifact(artifact_id: UUID) -> Iterator:
            with self._artifacts.open_content_for_owner(
                artifact_id, effective_owner_id=owner_id
            ) as (handle, stream):
                yield handle.size_bytes, stream

        try:
            with self._renderer.render(
                render_clips,
                track_count=len(tracks),
                tracks=render_tracks,
                master_gain_db=render.master_gain_db,
                cancel_requested=lambda: self._cancel_requested(job_id, claimed_by, claim_token),
                open_artifact=open_artifact,
            ) as output:
                return self._previews.complete_render(
                    job_id=job_id,
                    claimed_by=claimed_by,
                    claim_token=claim_token,
                    output_path=output.path,
                )
        except PreviewRenderError as error:
            cancelled = str(error) == "WORKING_PREVIEW_CANCELLED" or self._cancel_requested(
                job_id, claimed_by, claim_token
            )
            self._finish_failure(
                job_id,
                claimed_by,
                claim_token,
                cancelled=cancelled,
                error_code=("WORKING_PREVIEW_CANCELLED" if cancelled else str(error)),
            )
            raise
        except ArtifactAccessError:
            self._finish_failure(
                job_id,
                claimed_by,
                claim_token,
                cancelled=False,
                error_code="WORKING_PREVIEW_SOURCE_UNAVAILABLE",
            )
            raise

    def _cancel_requested(self, job_id: UUID, claimed_by: str, claim_token: UUID) -> bool:
        with self._session_factory() as session:
            job = JobRepository(session).get_job(job_id)
            return bool(
                job
                and job.status is JobStatus.RUNNING
                and job.claimed_by == claimed_by
                and job.claim_token == claim_token
                and job.cancel_requested_at is not None
            )

    def _finish_failure(
        self,
        job_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        *,
        cancelled: bool,
        error_code: str,
    ) -> None:
        with self._session_factory() as session, session.begin():
            JobRepository(session).finish_owned_claim(
                job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
                status=JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                now=self._clock(),
                error_code=None if cancelled else error_code[:64],
                error_message=None if cancelled else "Working Preview render failed.",
                error_retryable=None if cancelled else True,
            )
