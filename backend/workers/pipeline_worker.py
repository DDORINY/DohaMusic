"""Asynchronous orchestrator worker for the complete mock AI workflow."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.audio_analysis import (
    AudioAnalysisResult,
    AudioAnalysisStatus,
    AudioAnalysisWarning,
    AudioQualityAnalyzer,
    HookAnalysisResult,
    HookAnalyzer,
    TempoAnalysisResult,
    TempoAnalyzer,
)
from backend.core.job_status import JobStatus
from backend.core.logging import get_logger
from backend.kpop.options import public_generation_metadata
from backend.pipeline.context import PipelineContext
from backend.pipeline.errors import PipelineError
from backend.pipeline.executor import PipelineExecutor
from backend.pipeline.steps import PipelineStep
from backend.repositories.pipeline_repository import PipelineRepository
from backend.storage.service import StorageService

logger = get_logger(__name__)


class PipelineCancelled(Exception):
    """Internal cooperative-cancellation signal."""


class PipelineWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        executor: PipelineExecutor,
        storage: StorageService,
        audio_quality_analyzer: AudioQualityAnalyzer,
        tempo_analyzer: TempoAnalyzer,
        hook_analyzer: HookAnalyzer,
    ) -> None:
        self.session_factory = session_factory
        self.executor = executor
        self.storage = storage
        self.audio_quality_analyzer = audio_quality_analyzer
        self.tempo_analyzer = tempo_analyzer
        self.hook_analyzer = hook_analyzer

    def run(self, job_id: str) -> None:
        started_at = time.perf_counter()
        logger.info("pipeline_worker_started job_id=%s", job_id)
        with self.session_factory() as session:
            repository = PipelineRepository(session)
            job = repository.get(job_id)
            if job is None:
                logger.error("pipeline_job_not_found job_id=%s", job_id)
                return
            context: PipelineContext | None = None
            try:
                self._ensure_not_cancelled(repository, job)
                repository.transition(job, JobStatus.VALIDATING, "validating_inputs", 0)
                profile = repository.get_profile(job.voice_profile_id)
                if profile is None or not profile.consent_confirmed:
                    raise PermissionError("동의된 음성 프로필이 필요합니다.")
                reference_path = self.storage.resolve_voice_reference(profile.reference_file_path)
                if not reference_path.is_file():
                    raise FileNotFoundError("참조 음성 파일이 없습니다.")
                context = PipelineContext(
                    job_id=job.id,
                    prompt=job.prompt,
                    lyrics=job.lyrics,
                    genre=job.genre,
                    duration_seconds=job.duration_seconds,
                    seed=job.seed,
                    voice_profile_id=job.voice_profile_id,
                    reference_voice_path=reference_path,
                    pipeline_version=job.pipeline_version,
                )
                self.executor.execute(
                    context,
                    lambda step: self._start_step(repository, job, step),
                    lambda step: self._complete_step(repository, job, step),
                )
                self._ensure_not_cancelled(repository, job)
                metadata = self._metadata(context, started_at, success=True)
                metadata.update(self._kpop_metadata(job.input_snapshot))
                requested_bpm = self._requested_bpm(job.input_snapshot)
                metadata["audio_analysis"] = AudioAnalysisResult.pending(requested_bpm).model_dump(
                    mode="json"
                )
                metadata_path = self._write_metadata(job.id, metadata)
                context.metadata_file = metadata_path
                self._ensure_not_cancelled(repository, job)
                metadata = self._complete_audio_analysis(repository, job, context, metadata)
                self._ensure_not_cancelled(repository, job)
                if not repository.finalize_success(job, metadata, self._file_entries(context)):
                    raise PipelineCancelled
                logger.info(
                    "pipeline_worker_completed job_id=%s duration_ms=%s",
                    job_id,
                    round((time.perf_counter() - started_at) * 1_000, 2),
                )
            except PipelineCancelled:
                session.rollback()
                if context is not None:
                    self._cleanup_partial_outputs(context)
                repository.mark_cancelled(job)
                logger.info("pipeline_worker_cancelled job_id=%s", job_id)
            except PipelineError as exc:
                logger.error(
                    "pipeline_step_failed job_id=%s step=%s code=%s",
                    job_id,
                    exc.step,
                    exc.code,
                )
                session.rollback()
                self._fail(repository, job, context, started_at, exc)
            except Exception as exc:
                logger.exception("pipeline_worker_failed job_id=%s", job_id)
                session.rollback()
                wrapped = PipelineError("PIPELINE_FAILED", str(exc), step=job.current_step)
                self._fail(repository, job, context, started_at, wrapped)
            finally:
                logger.info(
                    "pipeline_worker_finished job_id=%s duration_ms=%s",
                    job_id,
                    round((time.perf_counter() - started_at) * 1_000, 2),
                )

    @staticmethod
    def _start_step(repository: PipelineRepository, job: Any, step: PipelineStep) -> None:
        PipelineWorker._ensure_not_cancelled(repository, job)
        repository.transition(job, step.status, f"{step.name}_started", step.progress_percent)
        logger.info("pipeline_step_started job_id=%s step=%s", job.id, step.name)

    @staticmethod
    def _complete_step(repository: PipelineRepository, job: Any, step: PipelineStep) -> None:
        PipelineWorker._ensure_not_cancelled(repository, job)
        logger.info("pipeline_step_boundary_checked job_id=%s step=%s", job.id, step.name)

    @staticmethod
    def _ensure_not_cancelled(repository: PipelineRepository, job: Any) -> None:
        repository.session.refresh(job)
        if job.status in {
            JobStatus.CANCEL_REQUESTED.value,
            JobStatus.CANCELLED.value,
        }:
            raise PipelineCancelled

    def _fail(
        self,
        repository: PipelineRepository,
        job: Any,
        context: PipelineContext | None,
        started_at: float,
        error: PipelineError,
    ) -> None:
        if context is not None:
            self._cleanup_partial_outputs(context)
            metadata = self._metadata(context, started_at, success=False)
        else:
            metadata = {
                "pipeline_version": job.pipeline_version,
                "success": False,
                "execution_time_seconds": round(time.perf_counter() - started_at, 6),
                "step_execution": [],
                "errors": [],
            }
        final_error = {"step": error.step, "code": error.code, "message": error.message}
        if not any(
            item.get("step") == error.step and item.get("code") == error.code
            for item in metadata["errors"]
        ):
            metadata["errors"].append(final_error)
        metadata.update(self._kpop_metadata(job.input_snapshot))
        metadata_path = self._write_metadata(job.id, metadata)
        repository.add_file(
            job.id,
            "metadata",
            self.storage.relative_path(metadata_path),
            "application/json",
        )
        repository.mark_failed(
            job,
            error.code,
            "Pipeline 작업이 실패했습니다.",
            error.step,
            metadata,
        )

    def _metadata(
        self, context: PipelineContext, started_at: float, success: bool
    ) -> dict[str, Any]:
        timings = [item["execution_time_seconds"] for item in context.step_execution]
        vram_values = [
            item["peak_vram_mb"]
            for item in context.step_execution
            if item.get("peak_vram_mb") is not None
        ]
        return {
            "pipeline_version": context.pipeline_version,
            "success": success,
            "seed": context.seed,
            "duration_seconds": context.duration_seconds,
            "execution_time_seconds": round(time.perf_counter() - started_at, 6),
            "providers": context.providers,
            "step_execution": context.step_execution,
            "benchmark": {
                "step_time_seconds": round(sum(timings), 6),
                "peak_vram_mb": max(vram_values, default=None),
                "cpu_percent": None,
                "gpu_percent": None,
                "success": success,
                "failed_step": None
                if success
                else (context.errors[-1]["step"] if context.errors else None),
            },
            "errors": list(context.errors),
        }

    def _write_metadata(self, job_id: str, metadata: dict[str, Any]) -> Path:
        path = self.storage.pipeline_dir / job_id / "metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _complete_audio_analysis(
        self,
        repository: PipelineRepository,
        job: Any,
        context: PipelineContext,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Finish best-effort analysis before publishing the success boundary."""

        requested_bpm = self._requested_bpm(job.input_snapshot)
        if context.output_file is None:
            analysis = AudioAnalysisResult.failed(requested_bpm)
        else:
            try:
                analysis = self.audio_quality_analyzer.analyze(context.output_file)
            except Exception:
                logger.exception("audio_analysis_failed job_id=%s", job.id)
                analysis = AudioAnalysisResult.failed(requested_bpm)
            try:
                tempo = self.tempo_analyzer.analyze(
                    context.output_file, requested_bpm=requested_bpm
                )
            except Exception:
                logger.exception("tempo_analysis_failed job_id=%s", job.id)
                tempo_warning = AudioAnalysisWarning(
                    code="TEMPO_DETECTION_FAILED",
                    message="템포를 안정적으로 추정하지 못했습니다.",
                )
                tempo = TempoAnalysisResult.failed(requested_bpm, tempo_warning)
            try:
                hook = self.hook_analyzer.analyze(context.output_file)
            except Exception:
                logger.exception("hook_analysis_failed job_id=%s", job.id)
                hook_warning = AudioAnalysisWarning(
                    code="HOOK_DETECTION_FAILED",
                    message="후렴 후보를 안정적으로 추정하지 못했습니다.",
                )
                hook = HookAnalysisResult.failed(hook_warning)
            analysis = analysis.model_copy(
                update={
                    "analysis_status": self._combined_analysis_status(
                        analysis.analysis_status, tempo.status, hook.status
                    ),
                    "tempo": tempo,
                    "hook": hook,
                    "warnings": [
                        *analysis.warnings,
                        *tempo.warnings,
                        *hook.warnings,
                    ],
                }
            )

        updated_metadata = dict(metadata)
        updated_metadata["audio_analysis"] = analysis.model_dump(mode="json")
        try:
            repository.set_metadata(job, updated_metadata)
        except Exception:
            repository.session.rollback()
            logger.exception("audio_analysis_metadata_save_failed job_id=%s", job.id)
            updated_metadata = dict(metadata)
            updated_metadata["audio_analysis"] = AudioAnalysisResult.failed(
                requested_bpm
            ).model_dump(mode="json")
            try:
                repository.set_metadata(job, updated_metadata)
            except Exception:
                repository.session.rollback()
                logger.exception("audio_analysis_failure_metadata_save_failed job_id=%s", job.id)
                return updated_metadata
        try:
            self._write_metadata(job.id, updated_metadata)
        except OSError:
            logger.exception("audio_analysis_file_metadata_save_failed job_id=%s", job.id)
        logger.info(
            "audio_analysis_completed job_id=%s status=%s version=%s",
            job.id,
            analysis.analysis_status.value,
            analysis.audio_analysis_version,
        )
        return updated_metadata

    @staticmethod
    def _kpop_metadata(snapshot: object) -> dict[str, object]:
        options, version = public_generation_metadata(snapshot)
        if options is None:
            return {}
        return {
            "generation_options": options,
            "kpop_prompt_compiler_version": version,
        }

    @staticmethod
    def _requested_bpm(snapshot: object) -> float | None:
        options, _ = public_generation_metadata(snapshot)
        if not isinstance(options, dict):
            return None
        value = options.get("requested_bpm")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    @staticmethod
    def _combined_analysis_status(
        quality_status: AudioAnalysisStatus, *component_statuses: AudioAnalysisStatus
    ) -> AudioAnalysisStatus:
        if quality_status in {
            AudioAnalysisStatus.FAILED,
            AudioAnalysisStatus.UNSUPPORTED,
        }:
            return quality_status
        if quality_status is AudioAnalysisStatus.COMPLETED and all(
            status is AudioAnalysisStatus.COMPLETED for status in component_statuses
        ):
            return AudioAnalysisStatus.COMPLETED
        return AudioAnalysisStatus.PARTIAL

    def _file_entries(self, context: PipelineContext) -> list[tuple[str, str, str]]:
        entries = (
            ("music", context.music_file, "audio/wav"),
            ("vocals", context.vocals_file, "audio/wav"),
            ("instrumental", context.instrumental_file, "audio/wav"),
            ("converted_voice", context.converted_voice, "audio/wav"),
            ("final", context.output_file, "audio/wav"),
            ("metadata", context.metadata_file, "application/json"),
        )
        return [
            (file_type, self.storage.relative_path(path), mime_type)
            for file_type, path, mime_type in entries
            if path is not None
        ]

    @staticmethod
    def _cleanup_partial_outputs(context: PipelineContext) -> None:
        for path in context.generated_paths():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("pipeline_partial_cleanup_failed path=%s", path)
