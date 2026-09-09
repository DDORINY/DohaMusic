"""FastAPI application composition root."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request, Response

from backend.ai.factory import create_music_generator
from backend.ai.stem_factory import create_stem_separator
from backend.ai.voice_factory import create_voice_converter
from backend.api.exception_handlers import register_exception_handlers
from backend.api.router import api_router
from backend.api.v1.dependencies import get_request_id, register_request_id_middleware
from backend.audio.factory import create_audio_mixer
from backend.audio.working_preview_renderer import FfmpegWorkingCompositionPreviewRenderer
from backend.audio_analysis import (
    DefaultAudioQualityAnalyzer,
    DefaultHookAnalyzer,
    DefaultTempoAnalyzer,
)
from backend.core.config import Settings, get_settings
from backend.core.cursor_pagination import CursorCodec
from backend.core.logging import configure_logging, get_logger
from backend.db.migrations import upgrade_database
from backend.db.session import create_session_factory
from backend.lyrics.factory import create_lyrics_generator
from backend.pipeline.audio import WavExporter
from backend.pipeline.executor import PipelineExecutor
from backend.pipeline.steps import (
    ExportStep,
    GenerateMusicStep,
    MixStep,
    StemSeparationStep,
    VoiceConversionStep,
)
from backend.repositories.workspace import SqlAlchemyPayloadLocatorPersistence
from backend.services.generation_service import GenerationService
from backend.services.history_service import HistoryService
from backend.services.lyrics_service import LyricsService
from backend.services.pipeline_service import PipelineService
from backend.services.stem_service import StemService
from backend.services.voice_conversion_service import VoiceConversionService
from backend.services.voice_enrollment_service import VoiceEnrollmentService
from backend.services.voice_profile_service import VoiceProfileService
from backend.services.voice_upload_service import VoiceUploadService
from backend.services.workspace import (
    ArtifactApplicationService,
    ArtifactIngestionService,
    AssetService,
    CompositionService,
    ExportJobCompletionService,
    ExportPublicationService,
    ExportWorkerRunner,
    ExportWorkerService,
    JobService,
    PayloadLocatorService,
    PayloadStagingService,
    TrustedArtifactRegistrationService,
    WorkingCompositionService,
    WorkingPreviewService,
    WorkspaceService,
)
from backend.storage import ArtifactStorageRoots, LocalFilesystemStagingAdapter
from backend.storage.artifact_publisher import LocalArtifactPublisher
from backend.storage.service import StorageService
from backend.voice_enrollment.maintenance import (
    VoiceEnrollmentMaintenanceService,
    VoiceMaintenanceMetrics,
)
from backend.voice_enrollment.scheduler import VoiceEnrollmentScheduler
from backend.workers.dispatcher import ThreadPoolJobDispatcher
from backend.workers.generation_worker import GenerationWorker
from backend.workers.pipeline_worker import PipelineWorker
from backend.workers.stem_worker import StemWorker
from backend.workers.voice_conversion_worker import VoiceConversionWorker

logger = get_logger(__name__)


@contextmanager
def _export_renderer_artifact_placeholder(_artifact_id: UUID) -> Iterator[tuple[int, object]]:
    raise RuntimeError("Export renderer media access must be supplied by ExportWorkerService")
    yield 0, object()


def run_startup_migration(settings: Settings) -> None:
    """Run the legacy startup migration only after explicit opt-in."""

    if not settings.auto_migrate:
        logger.warning(
            "automatic_database_migration_disabled "
            "manual_preflight_required=true schema_changed=false"
        )
        return
    logger.warning("automatic_database_migration_enabled target_revision=head")
    upgrade_database(settings.database_url)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application with replaceable infrastructure dependencies."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = StorageService(resolved_settings.storage_root)
        storage.ensure_layout()
        run_startup_migration(resolved_settings)

        session_factory = create_session_factory(resolved_settings.database_url)
        cursor_signing_key = resolved_settings.cursor_signing_key.get_secret_value()
        cursor_codec = CursorCodec(cursor_signing_key) if cursor_signing_key else None
        music_generator = create_music_generator(resolved_settings, storage)
        stem_separator = create_stem_separator(resolved_settings, storage)
        voice_converter = create_voice_converter(resolved_settings, storage)
        audio_mixer = create_audio_mixer(resolved_settings, storage.pipeline_dir)
        lyrics_generator = create_lyrics_generator(resolved_settings)
        logger.info(
            "provider_configured provider=%s model=%s",
            resolved_settings.music_generator,
            music_generator.model_name,
        )
        logger.info(
            "stem_provider_configured provider=%s model=%s",
            resolved_settings.stem_provider,
            stem_separator.model_name,
        )
        logger.info(
            "voice_provider_configured provider=%s model=%s",
            resolved_settings.voice_provider,
            voice_converter.model_name,
        )
        logger.info("audio_mixer_configured provider=%s", resolved_settings.audio_mixer)
        logger.info(
            "lyrics_provider_configured provider=%s model=%s",
            lyrics_generator.provider,
            lyrics_generator.model_name,
        )
        worker = GenerationWorker(
            session_factory=session_factory,
            music_generator=music_generator,
            storage=storage,
        )
        shared_executor = ThreadPoolExecutor(
            max_workers=resolved_settings.worker_max_threads,
            thread_name_prefix="dohamusic-ai-worker",
        )
        dispatcher = ThreadPoolJobDispatcher(
            worker=worker,
            executor=shared_executor,
        )
        stem_worker = StemWorker(
            session_factory=session_factory,
            stem_separator=stem_separator,
            storage=storage,
        )
        stem_dispatcher = ThreadPoolJobDispatcher(
            worker=stem_worker,
            executor=shared_executor,
        )
        voice_worker = VoiceConversionWorker(
            session_factory=session_factory,
            voice_converter=voice_converter,
            storage=storage,
        )
        voice_dispatcher = ThreadPoolJobDispatcher(
            worker=voice_worker,
            executor=shared_executor,
        )
        pipeline_executor = PipelineExecutor(
            steps=[
                GenerateMusicStep(music_generator),
                StemSeparationStep(stem_separator),
                VoiceConversionStep(voice_converter),
                MixStep(audio_mixer),
                ExportStep(WavExporter(storage.pipeline_dir)),
            ],
            max_retries=resolved_settings.pipeline_max_retries,
            step_timeout_seconds=resolved_settings.pipeline_step_timeout_seconds,
        )
        pipeline_worker = PipelineWorker(
            session_factory=session_factory,
            executor=pipeline_executor,
            storage=storage,
            audio_quality_analyzer=DefaultAudioQualityAnalyzer(),
            tempo_analyzer=DefaultTempoAnalyzer(),
            hook_analyzer=DefaultHookAnalyzer(),
        )
        pipeline_dispatcher = ThreadPoolJobDispatcher(
            worker=pipeline_worker,
            executor=shared_executor,
        )

        app.state.settings = resolved_settings
        app.state.session_factory = session_factory
        app.state.workspace_service = WorkspaceService(
            session_factory,
            cursor_codec=cursor_codec,
        )
        app.state.asset_service = AssetService(
            session_factory,
            cursor_codec=cursor_codec,
        )
        app.state.composition_service = CompositionService(
            session_factory,
            cursor_codec=cursor_codec,
        )
        app.state.working_composition_service = WorkingCompositionService(session_factory)
        app.state.job_service = JobService(
            session_factory,
            cursor_codec=cursor_codec,
        )
        payload_locator_service = PayloadLocatorService(
            SqlAlchemyPayloadLocatorPersistence(session_factory)
        )
        app.state.payload_locator_service = payload_locator_service
        artifact_roots = (
            ArtifactStorageRoots.from_base_root(resolved_settings.artifact_root)
            if resolved_settings.artifact_root is not None
            else None
        )
        app.state.artifact_application_service = ArtifactApplicationService(
            session_factory,
            artifact_roots=artifact_roots,
        )
        preview_ingestion_service = (
            ArtifactIngestionService(
                session_factory,
                artifact_roots=artifact_roots,
                staging_root=resolved_settings.artifact_staging_root,
            )
            if artifact_roots is not None
            else None
        )
        app.state.working_preview_service = WorkingPreviewService(
            session_factory,
            ingestion_service=preview_ingestion_service,
        )
        export_runner = None
        if artifact_roots is not None and resolved_settings.artifact_staging_root is not None:
            staging_root = Path(resolved_settings.artifact_staging_root)
            publisher = LocalArtifactPublisher(artifact_roots, staging_root)
            export_ingestion = ArtifactIngestionService(
                session_factory,
                artifact_roots=artifact_roots,
                staging_root=staging_root,
            )
            export_publications = ExportPublicationService(
                session_factory,
                publisher=publisher,
            )
            export_completion = ExportJobCompletionService(
                session_factory,
                trusted_registration=TrustedArtifactRegistrationService(
                    session_factory,
                    publisher=publisher,
                    ingestion_service=export_ingestion,
                ),
            )
            export_worker = ExportWorkerService(
                session_factory,
                artifacts=app.state.artifact_application_service,
                renderer=FfmpegWorkingCompositionPreviewRenderer(
                    ffmpeg_executable=resolved_settings.voice_ffmpeg_executable,
                    temp_root=staging_root / "canonical-export-render",
                    open_artifact=_export_renderer_artifact_placeholder,
                ),
                publications=export_publications,
                completion=export_completion,
            )
            export_runner = ExportWorkerRunner(
                session_factory,
                worker=export_worker,
                poll_interval_seconds=resolved_settings.export_worker_poll_interval_seconds,
            )
        app.state.export_worker_runner = export_runner
        payload_staging_adapter = (
            LocalFilesystemStagingAdapter(
                resolved_settings.artifact_staging_root,
                artifact_roots=artifact_roots,
            )
            if resolved_settings.artifact_staging_root is not None
            else None
        )
        app.state.verified_payload_staging = payload_staging_adapter
        app.state.payload_staging_service = (
            PayloadStagingService(payload_locator_service, payload_staging_adapter)
            if payload_staging_adapter is not None
            else None
        )
        app.state.storage = storage
        app.state.worker = worker
        app.state.dispatcher = dispatcher
        app.state.stem_worker = stem_worker
        app.state.stem_dispatcher = stem_dispatcher
        app.state.voice_worker = voice_worker
        app.state.voice_dispatcher = voice_dispatcher
        app.state.pipeline_worker = pipeline_worker
        app.state.pipeline_dispatcher = pipeline_dispatcher
        app.state.generation_service = GenerationService(
            session_factory=session_factory,
            dispatcher=dispatcher,
        )
        app.state.history_service = HistoryService(session_factory=session_factory)
        app.state.lyrics_service = LyricsService(
            session_factory=session_factory,
            generator=lyrics_generator,
        )
        app.state.voice_profile_service = VoiceProfileService(
            session_factory=session_factory,
            storage=storage,
        )
        app.state.voice_enrollment_service = VoiceEnrollmentService(
            session_factory=session_factory,
            storage=storage,
            settings=resolved_settings,
        )
        voice_maintenance_metrics = VoiceMaintenanceMetrics()
        voice_maintenance_service = VoiceEnrollmentMaintenanceService(
            session_factory=session_factory,
            storage=storage,
            settings=resolved_settings,
            metrics=voice_maintenance_metrics,
        )
        voice_maintenance_scheduler = VoiceEnrollmentScheduler(
            maintenance=voice_maintenance_service,
            expiration_interval_seconds=(resolved_settings.voice_expiration_scan_interval_seconds),
            cleanup_interval_seconds=resolved_settings.voice_cleanup_interval_seconds,
            orphan_interval_seconds=resolved_settings.voice_orphan_scan_interval_seconds,
        )
        app.state.voice_maintenance_metrics = voice_maintenance_metrics
        app.state.voice_enrollment_maintenance_service = voice_maintenance_service
        app.state.voice_enrollment_scheduler = voice_maintenance_scheduler
        app.state.voice_upload_service = VoiceUploadService(
            session_factory=session_factory,
            storage=storage,
        )
        app.state.stem_service = StemService(
            session_factory=session_factory,
            dispatcher=stem_dispatcher,
        )
        app.state.voice_conversion_service = VoiceConversionService(
            session_factory=session_factory,
            dispatcher=voice_dispatcher,
        )
        app.state.pipeline_service = PipelineService(
            session_factory=session_factory,
            dispatcher=pipeline_dispatcher,
            pipeline_version=resolved_settings.pipeline_version,
            storage=storage,
        )
        await voice_maintenance_scheduler.start()
        if export_runner is not None:
            await export_runner.start()
        logger.info("application_started")
        try:
            yield
        finally:
            if export_runner is not None:
                await export_runner.stop()
            await voice_maintenance_scheduler.stop()
            shared_executor.shutdown(wait=True, cancel_futures=False)
            session_factory.kw["bind"].dispose()
            logger.info("application_stopped")

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router)

    @app.middleware("http")
    async def log_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()
        request_id = get_request_id(request)
        logger.info(
            "request_started method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        elapsed_ms = round((time.perf_counter() - started_at) * 1_000, 2)
        logger.info(
            "request_finished method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    register_request_id_middleware(app)

    return app
