"""Database-level proof for completed Export response-loss replay."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.models.workspace import (
    Artifact,
    ArtifactStorageLocation,
    Asset,
    AssetVersion,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    JobStatus,
)
from backend.services.workspace.export_job_completion_service import (
    ExportJobCompletionError,
    ExportJobCompletionErrorCode,
)
from backend.services.workspace.export_worker_service import ExportWorkerError, ExportWorkerService
from backend.tests.test_export_job_completion_uow import _request, _service
from backend.tests.test_export_publication_ledger import LedgerFixture
from backend.tests.test_trusted_artifact_registration import _published

COUNTED = (
    Asset,
    AssetVersion,
    Artifact,
    ArtifactStorageLocation,
    JobOutput,
    JobExportResult,
    JobExportPublication,
)


@pytest.fixture
def completed_export(tmp_path):
    fixture = LedgerFixture(tmp_path)
    _published(fixture)
    completed = _service(fixture).complete(_request(fixture))
    yield fixture, completed
    fixture.engine.dispose()


def _counts(fixture):
    with fixture.factory() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in COUNTED)


def _corrupt(fixture, statement: str, parameters: tuple = ()) -> None:
    with fixture.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(statement, parameters)
        connection.commit()


def test_completed_replay_is_fresh_and_cardinality_read_only(completed_export) -> None:
    fixture, original = completed_export
    counts_before = _counts(fixture)
    physical_before = tuple(fixture.roots.roots["music"].rglob("*.wav"))

    replay = _service(fixture).replay_completed(fixture.job.job_id)

    assert replay is not None
    assert replay.export_result.job_id == original.export_result.job_id
    assert replay.export_result.exported_asset_version_id == (
        original.export_result.exported_asset_version_id
    )
    assert replay.export_result.exported_artifact_id == original.export_result.exported_artifact_id
    assert replay.job_output.artifact_id == original.job_output.artifact_id
    assert _counts(fixture) == counts_before
    assert tuple(fixture.roots.roots["music"].rglob("*.wav")) == physical_before


def test_fresh_worker_terminal_replay_skips_execution_dependencies(completed_export) -> None:
    fixture, original = completed_export
    worker = ExportWorkerService(
        fixture.factory,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=_service(fixture),
        analyzer=object(),
    )

    replay = worker.execute_owned_claim(
        job_id=fixture.job.job_id,
        claimed_by="fresh-worker",
        claim_token=uuid4(),
    )

    assert replay.export_result.job_id == original.export_result.job_id
    assert replay.export_result.exported_artifact_id == original.export_result.exported_artifact_id


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        ("UPDATE job_export_publications SET state = 'intended' WHERE job_id = ?", None),
        ("UPDATE job_export_publications SET state = 'published' WHERE job_id = ?", None),
        (
            "UPDATE job_export_publications SET state = 'reconciliation_required' WHERE job_id = ?",
            None,
        ),
        ("DELETE FROM job_export_results WHERE job_id = ?", None),
        ("DELETE FROM job_outputs WHERE job_id = ?", None),
        ("UPDATE job_export_results SET exported_artifact_id = ? WHERE job_id = ?", "uuid"),
        ("UPDATE job_export_publications SET artifact_id = ? WHERE job_id = ?", "uuid"),
        ("UPDATE job_export_results SET composition_snapshot_id = ? WHERE job_id = ?", "uuid"),
        ("UPDATE job_export_results SET export_format = 'mp3' WHERE job_id = ?", None),
        ("DELETE FROM artifact_storage_locations WHERE artifact_id = ?", "artifact"),
        (
            "UPDATE artifact_storage_locations SET storage_domain = 'audio' WHERE artifact_id = ?",
            "artifact",
        ),
        (
            "UPDATE artifact_storage_locations SET storage_key = 'wrong.wav' WHERE artifact_id = ?",
            "artifact",
        ),
    ],
)
def test_inconsistent_succeeded_authority_fails_closed(
    completed_export, statement, parameters
) -> None:
    fixture, completed = completed_export
    job_id = fixture.job.job_id
    if parameters == "uuid":
        values = (uuid4().hex, job_id.hex)
    elif parameters == "artifact":
        values = (completed.export_result.exported_artifact_id.hex,)
    else:
        values = (job_id.hex,)
    _corrupt(fixture, statement, values)
    counts_before = _counts(fixture)

    with pytest.raises(ExportJobCompletionError) as error:
        _service(fixture).replay_completed(job_id)

    assert error.value.code is ExportJobCompletionErrorCode.CONFLICT
    assert _counts(fixture) == counts_before


@pytest.mark.parametrize("status", [JobStatus.CANCELLED, JobStatus.FAILED])
def test_non_success_terminal_job_is_not_replayed(completed_export, status) -> None:
    fixture, _completed = completed_export
    _corrupt(
        fixture,
        "UPDATE jobs SET status = ? WHERE job_id = ?",
        (status.value, fixture.job.job_id.hex),
    )

    assert _service(fixture).replay_completed(fixture.job.job_id) is None
    worker = ExportWorkerService(
        fixture.factory,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=_service(fixture),
        analyzer=object(),
    )
    with pytest.raises(ExportWorkerError, match="EXPORT_STALE_CLAIM"):
        worker.execute_owned_claim(
            job_id=fixture.job.job_id,
            claimed_by="stale",
            claim_token=uuid4(),
        )


def test_missing_exported_asset_version_fails_closed(completed_export) -> None:
    fixture, completed = completed_export
    _corrupt(
        fixture,
        "DELETE FROM asset_versions WHERE asset_version_id = ?",
        (completed.export_result.exported_asset_version_id.hex,),
    )

    with pytest.raises(ExportJobCompletionError) as error:
        _service(fixture).replay_completed(fixture.job.job_id)

    assert error.value.code is ExportJobCompletionErrorCode.CONFLICT


@pytest.mark.parametrize("target", ["asset_parent", "artifact_lineage"])
def test_export_lineage_mismatch_fails_closed(completed_export, target) -> None:
    fixture, completed = completed_export
    with fixture.factory() as session:
        unrelated_version = session.scalar(
            select(AssetVersion).where(
                AssetVersion.asset_version_id != completed.export_result.exported_asset_version_id
            )
        )
        assert unrelated_version is not None
    if target == "asset_parent":
        statement = (
            "UPDATE asset_versions SET asset_id = ?, version_number = 999 "
            "WHERE asset_version_id = ?"
        )
        values = (
            unrelated_version.asset_id.hex,
            completed.export_result.exported_asset_version_id.hex,
        )
    else:
        statement = "UPDATE artifacts SET asset_version_id = ? WHERE artifact_id = ?"
        values = (
            unrelated_version.asset_version_id.hex,
            completed.export_result.exported_artifact_id.hex,
        )
    _corrupt(fixture, statement, values)

    with pytest.raises(ExportJobCompletionError) as error:
        _service(fixture).replay_completed(fixture.job.job_id)

    assert error.value.code is ExportJobCompletionErrorCode.CONFLICT


def test_stale_running_job_is_not_intercepted_by_terminal_replay(completed_export) -> None:
    fixture, _completed = completed_export
    claim_token = uuid4()
    _corrupt(
        fixture,
        "UPDATE jobs SET status = 'running', claimed_by = 'current-worker', "
        "claim_token = ? WHERE job_id = ?",
        (claim_token.hex, fixture.job.job_id.hex),
    )
    worker = ExportWorkerService(
        fixture.factory,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=_service(fixture),
        analyzer=object(),
    )

    with pytest.raises(ExportWorkerError, match="EXPORT_STALE_CLAIM"):
        worker.execute_owned_claim(
            job_id=fixture.job.job_id,
            claimed_by="stale-worker",
            claim_token=uuid4(),
        )

    with fixture.factory() as session:
        job = session.get(Job, fixture.job.job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.claimed_by == "current-worker"
        assert job.claim_token == claim_token
