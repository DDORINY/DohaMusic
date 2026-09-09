from __future__ import annotations

import shutil
import time
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.audio_analysis import AudioAnalysisResult
from backend.core.job_status import JobStatus
from backend.models.pipeline_file import PipelineFile
from backend.models.pipeline_job import PipelineJob
from backend.pipeline.context import PipelineContext
from backend.pipeline.errors import StepTimeoutError, ValidationError
from backend.pipeline.executor import PipelineExecutor
from backend.repositories.pipeline_repository import PipelineRepository
from backend.schemas.pipeline import PipelineCreate


def wait_for_pipeline(client: TestClient, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/pipelines/{job_id}")
        assert response.status_code == 200
        job = response.json()
        analysis_status = (job.get("audio_analysis") or {}).get("analysis_status")
        if job["status"] in {"FAILED", "CANCELLED"} or (
            job["status"] == "COMPLETED" and analysis_status != "PENDING"
        ):
            return job
        time.sleep(0.01)
    raise AssertionError("Pipeline did not reach a terminal state")


def create_profile(client: TestClient) -> str:
    storage = client.app.state.storage
    reference = storage.voice_references_dir / "pipeline-consented.wav"
    shutil.copyfile(storage.sample_file, reference)
    response = client.post(
        "/api/voice-profiles",
        json={
            "name": "Pipeline 동의 음성",
            "reference_file_path": "voices/references/pipeline-consented.wav",
            "consent_confirmed": True,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_pipeline(client: TestClient, profile_id: str) -> dict[str, object]:
    response = client.post(
        "/api/pipelines",
        json={
            "prompt": "Mock pipeline integration",
            "lyrics": "테스트 가사",
            "duration_seconds": 10,
            "seed": 20260729,
            "voice_profile_id": profile_id,
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"
    return response.json()


def test_pipeline_success_progress_metadata_and_outputs(client: TestClient) -> None:
    job = create_pipeline(client, create_profile(client))
    completed = wait_for_pipeline(client, str(job["id"]))

    assert completed["status"] == "COMPLETED"
    assert completed["progress_percent"] == 100
    assert completed["current_step"] == "completed"
    # The repository mock fixture is intentionally 0.1 s, shorter than the
    # BS.1770 gating block, so WAV metrics succeed while LUFS is PARTIAL.
    assert completed["audio_analysis"]["analysis_status"] == "PARTIAL"
    assert completed["audio_analysis"]["quality"]["integrated_lufs"] is None
    assert completed["audio_analysis"]["tempo"]["status"] == "FAILED"
    assert completed["audio_analysis"]["tempo"]["detected_bpm"] is None
    assert completed["audio_analysis"]["hook"]["status"] == "PARTIAL"
    assert (
        completed["audio_analysis"]["hook"]["candidate"]["selection_strategy"] == "fallback_middle"
    )
    assert "source_file_role" not in completed["audio_analysis"]
    metadata = completed["result_metadata"]
    assert metadata["success"] is True
    assert [item["step"] for item in metadata["step_execution"]] == [
        "music",
        "stem",
        "voice",
        "mixer",
        "export",
    ]
    assert {key: value["provider"] for key, value in metadata["providers"].items()} == {
        "music": "mock",
        "stem": "mock",
        "voice": "mock",
        "mixer": "default",
        "export": "wav",
    }
    mixer_metrics = next(item for item in metadata["step_execution"] if item["step"] == "mixer")
    assert mixer_metrics["audio_quality"]["sample_rate"] == 48_000
    assert mixer_metrics["audio_quality"]["channels"] == 2
    assert mixer_metrics["audio_quality"]["clipping"]["detected"] is False
    assert "source_file_role" not in metadata["audio_analysis"]
    files = client.get(f"/api/pipelines/{job['id']}/files").json()
    assert {item["file_type"] for item in files} == {
        "music",
        "vocals",
        "instrumental",
        "converted_voice",
        "final",
        "metadata",
    }
    final = next(item for item in files if item["file_type"] == "final")
    assert all("file_path" not in item for item in files)
    with client.app.state.session_factory() as session:
        final_record = session.scalar(select(PipelineFile).where(PipelineFile.id == final["id"]))
        assert final_record is not None
        final_path = client.app.state.storage.resolve_relative_path(final_record.file_path)
    with wave.open(str(final_path), "rb") as audio:
        assert audio.getframerate() == 48_000
        assert audio.getnchannels() == 2


def test_pipeline_preserves_requested_bpm_only_as_tempo_comparison_metadata(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/pipelines",
        json={
            "prompt": "tempo comparison",
            "genre": "kpop_dance",
            "duration_seconds": 10,
            "voice_profile_id": create_profile(client),
            "generation_options": {
                "preset_id": "kpop_dance",
                "requested_bpm": 120,
            },
        },
    )
    assert response.status_code == 202

    completed = wait_for_pipeline(client, response.json()["id"])

    assert completed["status"] == "COMPLETED"
    assert completed["audio_analysis"]["tempo"]["requested_bpm"] == 120.0
    assert completed["result_metadata"]["generation_options"]["requested_bpm"] == 120
    assert "source_path" not in completed["audio_analysis"]["tempo"]
    assert "raw_onset_envelope" not in completed["audio_analysis"]["tempo"]


class FlakyMusicGenerator:
    model_name = "flaky-mock"

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.calls = 0

    def generate(self, request: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient test failure")
        return self.delegate.generate(request)


def test_pipeline_retries_retryable_provider_failure(client: TestClient) -> None:
    step = client.app.state.pipeline_worker.executor.steps[0]
    flaky = FlakyMusicGenerator(step.generator)
    step.generator = flaky

    job = create_pipeline(client, create_profile(client))
    completed = wait_for_pipeline(client, str(job["id"]))

    assert completed["status"] == "COMPLETED"
    attempts = [
        item for item in completed["result_metadata"]["step_execution"] if item["step"] == "music"
    ]
    assert [item["status"] for item in attempts] == ["FAILED", "COMPLETED"]
    assert flaky.calls == 2


class FailingAudioAnalyzer:
    def analyze(self, _file_path: object) -> AudioAnalysisResult:
        raise RuntimeError("private analyzer detail")


class FailingHookAnalyzer:
    def analyze(self, _file_path: object) -> object:
        raise RuntimeError("private hook analyzer detail")


def test_audio_analysis_failure_does_not_fail_pipeline_or_hide_final_file(
    client: TestClient,
) -> None:
    client.app.state.pipeline_worker.audio_quality_analyzer = FailingAudioAnalyzer()
    job = create_pipeline(client, create_profile(client))
    completed = wait_for_pipeline(client, str(job["id"]))

    assert completed["status"] == "COMPLETED"
    assert completed["audio_analysis"]["analysis_status"] == "FAILED"
    assert "private analyzer detail" not in str(completed["audio_analysis"])
    files = client.get(f"/api/pipelines/{job['id']}/files").json()
    assert any(item["file_type"] == "final" for item in files)


def test_hook_analysis_failure_is_partial_and_keeps_pipeline_completed(
    client: TestClient,
) -> None:
    client.app.state.pipeline_worker.hook_analyzer = FailingHookAnalyzer()
    job = create_pipeline(client, create_profile(client))
    completed = wait_for_pipeline(client, str(job["id"]))

    assert completed["status"] == "COMPLETED"
    assert completed["audio_analysis"]["analysis_status"] == "PARTIAL"
    assert completed["audio_analysis"]["hook"] == {
        "hook_analysis_version": "1.0",
        "status": "FAILED",
        "candidate": None,
    }
    assert "private hook analyzer detail" not in str(completed["audio_analysis"])
    files = client.get(f"/api/pipelines/{job['id']}/files").json()
    assert any(item["file_type"] == "final" for item in files)


def test_audio_analysis_metadata_failure_keeps_pipeline_completed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PipelineRepository.set_metadata
    calls = 0

    def fail_once(
        repository: PipelineRepository,
        current: PipelineJob,
        metadata: dict[str, object],
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary metadata failure")
        original(repository, current, metadata)

    monkeypatch.setattr(PipelineRepository, "set_metadata", fail_once)
    job = create_pipeline(client, create_profile(client))
    completed = wait_for_pipeline(client, str(job["id"]))

    assert completed["status"] == "COMPLETED"
    assert completed["audio_analysis"]["analysis_status"] == "FAILED"
    assert calls == 2


class CompletionBoundaryAnalyzer:
    def __init__(self, session_factory: object) -> None:
        self.session_factory = session_factory

    def analyze(self, file_path: object) -> AudioAnalysisResult:
        job_id = file_path.parent.name
        with self.session_factory() as session:
            job = session.get(PipelineJob, job_id)
            assert job is not None
            assert job.status != JobStatus.COMPLETED.value
        return AudioAnalysisResult.failed()


def test_analysis_finishes_before_irreversible_pipeline_completion(
    client: TestClient,
) -> None:
    profile_id = create_profile(client)
    client.app.state.pipeline_worker.audio_quality_analyzer = CompletionBoundaryAnalyzer(
        client.app.state.session_factory
    )
    response = client.post(
        "/api/pipelines",
        json={
            "prompt": "completion boundary",
            "duration_seconds": 10,
            "voice_profile_id": profile_id,
        },
    )
    job_id = response.json()["id"]

    completed = wait_for_pipeline(client, job_id)
    assert completed["status"] == "COMPLETED"
    final = next(
        item
        for item in client.get(f"/api/pipelines/{job_id}/files").json()
        if item["file_type"] == "final"
    )
    with client.app.state.session_factory() as session:
        stored = session.get(PipelineFile, final["id"])
        assert stored is not None
        final_path = client.app.state.storage.resolve_relative_path(stored.file_path)
    final_path.unlink()
    assert not final_path.exists()
    assert client.post(f"/api/pipelines/{job_id}/cancel").status_code == 409


class AlwaysFailingConverter:
    model_name = "failing-voice"

    def convert(self, _request: object) -> None:
        raise RuntimeError("expected voice failure")


def test_pipeline_failure_records_step_and_cleans_partial_audio(
    client: TestClient,
) -> None:
    client.app.state.pipeline_worker.executor.steps[2].converter = AlwaysFailingConverter()
    job = create_pipeline(client, create_profile(client))
    failed = wait_for_pipeline(client, str(job["id"]))

    assert failed["status"] == "FAILED"
    assert failed["failed_step"] == "voice"
    assert failed["error_code"] == "PIPELINE_PROVIDER_FAILED"
    files = client.get(f"/api/pipelines/{job['id']}/files").json()
    assert {item["file_type"] for item in files} == {"metadata"}
    assert failed["result_metadata"]["benchmark"]["failed_step"] == "voice"


class FailingStep:
    def __init__(self, name: str, status: JobStatus, progress_percent: int) -> None:
        self.name = name
        self.status = status
        self.progress_percent = progress_percent

    def execute(self, _context: PipelineContext) -> None:
        raise ValidationError(self.name, "expected step failure")


@pytest.mark.parametrize("index", [0, 1, 2])
def test_music_stem_and_voice_step_failures_are_attributed(client: TestClient, index: int) -> None:
    original = client.app.state.pipeline_worker.executor.steps[index]
    client.app.state.pipeline_worker.executor.steps[index] = FailingStep(
        original.name, original.status, original.progress_percent
    )
    job = create_pipeline(client, create_profile(client))
    failed = wait_for_pipeline(client, str(job["id"]))
    assert failed["status"] == "FAILED"
    assert failed["failed_step"] == original.name
    assert failed["error_code"] == "PIPELINE_VALIDATION_FAILED"


def test_pipeline_rejects_unknown_profile_and_invalid_input(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = client.post(
        "/api/pipelines",
        json={"prompt": "test", "voice_profile_id": missing},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert (
        client.post("/api/pipelines", json={"prompt": "", "voice_profile_id": "short"}).status_code
        == 422
    )
    assert client.get("/api/pipelines/missing").status_code == 404
    assert client.get("/api/pipelines/missing/files").status_code == 404


class SlowStep:
    name = "slow"
    status = JobStatus.GENERATING
    progress_percent = 20

    def execute(self, _context: PipelineContext) -> dict[str, object]:
        time.sleep(0.01)
        return {}


def test_pipeline_step_timeout_policy(tmp_path) -> None:
    context = PipelineContext(
        job_id="timeout-test",
        prompt="test",
        lyrics=None,
        genre=None,
        duration_seconds=1,
        seed=None,
        voice_profile_id="profile",
        reference_voice_path=tmp_path / "reference.wav",
        pipeline_version="1",
    )
    executor = PipelineExecutor([SlowStep()], max_retries=0, step_timeout_seconds=0.001)
    with pytest.raises(StepTimeoutError):
        executor.execute(context, lambda _step: None)


def create_stored_pipeline(client: TestClient, status: JobStatus) -> PipelineJob:
    profile_id = create_profile(client)
    request = PipelineCreate(
        prompt="취소 재시도 테스트",
        lyrics="[Verse]\n테스트 가사",
        genre="댄스 팝",
        duration_seconds=30,
        seed=77,
        voice_profile_id=profile_id,
    )
    with client.app.state.session_factory() as session:
        repository = PipelineRepository(session)
        job = repository.create(request, "test-v1")
        job.status = status.value
        job.current_step = "test"
        session.commit()
        session.refresh(job)
        session.expunge(job)
        return job


def test_cancel_pending_is_immediate_and_idempotent(client: TestClient) -> None:
    job = create_stored_pipeline(client, JobStatus.PENDING)
    response = client.post(f"/api/pipelines/{job.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["cancelled_at"] is not None
    repeated = client.post(f"/api/pipelines/{job.id}/cancel")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "CANCELLED"


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
def test_cancel_terminal_job_is_rejected(client: TestClient, status: JobStatus) -> None:
    job = create_stored_pipeline(client, status)
    response = client.post(f"/api/pipelines/{job.id}/cancel")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PIPELINE_CANCEL_NOT_ALLOWED"


def test_cancel_running_requests_cooperative_stop(client: TestClient) -> None:
    job = create_stored_pipeline(client, JobStatus.GENERATING)
    response = client.post(f"/api/pipelines/{job.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCEL_REQUESTED"
    assert response.json()["cancel_requested_at"] is not None


@pytest.mark.parametrize("source_status", [JobStatus.FAILED, JobStatus.CANCELLED])
def test_retry_creates_new_job_with_snapshot_and_relation(
    client: TestClient, source_status: JobStatus
) -> None:
    source = create_stored_pipeline(client, source_status)
    response = client.post(f"/api/pipelines/{source.id}/retry")
    assert response.status_code == 202
    payload = response.json()
    assert payload["source_job_id"] == source.id
    assert payload["job"]["id"] != source.id
    assert payload["job"]["retry_of_job_id"] == source.id
    assert payload["job"]["seed"] == 77
    assert payload["job"]["project_id"] == source.project_id
    assert payload["job"]["audio_analysis"] is None
    assert "audio_analysis" not in payload["job"]["result_metadata"]
    repeated = client.post(f"/api/pipelines/{source.id}/retry")
    assert repeated.status_code == 202
    assert repeated.json()["job"]["id"] == payload["job"]["id"]
    assert client.get(f"/api/pipelines/{source.id}").json()["status"] == source_status


def test_retry_analyzes_new_wav_instead_of_copying_source_tempo(
    client: TestClient,
) -> None:
    source = create_stored_pipeline(client, JobStatus.FAILED)
    with client.app.state.session_factory() as session:
        stored = session.get(PipelineJob, source.id)
        assert stored is not None
        stored.result_metadata = {
            "audio_analysis": {
                "audio_analysis_version": "1.0",
                "analysis_status": "PARTIAL",
                "quality": None,
                "tempo": {
                    "version": "1.0",
                    "status": "COMPLETED",
                    "requested_bpm": None,
                    "detected_bpm": 99.9,
                    "confidence": 0.99,
                    "bpm_error": None,
                    "absolute_bpm_error": None,
                    "half_time_candidate": False,
                    "double_time_candidate": False,
                    "warnings": [],
                },
                "hook": {
                    "hook_analysis_version": "1.0",
                    "status": "COMPLETED",
                    "candidate": {
                        "start_seconds": 40.0,
                        "end_seconds": 55.0,
                        "duration_seconds": 15.0,
                        "confidence": 0.95,
                        "selection_strategy": "energy_repetition",
                        "raw_frame_scores": [0.2, 0.95],
                    },
                    "warnings": [],
                },
                "warnings": [],
            }
        }
        session.commit()

    response = client.post(f"/api/pipelines/{source.id}/retry")
    assert response.status_code == 202
    retried = wait_for_pipeline(client, response.json()["job"]["id"])

    assert retried["status"] == "COMPLETED"
    assert retried["audio_analysis"]["tempo"]["detected_bpm"] is None
    assert retried["audio_analysis"]["tempo"]["confidence"] is None
    assert retried["audio_analysis"]["hook"]["candidate"]["start_seconds"] == 0
    assert retried["audio_analysis"]["hook"]["candidate"]["selection_strategy"] == "fallback_middle"
    assert (
        client.get(f"/api/pipelines/{source.id}").json()["audio_analysis"]["tempo"]["detected_bpm"]
        == 99.9
    )
    source_hook = client.get(f"/api/pipelines/{source.id}").json()["audio_analysis"]["hook"]
    assert source_hook["candidate"]["start_seconds"] == 40.0
    assert "raw_frame_scores" not in source_hook["candidate"]


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.GENERATING, JobStatus.COMPLETED])
def test_retry_rejects_non_terminal_source(client: TestClient, status: JobStatus) -> None:
    source = create_stored_pipeline(client, status)
    response = client.post(f"/api/pipelines/{source.id}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PIPELINE_RETRY_NOT_ALLOWED"


def test_cancel_retry_missing_job(client: TestClient) -> None:
    assert client.post("/api/pipelines/missing/cancel").status_code == 404
    assert client.post("/api/pipelines/missing/retry").status_code == 404


class CooperativeCancelStep:
    name = "cooperative_cancel"
    status = JobStatus.GENERATING
    progress_percent = 20

    def __init__(self, session_factory: object) -> None:
        self.session_factory = session_factory

    def execute(self, context: PipelineContext) -> dict[str, object]:
        with self.session_factory() as session:
            job = session.get(PipelineJob, context.job_id)
            assert job is not None
            job.status = JobStatus.CANCEL_REQUESTED.value
            session.commit()
        return {}


def test_worker_detects_cancel_after_step_and_publishes_no_result(
    client: TestClient,
) -> None:
    client.app.state.pipeline_worker.executor.steps = [
        CooperativeCancelStep(client.app.state.session_factory)
    ]
    job = create_pipeline(client, create_profile(client))
    cancelled = wait_for_pipeline(client, str(job["id"]))
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["can_retry"] is True
    assert client.get(f"/api/pipelines/{job['id']}/files").json() == []


def test_result_commit_loses_to_committed_cancel(client: TestClient) -> None:
    job = create_stored_pipeline(client, JobStatus.GENERATING)
    client.post(f"/api/pipelines/{job.id}/cancel")
    with client.app.state.session_factory() as session:
        repository = PipelineRepository(session)
        current = repository.get(job.id)
        assert current is not None
        assert (
            repository.finalize_success(
                current,
                {"success": True},
                [("final", "pipeline/final.wav", "audio/wav")],
            )
            is False
        )
        session.refresh(current)
        assert current.status == "CANCEL_REQUESTED"
        assert repository.list_files(job.id) == []
