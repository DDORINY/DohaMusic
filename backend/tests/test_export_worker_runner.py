"""Export-only production runner claim and lifecycle contracts."""

from __future__ import annotations

import asyncio
import io
import shutil
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.factory import create_app
from backend.core.config import Settings
from backend.models.workspace import (
    Artifact,
    AssetType,
    AssetVersion,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    JobStatus,
    ProjectAsset,
)
from backend.repositories.workspace import JobRepository
from backend.services.workspace import (
    ArtifactIngestionRequest,
    ArtifactIngestionService,
    AssetService,
    CompositionService,
    SnapshotItemInput,
    WorkspaceService,
)
from backend.services.workspace.export_worker_runner import ExportWorkerRunner
from backend.storage.artifact_resolver import APPROVED_STORAGE_DOMAINS, ArtifactStorageRoots
from backend.tests.test_export_publication_ledger import LedgerFixture
from backend.tests.test_export_worker_happy_path import _source_wav


class RecordingWorker:
    def __init__(self, factory, *, failures_remaining: int = 0) -> None:
        self.factory = factory
        self.failures_remaining = failures_remaining
        self.calls = []
        self.lock = Lock()

    def execute_owned_claim(self, *, job_id, claimed_by, claim_token):
        with self.lock:
            self.calls.append((job_id, claimed_by, claim_token))
            fail = self.failures_remaining > 0
            self.failures_remaining = max(0, self.failures_remaining - 1)
        with self.factory() as session, session.begin():
            status = JobStatus.FAILED if fail else JobStatus.SUCCEEDED
            finished = JobRepository(session).finish_owned_claim(
                job_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
                status=status,
                now=datetime.now(UTC),
                error_code="EXPORT_TEST_FAILURE" if fail else None,
                error_message="Export failed." if fail else None,
                error_retryable=fail or None,
            )
            assert finished is not None
        if fail:
            raise RuntimeError("expected worker failure")
        return object()


def _queued(fixture: LedgerFixture) -> None:
    with fixture.factory() as session, session.begin():
        job = session.get(Job, fixture.job.job_id)
        job.status = JobStatus.QUEUED
        job.claim_token = None
        job.claimed_by = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.started_at = None


def _runner(fixture, worker, *, runner_id="export-runner", clock=None):
    return ExportWorkerRunner(
        fixture.factory,
        worker=worker,
        poll_interval_seconds=0.01,
        runner_id=runner_id,
        clock=clock or (lambda: datetime.now(UTC)),
    )


def test_runner_claims_only_export_and_passes_owned_token(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    _queued(fixture)
    with fixture.factory() as session, session.begin():
        session.add(
            Job(
                project_id=fixture.project.project_id,
                workspace_id=fixture.job.workspace_id,
                job_type="music",
                status=JobStatus.QUEUED,
                api_contract_version="1",
                settings_snapshot={},
                requested_by=fixture.owner,
            )
        )
    worker = RecordingWorker(fixture.factory)

    assert _runner(fixture, worker).run_once() is True
    assert len(worker.calls) == 1
    assert worker.calls[0][0] == fixture.job.job_id
    with fixture.factory() as session:
        assert session.get(Job, fixture.job.job_id).status is JobStatus.SUCCEEDED
        provider_job = session.query(Job).filter(Job.job_type == "music").one()
        assert provider_job.status is JobStatus.QUEUED
    fixture.engine.dispose()


def test_runner_reports_no_eligible_export(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    worker = RecordingWorker(fixture.factory)

    assert _runner(fixture, worker).run_once() is False
    assert worker.calls == []
    fixture.engine.dispose()


def test_expired_export_claim_is_requeued_and_reclaimed(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    now = datetime.now(UTC)
    with fixture.factory() as session, session.begin():
        job = session.get(Job, fixture.job.job_id)
        job.heartbeat_at = now - timedelta(minutes=10)
        job.lease_expires_at = now - timedelta(minutes=5)
    worker = RecordingWorker(fixture.factory)

    assert _runner(fixture, worker, clock=lambda: now).run_once() is True
    assert len(worker.calls) == 1
    assert worker.calls[0][2] != fixture.claim_token
    fixture.engine.dispose()


def test_two_runners_claim_one_export_once(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    _queued(fixture)
    worker = RecordingWorker(fixture.factory)
    runners = [_runner(fixture, worker, runner_id=f"runner-{index}") for index in range(2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda runner: runner.run_once(), runners))

    assert sorted(results) == [False, True]
    assert len(worker.calls) == 1
    fixture.engine.dispose()


def test_worker_failure_is_terminal_and_next_iteration_survives(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    _queued(fixture)
    with fixture.factory() as session, session.begin():
        next_job = Job(
            project_id=fixture.project.project_id,
            workspace_id=fixture.job.workspace_id,
            composition_snapshot_id=fixture.snapshot.composition_snapshot_id,
            job_type="export",
            status=JobStatus.QUEUED,
            api_contract_version="1",
            settings_snapshot={"format": "wav"},
            requested_by=fixture.owner,
        )
        session.add(next_job)
        session.flush()
        next_job_id = next_job.job_id
    failing = RecordingWorker(fixture.factory, failures_remaining=1)
    runner = _runner(fixture, failing)

    with pytest.raises(RuntimeError, match="expected worker failure"):
        runner.run_once()
    assert runner.run_once() is True
    assert runner.run_once() is False
    with fixture.factory() as session:
        assert session.get(Job, fixture.job.job_id).status is JobStatus.FAILED
        assert session.get(Job, next_job_id).status is JobStatus.SUCCEEDED
    fixture.engine.dispose()


def test_runner_start_stop_is_idempotent(tmp_path) -> None:
    fixture = LedgerFixture(tmp_path)
    runner = _runner(fixture, RecordingWorker(fixture.factory))

    async def lifecycle() -> None:
        await runner.start()
        await runner.start()
        assert runner.is_running
        await runner.stop()
        await runner.stop()
        assert not runner.is_running

    asyncio.run(lifecycle())
    fixture.engine.dispose()


def _wait_for_terminal(client: TestClient, job_id: str) -> dict:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
    raise AssertionError("Export Job did not reach a terminal state")


def _seed_product_snapshot(
    client: TestClient,
    staging: Path,
    artifact_roots: ArtifactStorageRoots,
    *,
    muted: bool = False,
) -> tuple[UUID, UUID]:
    owner_id = uuid4()
    workspaces = client.app.state.workspace_service
    assert isinstance(workspaces, WorkspaceService)
    workspace = workspaces.create_workspace(owner_id=owner_id, name="Export Product")
    project = workspaces.create_project(
        workspace_id=workspace.workspace_id,
        title="Canonical WAV",
        created_by=owner_id,
    )
    assets = client.app.state.asset_service
    assert isinstance(assets, AssetService)
    asset = assets.create_asset(
        owner_id=owner_id,
        workspace_id=workspace.workspace_id,
        asset_type=AssetType.MUSIC,
    )
    version = assets.create_asset_version(
        asset_id=asset.asset_id,
        version_origin="user_created",
        settings_snapshot={},
        created_by=owner_id,
    )
    workspaces.attach_asset(
        project_id=project.project_id,
        asset_id=asset.asset_id,
        display_order=0,
        role="music",
    )
    source = staging / "product-source.wav"
    source.write_bytes(_source_wav())
    ArtifactIngestionService(
        client.app.state.session_factory,
        artifact_roots=artifact_roots,
        staging_root=staging,
    ).ingest(
        ArtifactIngestionRequest(
            asset_version_id=version.asset_version_id,
            artifact_kind="audio",
            producer_type="provider",
            producer_id="product-test",
            run_id="product-test",
            storage_domain="music",
            temporary_path=source,
            expected_media_type="audio/wav",
            expected_sha256=None,
            original_filename=source.name,
        )
    )
    snapshot = (
        CompositionService(client.app.state.session_factory)
        .create_snapshot(
            project_id=project.project_id,
            effective_owner_id=owner_id,
            items=[SnapshotItemInput(version.asset_version_id, "music", 0)],
            mix_settings_snapshot={},
            provider_versions={},
            model_manifest_ids={},
            idempotency_key="product-snapshot",
        )
        .aggregate.snapshot
    )
    track_id = uuid4()
    with client.app.state.session_factory() as session, session.begin():
        session.add(
            CompositionSnapshotTrack(
                snapshot_track_id=track_id,
                composition_snapshot_id=snapshot.composition_snapshot_id,
                canonical_track_id=uuid4(),
                track_type="audio",
                name="Product Track",
                track_order=0,
                gain_db=Decimal("0"),
                pan=Decimal("0"),
                muted=muted,
                solo=False,
            )
        )
        session.flush()
        session.add(
            CompositionSnapshotClip(
                composition_snapshot_id=snapshot.composition_snapshot_id,
                snapshot_track_id=track_id,
                canonical_clip_id=uuid4(),
                source_asset_version_id=version.asset_version_id,
                timeline_start=0,
                source_in=0,
                source_out=1_000_000,
                source_duration=1_000_000,
                timeline_duration=1_000_000,
                loop_enabled=False,
                loop_phase=0,
                gain_db=Decimal("0"),
                fade_in=0,
                fade_out=0,
            )
        )
    return project.project_id, snapshot.composition_snapshot_id


def _product_app(tmp_path: Path, *, poll_interval: float = 0.01, runner_enabled: bool = True):
    artifact_root = tmp_path / "artifacts"
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    for domain in APPROVED_STORAGE_DOMAINS:
        (artifact_root / domain).mkdir(parents=True, exist_ok=True)
    roots = ArtifactStorageRoots.from_base_root(artifact_root)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{(tmp_path / 'product.db').as_posix()}",
            auto_migrate=True,
            cursor_signing_key="test-workspace-cursor-signing-key-32-bytes",
            storage_root=tmp_path / "legacy-storage",
            artifact_root=artifact_root if runner_enabled else None,
            artifact_staging_root=staging if runner_enabled else None,
            export_worker_poll_interval_seconds=poll_interval,
            log_level="WARNING",
        )
    )
    return app, staging, roots


def test_api_created_export_completes_through_production_runner(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    artifact_root = tmp_path / "artifacts"
    staging = tmp_path / "staging"
    staging.mkdir()
    for domain in APPROVED_STORAGE_DOMAINS:
        (artifact_root / domain).mkdir(parents=True)
    artifact_roots = ArtifactStorageRoots.from_base_root(artifact_root)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{(tmp_path / 'product.db').as_posix()}",
            auto_migrate=True,
            cursor_signing_key="test-workspace-cursor-signing-key-32-bytes",
            storage_root=tmp_path / "legacy-storage",
            artifact_root=artifact_root,
            artifact_staging_root=staging,
            export_worker_poll_interval_seconds=0.01,
            log_level="WARNING",
        )
    )

    with TestClient(app) as client:
        assert isinstance(client.app.state.export_worker_runner, ExportWorkerRunner)
        assert client.app.state.export_worker_runner.is_running
        project_id, snapshot_id = _seed_product_snapshot(client, staging, artifact_roots)
        request = {
            "project_id": str(project_id),
            "job_type": "export",
            "composition_snapshot_id": str(snapshot_id),
            "inputs": [],
            "settings_snapshot": {"format": "wav"},
        }
        created = client.post(
            "/api/v1/jobs",
            json=request,
            headers={"Idempotency-Key": "product-export"},
        )
        replay = client.post(
            "/api/v1/jobs",
            json=request,
            headers={"Idempotency-Key": "product-export"},
        )
        assert created.status_code == replay.status_code == 201
        assert created.json()["data"]["job_id"] == replay.json()["data"]["job_id"]
        terminal = _wait_for_terminal(client, created.json()["data"]["job_id"])
        assert terminal["status"] == "succeeded"
        assert terminal["inputs"] == []
        assert len(terminal["outputs"]) == 1
        artifact_id = terminal["outputs"][0]["artifact_id"]
        downloaded = client.get(f"/api/v1/artifacts/{artifact_id}/content")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(downloaded.content), "rb") as output:
            assert output.getframerate() == 48_000
            assert output.getnchannels() == 2
            assert output.getsampwidth() == 2
            assert output.getnframes() == 48_000


def test_all_muted_product_export_is_valid_timeline_silence(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    app, staging, roots = _product_app(tmp_path)
    with TestClient(app) as client:
        project_id, snapshot_id = _seed_product_snapshot(client, staging, roots, muted=True)
        created = client.post(
            "/api/v1/jobs",
            json={
                "project_id": str(project_id),
                "job_type": "export",
                "composition_snapshot_id": str(snapshot_id),
                "inputs": [],
                "settings_snapshot": {"format": "wav"},
            },
            headers={"Idempotency-Key": "all-muted-export"},
        )
        terminal = _wait_for_terminal(client, created.json()["data"]["job_id"])
        assert terminal["status"] == "succeeded"
        artifact_id = terminal["outputs"][0]["artifact_id"]
        downloaded = client.get(f"/api/v1/artifacts/{artifact_id}/content")
        with wave.open(io.BytesIO(downloaded.content), "rb") as output:
            assert (output.getframerate(), output.getnchannels(), output.getsampwidth()) == (
                48_000,
                2,
                2,
            )
            assert output.getnframes() == 48_000
            assert set(output.readframes(output.getnframes())) <= {0}
        with client.app.state.session_factory() as session:
            job_id = UUID(terminal["job_id"])
            assert session.get(JobExportPublication, job_id).state.value == "completed"
            result = session.get(JobExportResult, job_id)
            assert result is not None
            assert result.integrated_loudness_lufs is None
            assert result.true_peak_dbtp is None
            assert not result.loudness_passed
            assert result.true_peak_passed
            assert not result.overall_pass
            assert session.query(JobExportPublication).count() == 1
            assert session.query(JobExportResult).count() == 1
            assert session.query(JobOutput).count() == 1
            assert session.query(ProjectAsset).filter(ProjectAsset.role == "export").count() == 1
            assert session.query(AssetVersion).count() == 2
            assert session.query(Artifact).count() == 2


def test_queued_product_export_survives_full_lifespan_restart(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    first_app, staging, roots = _product_app(tmp_path, runner_enabled=False)
    with TestClient(first_app) as client:
        project_id, snapshot_id = _seed_product_snapshot(client, staging, roots)
        created = client.post(
            "/api/v1/jobs",
            json={
                "project_id": str(project_id),
                "job_type": "export",
                "composition_snapshot_id": str(snapshot_id),
                "inputs": [],
                "settings_snapshot": {"format": "wav"},
            },
            headers={"Idempotency-Key": "restart-export"},
        )
        job_id = created.json()["data"]["job_id"]
        assert client.get(f"/api/v1/jobs/{job_id}").json()["data"]["status"] == "queued"

    restarted_app, _, _ = _product_app(tmp_path)
    with TestClient(restarted_app) as client:
        terminal = _wait_for_terminal(client, job_id)
        assert terminal["status"] == "succeeded"
        artifact_id = terminal["outputs"][0]["artifact_id"]
        downloaded = client.get(f"/api/v1/artifacts/{artifact_id}/content")
        assert downloaded.status_code == 200
        with wave.open(io.BytesIO(downloaded.content), "rb") as output:
            assert output.getnframes() == 48_000
        with client.app.state.session_factory() as session:
            assert session.query(JobExportPublication).count() == 1
            assert session.query(JobExportResult).count() == 1
            assert session.query(JobOutput).count() == 1
            assert session.query(ProjectAsset).filter(ProjectAsset.role == "export").count() == 1
            assert session.query(AssetVersion).count() == 2
            assert session.query(Artifact).count() == 2


def test_different_product_jobs_share_export_asset_but_not_versions(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    app, staging, roots = _product_app(tmp_path)
    with TestClient(app) as client:
        project_id, snapshot_id = _seed_product_snapshot(client, staging, roots)
        request = {
            "project_id": str(project_id),
            "job_type": "export",
            "composition_snapshot_id": str(snapshot_id),
            "inputs": [],
            "settings_snapshot": {"format": "wav"},
        }
        jobs = [
            client.post(
                "/api/v1/jobs",
                json=request,
                headers={"Idempotency-Key": f"distinct-export-{index}"},
            ).json()["data"]["job_id"]
            for index in range(2)
        ]
        assert jobs[0] != jobs[1]
        assert all(_wait_for_terminal(client, job_id)["status"] == "succeeded" for job_id in jobs)
        with client.app.state.session_factory() as session:
            assert session.query(ProjectAsset).filter(ProjectAsset.role == "export").count() == 1
            assert session.query(JobExportPublication).count() == 2
            assert session.query(JobExportResult).count() == 2
            assert session.query(JobOutput).count() == 2
            assert session.query(AssetVersion).count() == 3
            assert session.query(Artifact).count() == 3
