"""Claim-owned canonical WAV Export orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.audio.export_analyzer import (
    EXPORT_ANALYZER_NAME,
    EXPORT_ANALYZER_VERSION,
    CanonicalWavExportAnalyzer,
    ExportAnalysisError,
)
from backend.audio.working_preview_renderer import (
    PreviewRenderClip,
    PreviewRenderError,
    PreviewRenderTrack,
    WorkingCompositionPreviewRenderer,
)
from backend.models.workspace import ExportPublicationState, JobStatus
from backend.repositories.workspace import AssetRepository, CompositionRepository, JobRepository
from backend.services.workspace.artifact_application_service import ArtifactApplicationService
from backend.services.workspace.export_job_completion_service import (
    ExportJobCompletionRequest,
    ExportJobCompletionResult,
    ExportJobCompletionService,
)
from backend.services.workspace.export_publication_service import ExportPublicationService
from backend.services.workspace.trusted_media_metadata_service import TrustedMediaMetadataService
from backend.storage.artifact_integrity import calculate_artifact_integrity


class ExportWorkerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _FrozenExport:
    clips: tuple[PreviewRenderClip, ...]
    tracks: tuple[PreviewRenderTrack, ...]
    master_gain_db: object
    fingerprint: str


class ExportWorkerService:
    """Render or recover one already-owned Export Job claim."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        artifacts: ArtifactApplicationService,
        renderer: WorkingCompositionPreviewRenderer,
        publications: ExportPublicationService,
        completion: ExportJobCompletionService,
        analyzer: CanonicalWavExportAnalyzer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._artifacts = artifacts
        self._renderer = renderer
        self._publications = publications
        self._completion = completion
        self._analyzer = analyzer or CanonicalWavExportAnalyzer()

    def execute_owned_claim(
        self, *, job_id: UUID, claimed_by: str, claim_token: UUID
    ) -> ExportJobCompletionResult:
        replay = self._completion.replay_completed(job_id)
        if replay is not None:
            return replay
        try:
            return self._execute_owned_claim(
                job_id=job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
            )
        except Exception as error:
            disposition = self._claim_disposition(job_id, claimed_by, claim_token)
            if disposition == "stale":
                raise ExportWorkerError("EXPORT_STALE_CLAIM") from error
            cancelled = disposition == "cancelled"
            self._finish_terminal(
                job_id,
                claimed_by,
                claim_token,
                cancelled=cancelled,
                error_code=None if cancelled else self._safe_error_code(error),
            )
            raise

    def _execute_owned_claim(
        self, *, job_id: UUID, claimed_by: str, claim_token: UUID
    ) -> ExportJobCompletionResult:
        frozen, owner_id, state = self._load(job_id, claimed_by, claim_token)
        if state is ExportPublicationState.COMPLETED:
            raise ExportWorkerError("EXPORT_ALREADY_COMPLETED")
        if state is ExportPublicationState.PUBLISHED:
            recovered = self._publications.publish_or_recover(
                job_id=job_id, claimed_by=claimed_by, claim_token=claim_token
            )
            if recovered.storage_outcome is None:
                raise ExportWorkerError("EXPORT_PUBLICATION_MISSING")
            with self._publications.open_trusted_publication(
                job_id=job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
            ) as (payload, _stream):
                analysis = self._analyzer.analyze(payload.path)
        else:
            with self._render(frozen, owner_id, job_id, claimed_by, claim_token) as output:
                analysis = self._analyzer.analyze(output.path)
                with output.path.open("rb") as stream:
                    integrity = calculate_artifact_integrity(stream)
                self._publications.set_expected_integrity(
                    job_id=job_id,
                    claimed_by=claimed_by,
                    claim_token=claim_token,
                    sha256=integrity.checksum,
                    size_bytes=integrity.size_bytes,
                )
                self._publications.publish_or_recover(
                    job_id=job_id,
                    claimed_by=claimed_by,
                    claim_token=claim_token,
                    staged_payload=output.path,
                )
        return self._completion.complete(
            ExportJobCompletionRequest(
                job_id=job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
                render_fingerprint=frozen.fingerprint,
                quality=analysis.quality,
                analyzer_name=EXPORT_ANALYZER_NAME,
                analyzer_version=EXPORT_ANALYZER_VERSION,
            )
        )

    def _load(self, job_id: UUID, claimed_by: str, claim_token: UUID):
        with self._session_factory() as session:
            jobs = JobRepository(session)
            job = jobs.get_job(job_id)
            if (
                job is None
                or job.job_type != "export"
                or job.composition_snapshot_id is None
                or job.status is not JobStatus.RUNNING
                or job.claimed_by != claimed_by
                or job.claim_token != claim_token
                or job.cancel_requested_at is not None
            ):
                raise ExportWorkerError("EXPORT_STALE_CLAIM")
            repository = CompositionRepository(session)
            snapshot = repository.get_project_snapshot(job.project_id, job.composition_snapshot_id)
            if snapshot is None:
                raise ExportWorkerError("EXPORT_SNAPSHOT_INVALID")
            tracks = repository.list_snapshot_tracks(snapshot.composition_snapshot_id)
            clips = repository.list_snapshot_clips_for_snapshot(snapshot.composition_snapshot_id)
            if not tracks or not clips:
                raise ExportWorkerError("EXPORT_EMPTY")
            track_orders = {track.snapshot_track_id: track.track_order for track in tracks}
            media = TrustedMediaMetadataService(AssetRepository(session))
            render_clips = []
            for order, clip in enumerate(clips):
                source = media.resolve_clip_source(clip.source_asset_version_id)
                if source.duration_us != clip.source_duration:
                    raise ExportWorkerError("EXPORT_SOURCE_INVALID")
                render_clips.append(
                    PreviewRenderClip(
                        clip.snapshot_clip_id,
                        track_orders[clip.snapshot_track_id],
                        order,
                        source.artifact_id,
                        clip.source_in,
                        clip.source_out,
                        clip.timeline_start,
                        clip.timeline_duration,
                        5,
                        clip.loop_enabled,
                        clip.loop_phase,
                        clip.gain_db,
                        clip.fade_in,
                        clip.fade_out,
                    )
                )
            render_tracks = tuple(
                PreviewRenderTrack(t.track_order, t.gain_db, t.pan, t.muted, t.solo) for t in tracks
            )
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "snapshot_id": str(snapshot.composition_snapshot_id),
                        "format": "wav",
                        "sample_rate": 48_000,
                        "channels": 2,
                        "codec": "pcm_s16le",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            publication = self._publications.ensure_intent(
                job_id=job_id,
                composition_snapshot_id=snapshot.composition_snapshot_id,
            )
            return (
                _FrozenExport(
                    tuple(render_clips),
                    render_tracks,
                    snapshot.master_gain_db,
                    fingerprint,
                ),
                job.requested_by,
                publication.state,
            )

    @contextmanager
    def _render(self, frozen, owner_id, job_id, claimed_by, claim_token) -> Iterator:
        @contextmanager
        def open_artifact(artifact_id: UUID) -> Iterator:
            with self._artifacts.open_content_for_owner(
                artifact_id, effective_owner_id=owner_id
            ) as (handle, stream):
                yield handle.size_bytes, stream

        with self._renderer.render(
            frozen.clips,
            track_count=len(frozen.tracks),
            tracks=frozen.tracks,
            master_gain_db=frozen.master_gain_db,
            cancel_requested=lambda: self._cancelled(job_id, claimed_by, claim_token),
            open_artifact=open_artifact,
        ) as output:
            yield output

    def _cancelled(self, job_id, claimed_by, claim_token) -> bool:
        with self._session_factory() as session:
            job = JobRepository(session).get_job(job_id)
            return bool(
                job is None
                or job.status is not JobStatus.RUNNING
                or job.claimed_by != claimed_by
                or job.claim_token != claim_token
                or job.cancel_requested_at is not None
            )

    def _claim_disposition(self, job_id, claimed_by, claim_token) -> str:
        with self._session_factory() as session:
            job = JobRepository(session).get_job(job_id)
            if (
                job is None
                or job.status is not JobStatus.RUNNING
                or job.claimed_by != claimed_by
                or job.claim_token != claim_token
            ):
                return "stale"
            return "cancelled" if job.cancel_requested_at is not None else "owned"

    def _finish_terminal(
        self,
        job_id,
        claimed_by,
        claim_token,
        *,
        cancelled: bool,
        error_code: str | None,
    ) -> None:
        with self._session_factory() as session, session.begin():
            finished = JobRepository(session).finish_owned_claim(
                job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
                status=JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                now=datetime.now(UTC),
                error_code=error_code,
                error_message=None if cancelled else "Canonical WAV Export failed.",
                error_retryable=None if cancelled else True,
            )
            if finished is None:
                raise ExportWorkerError("EXPORT_STALE_CLAIM")

    @staticmethod
    def _safe_error_code(error: Exception) -> str:
        if isinstance(error, ExportAnalysisError):
            return "EXPORT_QUALITY_ANALYSIS_FAILED"
        if isinstance(error, PreviewRenderError):
            return "EXPORT_RENDER_FAILED"
        if isinstance(error, ExportWorkerError):
            code = str(error)
            if code in {
                "EXPORT_EMPTY",
                "EXPORT_INTEGRITY_FAILED",
                "EXPORT_PUBLICATION_MISSING",
                "EXPORT_QUALITY_GATE_FAILED",
                "EXPORT_SNAPSHOT_INVALID",
                "EXPORT_SOURCE_INVALID",
            }:
                return code
        return "EXPORT_PUBLICATION_FAILED"
