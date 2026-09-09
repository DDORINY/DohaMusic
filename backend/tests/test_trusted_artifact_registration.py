"""Trusted durable publication to Artifact catalog registration contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.models.workspace import Artifact, ArtifactStorageLocation, JobExportPublication
from backend.repositories.workspace import ArtifactStorageRepository
from backend.repositories.workspace.export_publication_repository import (
    ExportPublicationRepository,
)
from backend.services.workspace.artifact_ingestion_service import ArtifactIngestionService
from backend.services.workspace.trusted_artifact_registration_service import (
    TrustedArtifactRegistrationError,
    TrustedArtifactRegistrationErrorCode,
    TrustedArtifactRegistrationOutcome,
    TrustedArtifactRegistrationRequest,
    TrustedArtifactRegistrationService,
)
from backend.storage.artifact_resolver import ArtifactStorageResolver
from backend.tests.test_export_publication_ledger import LedgerFixture


def _published(fixture: LedgerFixture):
    fixture.intent()
    payload = fixture.wav()
    fixture.set_integrity(payload)
    fixture.service().publish_or_recover(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
        staged_payload=payload,
    )
    return payload


def _service(fixture: LedgerFixture) -> TrustedArtifactRegistrationService:
    ingestion = ArtifactIngestionService(
        fixture.factory,
        artifact_roots=fixture.roots,
        staging_root=fixture.staging,
    )
    return TrustedArtifactRegistrationService(
        fixture.factory,
        publisher=fixture.publisher,
        ingestion_service=ingestion,
    )


def _request(fixture: LedgerFixture) -> TrustedArtifactRegistrationRequest:
    return TrustedArtifactRegistrationRequest(
        job_id=fixture.job.job_id,
        claimed_by=fixture.claimed_by,
        claim_token=fixture.claim_token,
        asset_version_id=fixture.version.asset_version_id,
        producer_id="canonical-export",
        run_id=str(fixture.job.job_id),
    )


@pytest.fixture
def registration(tmp_path):
    fixture = LedgerFixture(tmp_path)
    _published(fixture)
    yield fixture
    fixture.engine.dispose()


def test_registers_existing_publication_without_republish_and_resolver_handoff(registration):
    before = list(registration.roots.roots["music"].rglob("*.wav"))
    result = _service(registration).register(_request(registration))
    after = list(registration.roots.roots["music"].rglob("*.wav"))

    assert result.outcome is TrustedArtifactRegistrationOutcome.REGISTERED_NEW
    assert before == after
    with registration.factory() as session:
        ledger = session.get(JobExportPublication, registration.job.job_id)
        location = session.scalar(
            select(ArtifactStorageLocation).where(
                ArtifactStorageLocation.artifact_id == result.artifact.artifact_id
            )
        )
        assert ledger.artifact_id == result.artifact.artifact_id
        assert location.storage_key == ledger.storage_key
        resolver = ArtifactStorageResolver(ArtifactStorageRepository(session), registration.roots)
        with resolver.open_payload(result.artifact.artifact_id) as (resolved, stream):
            assert resolved.size_bytes == result.artifact.size_bytes
            assert stream.read(4) == b"RIFF"


def test_registration_replay_and_fresh_process_return_same_artifact(registration):
    first = _service(registration).register(_request(registration))
    replay = _service(registration).register(_request(registration))
    assert replay.outcome is TrustedArtifactRegistrationOutcome.REPLAYED_EXISTING
    assert replay.artifact.artifact_id == first.artifact.artifact_id
    with registration.factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactStorageLocation)) == 1


def test_registration_requires_published_ledger_and_current_claim(tmp_path):
    fixture = LedgerFixture(tmp_path)
    fixture.intent()
    try:
        with pytest.raises(TrustedArtifactRegistrationError) as intended:
            _service(fixture).register(_request(fixture))
        assert intended.value.code is TrustedArtifactRegistrationErrorCode.INVALID_LEDGER
        request = _request(fixture)
        stale = TrustedArtifactRegistrationRequest(
            job_id=request.job_id,
            claimed_by=request.claimed_by,
            claim_token=uuid4(),
            asset_version_id=request.asset_version_id,
        )
        with pytest.raises(TrustedArtifactRegistrationError) as stale_error:
            _service(fixture).register(stale)
        assert stale_error.value.code is TrustedArtifactRegistrationErrorCode.STALE_CLAIM
    finally:
        fixture.engine.dispose()


def test_tamper_fails_before_any_catalog_registration(registration):
    final = next(registration.roots.roots["music"].rglob("*.wav"))
    final.write_bytes(b"tampered")
    with pytest.raises(TrustedArtifactRegistrationError) as error:
        _service(registration).register(_request(registration))
    assert error.value.code is (
        TrustedArtifactRegistrationErrorCode.PUBLICATION_VERIFICATION_FAILED
    )
    with registration.factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert session.scalar(select(func.count()).select_from(ArtifactStorageLocation)) == 0
        assert session.get(JobExportPublication, registration.job.job_id).artifact_id is None


def test_concurrent_registration_converges_on_one_catalog_authority(registration):
    def register():
        return _service(registration).register(_request(registration)).artifact.artifact_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _: register(), range(2)))
    assert identities[0] == identities[1]
    with registration.factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactStorageLocation)) == 1


@pytest.mark.parametrize("failure_point", ["artifact", "storage", "ledger"])
def test_registration_failure_rolls_back_catalog_and_retains_payload(
    registration, monkeypatch, failure_point
):
    final = next(registration.roots.roots["music"].rglob("*.wav"))
    if failure_point == "artifact":
        monkeypatch.setattr(
            ArtifactIngestionService,
            "register_prepared",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("artifact")),
        )
    elif failure_point == "storage":
        monkeypatch.setattr(
            ArtifactStorageRepository,
            "add_storage_location",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage")),
        )
    else:
        monkeypatch.setattr(
            ExportPublicationRepository,
            "bind_artifact",
            lambda *_args, **_kwargs: None,
        )

    with pytest.raises(TrustedArtifactRegistrationError):
        _service(registration).register(_request(registration))
    assert final.exists()
    with registration.factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert session.scalar(select(func.count()).select_from(ArtifactStorageLocation)) == 0
        assert session.get(JobExportPublication, registration.job.job_id).artifact_id is None
