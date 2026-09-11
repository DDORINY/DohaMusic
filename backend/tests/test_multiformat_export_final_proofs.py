"""Final format-specific concurrency, cancellation, and failure proofs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.audio.export_delivery_encoder import (
    ExportDeliveryEncodingError,
    ExportDeliveryEncodingErrorCode,
)
from backend.audio.export_delivery_validator import (
    ExportDeliveryValidationError,
    ExportDeliveryValidationErrorCode,
)
from backend.models.workspace import JobStatus
from backend.repositories.workspace import JobRepository
from backend.services.workspace.export_job_completion_service import (
    ExportJobCompletionError,
    ExportJobCompletionService,
)
from backend.services.workspace.export_publication_service import (
    ExportPublicationError,
    ExportPublicationErrorCode,
)
from backend.services.workspace.export_worker_service import ExportWorkerError
from backend.tests.support.multiformat_export_fixture import (
    MultiFormatExportIntegrationFixture,
)


@pytest.mark.parametrize("formats", [("mp3", "flac"), ("mp3", "mp3"), ("flac", "flac")])
def test_distinct_jobs_complete_concurrently_on_one_export_asset(
    tmp_path: Path, formats: tuple[str, str]
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creations = [
            fixture.create_job(export_format, f"concurrent-{index}-{export_format}")
            for index, export_format in enumerate(formats)
        ]
        claims = [fixture.claim_next(f"concurrent-{index}") for index in range(2)]
        assert {claim[0] for claim in claims} == {
            creation.aggregate.job.job_id for creation in creations
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(fixture.execute_claim, claims))
        assert len(results) == 2
        assert {fixture.job_status(claim[0]) for claim in claims} == {JobStatus.SUCCEEDED}
        assert fixture.export_counts() == {
            "assets": 1,
            "memberships": 1,
            "versions": 2,
            "artifacts": 2,
            "locations": 2,
            "outputs": 2,
            "results": 2,
            "publications": 2,
            "physical": 2,
        }
    finally:
        fixture.close()


class _CancelAfterEncode:
    def __init__(self, fixture, job_id) -> None:
        self.fixture = fixture
        self.job_id = job_id

    @contextmanager
    def encode(self, canonical_wav, *, export_format):
        with self.fixture.encoder.encode(canonical_wav, export_format=export_format) as delivery:
            self.fixture.cancel(self.job_id)
            yield delivery


class _StaleAfterEncode:
    def __init__(self, fixture, job_id) -> None:
        self.fixture = fixture
        self.job_id = job_id

    @contextmanager
    def encode(self, canonical_wav, *, export_format):
        with self.fixture.encoder.encode(canonical_wav, export_format=export_format) as delivery:
            self.fixture.invalidate_claim(self.job_id)
            yield delivery


class _CancelAfterPublish:
    def __init__(self, fixture, job_id) -> None:
        self.fixture = fixture
        self.job_id = job_id

    def __getattr__(self, name):
        return getattr(self.fixture.publications, name)

    def publish_or_recover(self, **kwargs):
        result = self.fixture.publications.publish_or_recover(**kwargs)
        self.fixture.cancel(self.job_id)
        return result


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_cancellation_after_encode_prevents_publication(tmp_path: Path, export_format: str) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation = fixture.create_job(export_format, f"cancel-encode-{export_format}")
        claim = fixture.claim_next(f"cancel-encode-{export_format}")
        worker = fixture.build_worker(
            encoder=_CancelAfterEncode(fixture, creation.aggregate.job.job_id)
        )
        with pytest.raises(ExportPublicationError):
            fixture.execute_claim(claim, worker=worker)
        assert fixture.job_status(creation.aggregate.job.job_id) is JobStatus.CANCELLED
        counts = fixture.export_counts()
        assert counts["versions"] == 0
        assert counts["artifacts"] == 0
        assert counts["outputs"] == 0
        assert counts["results"] == 0
        assert counts["physical"] == 0
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_cancellation_after_publish_prevents_logical_completion(
    tmp_path: Path, export_format: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation = fixture.create_job(export_format, f"cancel-publish-{export_format}")
        claim = fixture.claim_next(f"cancel-publish-{export_format}")
        worker = fixture.build_worker(
            publications=_CancelAfterPublish(fixture, creation.aggregate.job.job_id)
        )
        with pytest.raises(ExportJobCompletionError):
            fixture.execute_claim(claim, worker=worker)
        assert fixture.job_status(creation.aggregate.job.job_id) is JobStatus.CANCELLED
        counts = fixture.export_counts()
        assert counts["versions"] == 0
        assert counts["outputs"] == 0
        assert counts["results"] == 0
        assert counts["physical"] == 1
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_stale_claim_after_encode_cannot_complete_and_fresh_claim_recovers(
    tmp_path: Path, export_format: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation = fixture.create_job(export_format, f"stale-{export_format}")
        stale_claim = fixture.claim_next(f"stale-{export_format}")
        stale_worker = fixture.build_worker(
            encoder=_StaleAfterEncode(fixture, creation.aggregate.job.job_id)
        )
        with pytest.raises(ExportWorkerError, match="EXPORT_STALE_CLAIM"):
            fixture.execute_claim(stale_claim, worker=stale_worker)
        assert fixture.job_status(creation.aggregate.job.job_id) is JobStatus.RUNNING
        fresh_claim = fixture.recover_and_claim(f"recovered-{export_format}")
        fixture.execute_claim(fresh_claim)
        assert fixture.job_status(creation.aggregate.job.job_id) is JobStatus.SUCCEEDED
        assert fixture.export_counts() == {
            "assets": 1,
            "memberships": 1,
            "versions": 1,
            "artifacts": 1,
            "locations": 1,
            "outputs": 1,
            "results": 1,
            "publications": 1,
            "physical": 1,
        }
    finally:
        fixture.close()


class _FailingEncoder:
    @contextmanager
    def encode(self, canonical_wav, *, export_format):
        raise ExportDeliveryEncodingError(ExportDeliveryEncodingErrorCode.ENCODE_FAILED)
        yield


class _FailingValidator:
    def validate(self, path, *, expected_format, expected_duration_us):
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)


class _FailingIntegrityPublication:
    def __init__(self, fixture) -> None:
        self.fixture = fixture

    def __getattr__(self, name):
        return getattr(self.fixture.publications, name)

    def set_expected_integrity(self, **kwargs):
        raise ExportPublicationError(ExportPublicationErrorCode.CONFLICT)


class _InjectedProcessCrash(BaseException):
    pass


class _CrashAfterTrustedRegistration:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def verify_evidence(self, request):
        return self.delegate.verify_evidence(request)

    def register_in_session(self, session, request, *, evidence):
        self.delegate.register_in_session(session, request, evidence=evidence)
        raise _InjectedProcessCrash


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
@pytest.mark.parametrize("failure", ["encoder", "validator", "integrity"])
def test_format_specific_prepublication_failure_is_atomic(
    tmp_path: Path, export_format: str, failure: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation = fixture.create_job(export_format, f"{failure}-{export_format}")
        claim = fixture.claim_next(f"{failure}-{export_format}")
        kwargs = {}
        if failure == "encoder":
            kwargs["encoder"] = _FailingEncoder()
        elif failure == "validator":
            kwargs["validator"] = _FailingValidator()
        else:
            kwargs["publications"] = _FailingIntegrityPublication(fixture)
        expected_error = {
            "encoder": ExportDeliveryEncodingError,
            "validator": ExportDeliveryValidationError,
            "integrity": ExportPublicationError,
        }[failure]
        with pytest.raises(expected_error):
            fixture.execute_claim(claim, worker=fixture.build_worker(**kwargs))
        assert fixture.job_status(creation.aggregate.job.job_id) is JobStatus.FAILED
        counts = fixture.export_counts()
        assert counts["versions"] == 0
        assert counts["artifacts"] == 0
        assert counts["outputs"] == 0
        assert counts["results"] == 0
        assert counts["physical"] == 0
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_trusted_registration_rollback_and_physical_recovery(
    tmp_path: Path, export_format: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation = fixture.create_job(export_format, f"registration-{export_format}")
        claim = fixture.claim_next(f"registration-{export_format}")
        trusted = vars(fixture.completion)["_registration"]
        crashing_completion = ExportJobCompletionService(
            fixture.base.factory,
            trusted_registration=_CrashAfterTrustedRegistration(trusted),
        )
        with pytest.raises(_InjectedProcessCrash):
            fixture.execute_claim(
                claim, worker=fixture.build_worker(completion=crashing_completion)
            )
        counts = fixture.export_counts()
        assert counts["assets"] == 0
        assert counts["versions"] == 0
        assert counts["artifacts"] == 0
        assert counts["locations"] == 0
        assert counts["outputs"] == 0
        assert counts["results"] == 0
        assert counts["physical"] == 1
        fixture.invalidate_claim(creation.aggregate.job.job_id)
        fixture.execute_claim(fixture.recover_and_claim(f"registration-retry-{export_format}"))
        assert fixture.export_counts() == {
            "assets": 1,
            "memberships": 1,
            "versions": 1,
            "artifacts": 1,
            "locations": 1,
            "outputs": 1,
            "results": 1,
            "publications": 1,
            "physical": 1,
        }
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_late_completion_rollback_and_physical_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_format: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    original = JobRepository.add_job_output

    def crash_after_output(repository, item):
        original(repository, item)
        raise _InjectedProcessCrash

    try:
        creation = fixture.create_job(export_format, f"completion-{export_format}")
        claim = fixture.claim_next(f"completion-{export_format}")
        monkeypatch.setattr(JobRepository, "add_job_output", crash_after_output)
        with pytest.raises(_InjectedProcessCrash):
            fixture.execute_claim(claim)
        counts = fixture.export_counts()
        assert counts["assets"] == 0
        assert counts["versions"] == 0
        assert counts["artifacts"] == 0
        assert counts["locations"] == 0
        assert counts["outputs"] == 0
        assert counts["results"] == 0
        assert counts["physical"] == 1
        monkeypatch.setattr(JobRepository, "add_job_output", original)
        fixture.invalidate_claim(creation.aggregate.job.job_id)
        fixture.execute_claim(fixture.recover_and_claim(f"completion-retry-{export_format}"))
        assert fixture.export_counts() == {
            "assets": 1,
            "memberships": 1,
            "versions": 1,
            "artifacts": 1,
            "locations": 1,
            "outputs": 1,
            "results": 1,
            "publications": 1,
            "physical": 1,
        }
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
@pytest.mark.parametrize("corruption", ["result", "ledger", "storage", "media"])
def test_format_specific_terminal_corruption_is_read_only_and_fails_closed(
    tmp_path: Path, export_format: str, corruption: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        creation, _result = fixture.execute(export_format, f"corrupt-{export_format}")
        job_id = creation.aggregate.job.job_id
        before = fixture.export_counts()
        other_format = "flac" if export_format == "mp3" else "mp3"
        statements = {
            "result": (
                "UPDATE job_export_results SET export_format = :value WHERE job_id = :job_id",
                other_format,
            ),
            "ledger": (
                "UPDATE job_export_publications SET export_format = :value WHERE job_id = :job_id",
                other_format,
            ),
            "storage": (
                "UPDATE artifact_storage_locations SET storage_key = :value "
                "WHERE artifact_id = (SELECT exported_artifact_id FROM job_export_results "
                "WHERE job_id = :job_id)",
                f"corrupt.{export_format}",
            ),
            "media": (
                "UPDATE artifacts SET media_type = :value WHERE artifact_id = "
                "(SELECT exported_artifact_id FROM job_export_results WHERE job_id = :job_id)",
                "application/octet-stream",
            ),
        }
        statement, value = statements[corruption]
        with fixture.base.factory() as session, session.begin():
            session.execute(text(statement), {"value": value, "job_id": job_id.hex})
        with pytest.raises(ExportJobCompletionError):
            fixture.completion.replay_completed(job_id)
        assert fixture.export_counts() == before
    finally:
        fixture.close()
