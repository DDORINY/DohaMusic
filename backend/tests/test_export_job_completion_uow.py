"""Atomic Project Export Asset completion contracts."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.audio.export_quality import evaluate_export_quality
from backend.models.workspace import (
    Artifact,
    Asset,
    AssetType,
    AssetVersion,
    ExportPublicationState,
    Job,
    JobExportPublication,
    JobExportResult,
    JobOutput,
    JobStatus,
    MusicProject,
    ProjectAsset,
)
from backend.repositories.workspace import JobRepository
from backend.services.workspace.artifact_ingestion_service import ArtifactIngestionService
from backend.services.workspace.export_job_completion_service import (
    ExportJobCompletionError,
    ExportJobCompletionErrorCode,
    ExportJobCompletionRequest,
    ExportJobCompletionService,
)
from backend.services.workspace.trusted_artifact_registration_service import (
    TrustedArtifactRegistrationService,
)
from backend.tests.test_export_publication_ledger import LedgerFixture
from backend.tests.test_trusted_artifact_registration import _published


def _service(fixture: LedgerFixture) -> ExportJobCompletionService:
    ingestion = ArtifactIngestionService(
        fixture.factory,
        artifact_roots=fixture.roots,
        staging_root=fixture.staging,
    )
    registration = TrustedArtifactRegistrationService(
        fixture.factory,
        publisher=fixture.publisher,
        ingestion_service=ingestion,
    )
    return ExportJobCompletionService(
        fixture.factory,
        trusted_registration=registration,
    )


def _request(fixture: LedgerFixture, *, token=None) -> ExportJobCompletionRequest:
    return ExportJobCompletionRequest(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=token or fixture.claim_token,
        render_fingerprint="a" * 64,
        quality=evaluate_export_quality(
            integrated_loudness_lufs=-14.0,
            true_peak_dbtp=-1.5,
        ),
        analyzer_name="test-analyzer",
        analyzer_version="1",
    )


@pytest.fixture
def completion(tmp_path):
    fixture = LedgerFixture(tmp_path)
    _published(fixture)
    yield fixture
    fixture.engine.dispose()


def test_completion_is_atomic_replayable_and_uses_one_artifact(completion):
    physical_before = list(completion.roots.roots["music"].rglob("*.wav"))
    service = _service(completion)
    first = service.complete(_request(completion))
    replay = _service(completion).complete(_request(completion))

    assert replay.export_result.job_id == first.export_result.job_id
    assert replay.job_output.artifact_id == first.job_output.artifact_id
    assert first.job_output.artifact_id == first.export_result.exported_artifact_id
    assert list(completion.roots.roots["music"].rglob("*.wav")) == physical_before
    with completion.factory() as session:
        project = session.get(MusicProject, completion.job.project_id)
        membership = session.get(ProjectAsset, project.export_project_asset_id)
        asset = session.get(Asset, membership.asset_id)
        publication = session.get(JobExportPublication, completion.job.job_id)
        assert membership.role == "export"
        assert asset.asset_type is AssetType.EXPORT
        assert publication.state is ExportPublicationState.COMPLETED
        assert session.get(Job, completion.job.job_id).status is JobStatus.SUCCEEDED
        assert session.get(JobExportResult, completion.job.job_id) is not None
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == 2
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(JobOutput)) == 1


def test_cancelled_or_stale_claim_cannot_complete(completion):
    with completion.factory() as session, session.begin():
        job = JobRepository(session).get_job(completion.job.job_id)
        job.cancel_requested_at = job.created_at
    with pytest.raises(ExportJobCompletionError) as cancelled:
        _service(completion).complete(_request(completion))
    assert cancelled.value.code is ExportJobCompletionErrorCode.CANCELLED

    with completion.factory() as session, session.begin():
        job = JobRepository(session).get_job(completion.job.job_id)
        job.cancel_requested_at = None
    with pytest.raises(ExportJobCompletionError) as stale:
        _service(completion).complete(_request(completion, token=uuid4()))
    assert stale.value.code is ExportJobCompletionErrorCode.STALE_CLAIM
