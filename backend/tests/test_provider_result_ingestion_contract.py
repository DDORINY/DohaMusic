"""Metadata-only DohaVocal result의 trusted ingestion gate 계약 검증."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401
from backend.contracts.vocal_jobs import VOCAL_JOB_INPUT_SETTINGS_KEY
from backend.db.base import Base
from backend.db.sqlite import configure_sqlite_foreign_keys
from backend.models.workspace import (
    Artifact,
    Asset,
    AssetType,
    AssetVersion,
    CompositionSnapshot,
    Job,
    JobInput,
    JobOutput,
    JobStatus,
    ModelUsage,
    MusicProject,
    ProcessingChain,
    ProjectAsset,
    ProviderJobBinding,
    Workspace,
)
from backend.providers.vocal import (
    VocalArtifactLineage,
    VocalPayloadBackedResultCandidate,
    VocalPayloadSource,
    VocalProviderPayloadEntry,
    VocalProviderResultCandidate,
)
from backend.services.workspace import (
    IngestionDecisionReason,
    ProviderResultContractError,
    ProviderResultContractErrorReason,
    ProviderResultIngestionService,
    ProviderResultNotIngestibleError,
)


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("provider-result")
    engine = configure_sqlite_foreign_keys(
        create_engine(f"sqlite:///{(tmp_path / 'provider-result.db').as_posix()}")
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Workspace.__table__,
            MusicProject.__table__,
            ProjectAsset.__table__,
            ProcessingChain.__table__,
            CompositionSnapshot.__table__,
            Asset.__table__,
            AssetVersion.__table__,
            Artifact.__table__,
            Job.__table__,
            JobInput.__table__,
            JobOutput.__table__,
            ModelUsage.__table__,
            ProviderJobBinding.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@dataclass(frozen=True)
class ContractGraph:
    owner_id: UUID
    job_id: UUID
    binding_id: UUID
    source_version_id: UUID
    parent_version_id: UUID
    source_artifact_id: UUID
    chain_id: UUID
    provider_job_id: str


def _seed_contract(
    factory,
    *,
    job_type: str = "voice_conversion",
    owner_id: UUID | None = None,
    api_contract_version: str = "0.1.0",
) -> ContractGraph:
    owner = owner_id or uuid4()
    provider_job_id = f"provider-job-{uuid4()}"
    with factory.begin() as session:
        workspace = Workspace(owner_id=owner, name="Trusted result", lifecycle_status="active")
        session.add(workspace)
        session.flush()
        project = MusicProject(
            workspace_id=workspace.workspace_id,
            title="Vocal result",
            description=None,
            lifecycle_status="active",
            created_by=owner,
        )
        session.add(project)
        asset = Asset(
            workspace_id=workspace.workspace_id,
            owner_id=owner,
            asset_type=AssetType.VOCAL,
            lifecycle_status="active",
        )
        session.add(asset)
        session.flush()
        source = AssetVersion(
            asset_id=asset.asset_id,
            version_number=1,
            version_origin="user_created",
            settings_snapshot={},
            created_by=owner,
        )
        session.add(source)
        session.flush()
        parent = AssetVersion(
            asset_id=asset.asset_id,
            version_number=2,
            version_origin="user_edited",
            parent_asset_version_id=source.asset_version_id,
            settings_snapshot={},
            created_by=owner,
        )
        session.add(parent)
        source_artifact = Artifact(
            asset_version_id=source.asset_version_id,
            artifact_kind="audio",
            media_type="audio/wav",
            size_bytes=8,
            checksum_algorithm="sha256",
            artifact_checksum="1" * 64,
            producer_type="user",
            retention_status="active",
        )
        chain = ProcessingChain(
            name=f"vocal-{uuid4()}",
            chain_version="1",
            chain_checksum=uuid4().hex * 2,
            created_by=owner,
        )
        session.add_all((source_artifact, chain))
        session.flush()
        job_input = {
            "job_type": job_type,
            "source_asset_version_id": str(source.asset_version_id),
            "parent_asset_version_id": str(parent.asset_version_id),
            "processing_chain_id": str(chain.processing_chain_id),
        }
        if job_type == "voice_conversion":
            job_input.update(
                {
                    "voice_reference_artifact_id": str(source_artifact.artifact_id),
                    "source_entity_type": "recording_take",
                    "reference_entity_type": "voice_enrollment_sample",
                    "training_dataset_id": None,
                }
            )
        elif job_type == "vocal_correction":
            job_input["correction_types"] = ["pitch_correction"]
        elif job_type == "vocal_analysis":
            job_input["analysis_types"] = ["pitch"]
        job = Job(
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            job_type=job_type,
            status=JobStatus.RUNNING,
            provider_id="dohavocal",
            api_contract_version=api_contract_version,
            model_manifest_id=(
                "dynamic-vocal@2" if api_contract_version == "0.2.0" else "dynamic-vocal@1"
            ),
            progress_percent=10,
            settings_snapshot={
                "strength": 0.5,
                VOCAL_JOB_INPUT_SETTINGS_KEY: job_input,
            },
            requested_by=owner,
            attempt=1,
        )
        session.add(job)
        session.flush()
        session.add_all(
            (
                JobInput(
                    job_id=job.job_id,
                    artifact_id=source_artifact.artifact_id,
                    input_role="source_vocal",
                    input_order=0,
                ),
                JobInput(
                    job_id=job.job_id,
                    artifact_id=source_artifact.artifact_id,
                    input_role="voice_reference",
                    input_order=1,
                ),
            )
        )
        binding = ProviderJobBinding(
            workspace_job_id=job.job_id,
            provider_id="dohavocal",
            provider_job_id=provider_job_id,
        )
        session.add(binding)
        session.flush()
        return ContractGraph(
            owner,
            job.job_id,
            binding.provider_job_binding_id,
            source.asset_version_id,
            parent.asset_version_id,
            source_artifact.artifact_id,
            chain.processing_chain_id,
            provider_job_id,
        )


def _candidate(
    graph: ContractGraph,
    *,
    artifact_kind: str = "audio",
    analysis_result: dict | None = None,
) -> VocalProviderResultCandidate:
    checksum = "a" * 64
    return VocalProviderResultCandidate(
        artifact_id=str(uuid4()),
        artifact_kind=artifact_kind,
        media_type=("application/json" if artifact_kind == "analysis" else "audio/wav"),
        size_bytes=0,
        checksum_algorithm="sha256",
        artifact_checksum=checksum,
        checksum_scope="metadata_descriptor",
        payload_present=False,
        producer_type="provider",
        producer_id="dohavocal",
        run_id=graph.provider_job_id,
        retention_status="candidate",
        output_asset_version_id=str(uuid4()),
        lineage=VocalArtifactLineage(
            source_asset_version_id=str(graph.source_version_id),
            parent_asset_version_id=str(graph.parent_version_id),
            processing_chain_id=str(graph.chain_id),
            provider_id="dohavocal",
            model_manifest_id="dynamic-vocal@1",
            settings_snapshot={"strength": 0.5},
            processing_types=("voice_conversion",),
            created_at=datetime.now(UTC),
            checksum=checksum,
            checksum_scope="metadata_descriptor",
            source_artifact_id=str(graph.source_artifact_id),
            parent_artifact_id=str(graph.source_artifact_id),
            job_id=graph.provider_job_id,
        ),
        analysis_result=analysis_result,
    )


def _payload_candidate(graph: ContractGraph) -> VocalPayloadBackedResultCandidate:
    legacy = _candidate(graph)
    lineage = legacy.lineage.model_copy(update={"model_manifest_id": "dynamic-vocal@2"})
    return VocalPayloadBackedResultCandidate(
        **legacy.model_dump(exclude={"payload_present", "lineage", "media_type", "size_bytes"}),
        payload_present=True,
        lineage=lineage,
        media_type="audio/wav",
        size_bytes=7,
        payloads=(
            VocalProviderPayloadEntry(
                provider_artifact_id="provider-artifact-001",
                role="converted_vocal_candidate",
                source=VocalPayloadSource(kind="provider_subresource", source_id="content-001"),
                checksum_algorithm="sha256",
                payload_checksum=(
                    "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
                ),
                expected_size_bytes=7,
                expected_media_type="audio/wav",
                available_until=datetime(2099, 1, 1, tzinfo=UTC),
            ),
        ),
    )


def _validate(factory, graph, candidate, *, output_role="converted_vocal_candidate"):
    with factory() as session, session.begin():
        return ProviderResultIngestionService().validate_candidate_for_owner(
            session,
            effective_owner_id=graph.owner_id,
            workspace_job_id=graph.job_id,
            provider_job_binding_id=graph.binding_id,
            output_role=output_role,
            wire_candidate=candidate,
        )


def _counts(factory) -> tuple[int, int, int, int]:
    with factory() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(entity)) or 0
            for entity in (Artifact, AssetVersion, JobOutput, ModelUsage)
        )


def test_metadata_only_result_is_trusted_but_not_ingestion_eligible(
    session_factory,
) -> None:
    graph = _seed_contract(session_factory)
    before = _counts(session_factory)

    result = _validate(session_factory, graph, _candidate(graph))

    assert result.reason is IngestionDecisionReason.PAYLOAD_ABSENT
    assert result.eligible_for_binary_ingestion is False
    assert result.eligible_for_structured_ingestion is False
    assert result.candidate.output_role == "converted_vocal_candidate"
    assert result.candidate.checksum_scope == "metadata_descriptor"
    assert result.candidate.payload_reference is None
    assert result.candidate.idempotency_key == (
        graph.binding_id,
        "converted_vocal_candidate",
        result.candidate.provider_artifact_id,
    )
    with pytest.raises(ProviderResultNotIngestibleError) as error:
        result.candidate.require_payload_reference()
    assert error.value.reason is IngestionDecisionReason.PAYLOAD_ABSENT
    assert _counts(session_factory) == before


def test_revalidation_is_idempotent_and_has_no_side_effects(session_factory) -> None:
    graph = _seed_contract(session_factory)
    candidate = _candidate(graph)
    before = _counts(session_factory)

    first = _validate(session_factory, graph, candidate)
    second = _validate(session_factory, graph, candidate)

    assert first == second
    assert _counts(session_factory) == before


def test_payload_result_is_trusted_as_acquisition_candidate_only(
    session_factory,
) -> None:
    graph = _seed_contract(session_factory, api_contract_version="0.2.0")
    before = _counts(session_factory)

    result = _validate(session_factory, graph, _payload_candidate(graph))

    assert result.reason is IngestionDecisionReason.PAYLOAD_ACQUISITION_REQUIRED
    assert result.eligible_for_binary_ingestion is False
    assert result.candidate.payload_present is True
    assert result.candidate.provider_result_artifact_id != (result.candidate.provider_artifact_id)
    assert result.candidate.payloads[0].source_id == "content-001"
    with pytest.raises(ProviderResultNotIngestibleError) as error:
        result.candidate.require_payload_reference()
    assert error.value.reason is IngestionDecisionReason.PAYLOAD_ACQUISITION_REQUIRED
    assert _counts(session_factory) == before


def test_payload_replay_conflict_is_detected(session_factory) -> None:
    graph = _seed_contract(session_factory, api_contract_version="0.2.0")
    first = _validate(session_factory, graph, _payload_candidate(graph)).candidate
    changed_wire = _payload_candidate(graph)
    changed_payload = changed_wire.payloads[0].model_copy(update={"payload_checksum": "b" * 64})
    changed_wire = changed_wire.model_copy(update={"payloads": (changed_payload,)})
    changed = _validate(session_factory, graph, changed_wire).candidate

    with pytest.raises(ProviderResultContractError) as error:
        ProviderResultIngestionService.validate_replay(first, changed)
    assert error.value.reason is ProviderResultContractErrorReason.RESULT_REPLAY_CONFLICT


def test_result_contract_version_must_match_workspace_job(session_factory) -> None:
    graph = _seed_contract(session_factory)
    with pytest.raises(ProviderResultContractError) as error:
        _validate(session_factory, graph, _payload_candidate(graph))
    assert error.value.reason is ProviderResultContractErrorReason.CONTRACT_VERSION_MISMATCH


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("wrong_workspace", ProviderResultContractErrorReason.WORKSPACE_JOB_MISMATCH),
        ("missing_binding", ProviderResultContractErrorReason.BINDING_MISSING),
        (
            "wrong_provider",
            ProviderResultContractErrorReason.PROVIDER_IDENTITY_MISMATCH,
        ),
        (
            "wrong_provider_job",
            ProviderResultContractErrorReason.PROVIDER_JOB_IDENTITY_MISMATCH,
        ),
    ],
)
def test_binding_and_execution_identity_mismatch_fail_closed(session_factory, case, reason) -> None:
    graph = _seed_contract(session_factory)
    candidate = _candidate(graph)
    kwargs = {
        "effective_owner_id": graph.owner_id,
        "workspace_job_id": graph.job_id,
        "provider_job_binding_id": graph.binding_id,
        "output_role": "converted_vocal_candidate",
        "wire_candidate": candidate,
    }
    if case == "wrong_workspace":
        kwargs["workspace_job_id"] = uuid4()
    elif case == "missing_binding":
        kwargs["provider_job_binding_id"] = uuid4()
    elif case == "wrong_provider":
        kwargs["wire_candidate"] = candidate.model_copy(update={"producer_id": "dohaaudio"})
    else:
        kwargs["wire_candidate"] = candidate.model_copy(update={"run_id": "wrong-job"})

    with (
        pytest.raises(ProviderResultContractError) as error,
        session_factory() as session,
        session.begin(),
    ):
        ProviderResultIngestionService().validate_candidate_for_owner(session, **kwargs)
    assert error.value.reason is reason


def test_cross_job_output_role_is_rejected(session_factory) -> None:
    graph = _seed_contract(session_factory)

    with pytest.raises(ProviderResultContractError) as error:
        _validate(
            session_factory,
            graph,
            _candidate(graph),
            output_role="corrected_vocal_candidate",
        )
    assert error.value.reason is ProviderResultContractErrorReason.OUTPUT_ROLE_MISMATCH


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("manifest", ProviderResultContractErrorReason.MANIFEST_MISMATCH),
        ("settings", ProviderResultContractErrorReason.SETTINGS_MISMATCH),
        ("source", ProviderResultContractErrorReason.LINEAGE_MISMATCH),
        ("parent", ProviderResultContractErrorReason.LINEAGE_MISMATCH),
        ("checksum", ProviderResultContractErrorReason.CHECKSUM_MISMATCH),
    ],
)
def test_manifest_settings_lineage_and_checksum_mismatch_fail_closed(
    session_factory, field, reason
) -> None:
    graph = _seed_contract(session_factory)
    candidate = _candidate(graph)
    lineage = candidate.lineage
    if field == "manifest":
        lineage = lineage.model_copy(update={"model_manifest_id": "other@1"})
    elif field == "settings":
        lineage = lineage.model_copy(update={"settings_snapshot": {"strength": 0.9}})
    elif field == "source":
        lineage = lineage.model_copy(update={"source_asset_version_id": str(uuid4())})
    elif field == "parent":
        lineage = lineage.model_copy(update={"parent_asset_version_id": str(uuid4())})
    else:
        lineage = lineage.model_copy(update={"checksum": "b" * 64})
    candidate = candidate.model_copy(update={"lineage": lineage})

    with pytest.raises(ProviderResultContractError) as error:
        _validate(session_factory, graph, candidate)
    assert error.value.reason is reason


def test_processing_chain_must_match_job_and_effective_owner(session_factory) -> None:
    graph = _seed_contract(session_factory)
    candidate = _candidate(graph)
    with session_factory.begin() as session:
        foreign = ProcessingChain(
            name=f"foreign-{uuid4()}",
            chain_version="1",
            chain_checksum=uuid4().hex * 2,
            created_by=uuid4(),
        )
        session.add(foreign)
        session.flush()
        foreign_id = foreign.processing_chain_id
    candidate = candidate.model_copy(
        update={
            "lineage": candidate.lineage.model_copy(update={"processing_chain_id": str(foreign_id)})
        }
    )

    with pytest.raises(ProviderResultContractError) as error:
        _validate(session_factory, graph, candidate)
    assert error.value.reason is ProviderResultContractErrorReason.PROCESSING_CHAIN_MISMATCH


def test_provider_identifiers_and_paths_never_become_workspace_authority(
    session_factory,
) -> None:
    graph = _seed_contract(session_factory)
    candidate = _candidate(graph).model_copy(update={"artifact_id": "file:///tmp/fake.wav"})
    before = _counts(session_factory)

    with pytest.raises(ProviderResultContractError) as error:
        _validate(session_factory, graph, candidate)
    assert error.value.reason is ProviderResultContractErrorReason.INVALID_CANDIDATE
    assert _counts(session_factory) == before


def test_vocal_analysis_descriptor_is_not_a_structured_artifact_payload(
    session_factory,
) -> None:
    graph = _seed_contract(session_factory, job_type="vocal_analysis")
    candidate = _candidate(
        graph,
        artifact_kind="analysis",
        analysis_result={"pitch": {"status": "fake", "value": None}},
    ).model_copy(
        update={
            "lineage": _candidate(graph).lineage.model_copy(update={"processing_types": ("pitch",)})
        }
    )

    result = _validate(
        session_factory,
        graph,
        candidate,
        output_role="vocal_analysis_result",
    )

    assert result.eligible_for_binary_ingestion is False
    assert result.eligible_for_structured_ingestion is False
    assert result.candidate.analysis_result is not None


def test_validation_failure_has_no_completion_side_effects(session_factory) -> None:
    graph = _seed_contract(session_factory)
    before = _counts(session_factory)
    candidate = _candidate(graph).model_copy(
        update={"output_asset_version_id": "C:\\temp\\fake.wav"}
    )

    with pytest.raises(ProviderResultContractError):
        _validate(session_factory, graph, candidate)

    assert _counts(session_factory) == before
