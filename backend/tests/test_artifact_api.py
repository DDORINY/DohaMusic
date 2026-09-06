"""Artifact Metadata·content·download와 single-byte Range 계약 검증."""

from __future__ import annotations

import io
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.api.v1.dependencies import REQUEST_ID_HEADER
from backend.api.v1.range_requests import (
    ByteRange,
    RangeNotSatisfiable,
    parse_single_byte_range,
)
from backend.models.workspace import Artifact, Asset, AssetType, AssetVersion
from backend.repositories.workspace import ArtifactStorageRepository
from backend.services.workspace import (
    ArtifactApplicationService,
    ArtifactIngestionRequest,
    ArtifactIngestionService,
    AssetService,
    WorkspaceService,
)
from backend.storage import ArtifactStorageRoots
from backend.storage.artifact_resolver import APPROVED_STORAGE_DOMAINS


class ArtifactApiFixture:
    def __init__(self, client: TestClient, tmp_path: Path) -> None:
        self.client = client
        self.session_factory = client.app.state.session_factory
        self.root = tmp_path / "artifact-api-root"
        self.staging = tmp_path / "artifact-api-staging"
        self.staging.mkdir()
        for domain in APPROVED_STORAGE_DOMAINS:
            (self.root / domain).mkdir(parents=True)
        self.roots = ArtifactStorageRoots.from_base_root(self.root)
        client.app.state.artifact_application_service = ArtifactApplicationService(
            self.session_factory,
            artifact_roots=self.roots,
        )
        workspace_service = client.app.state.workspace_service
        assert isinstance(workspace_service, WorkspaceService)
        self.owner_id = uuid4()
        self.workspace = workspace_service.create_workspace(
            owner_id=self.owner_id,
            name="Artifact API Workspace",
        )
        self.asset_id, self.version_id = self.seed_lineage(self.owner_id)

    def seed_lineage(self, owner_id: UUID) -> tuple[UUID, UUID]:
        asset_service = self.client.app.state.asset_service
        assert isinstance(asset_service, AssetService)
        asset = asset_service.create_asset(
            owner_id=owner_id,
            workspace_id=(self.workspace.workspace_id if owner_id == self.owner_id else None),
            asset_type=AssetType.MUSIC,
        )
        version = asset_service.create_asset_version(
            asset_id=asset.asset_id,
            version_origin="provider",
            settings_snapshot={},
            created_by=owner_id,
        )
        return asset.asset_id, version.asset_version_id

    def ingest(
        self,
        payload: bytes,
        *,
        owner_id: UUID | None = None,
        artifact_kind: str = "audio",
        retention_status: str = "active",
    ) -> UUID:
        version_id = self.version_id
        if owner_id is not None and owner_id != self.owner_id:
            _asset_id, version_id = self.seed_lineage(owner_id)
        source = self.staging / f"{uuid4()}.wav"
        source.write_bytes(payload)
        result = ArtifactIngestionService(
            self.session_factory,
            artifact_roots=self.roots,
            staging_root=self.staging,
        ).ingest(
            ArtifactIngestionRequest(
                asset_version_id=version_id,
                artifact_kind=artifact_kind,
                producer_type="provider",
                producer_id="test-provider",
                run_id="test-run",
                storage_domain="audio",
                temporary_path=source,
            )
        )
        if retention_status != "active":
            self.update_artifact(
                result.artifact_id,
                retention_status=retention_status,
            )
        return result.artifact_id

    def update_artifact(self, artifact_id: UUID, **values: object) -> None:
        with self.session_factory() as session, session.begin():
            artifact = session.get(Artifact, artifact_id)
            assert artifact is not None
            for name, value in values.items():
                setattr(artifact, name, value)

    def payload_path(self, artifact_id: UUID) -> Path:
        with self.session_factory() as session:
            location = ArtifactStorageRepository(session).get_storage_location(artifact_id)
        assert location is not None
        return self.roots.candidate_path(
            location.storage_domain,
            location.storage_key,
        )


@pytest.fixture
def artifact_api(client: TestClient, tmp_path: Path) -> ArtifactApiFixture:
    return ArtifactApiFixture(client, tmp_path)


def _wav_payload(frames: int = 128) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as payload:
        payload.setnchannels(1)
        payload.setsampwidth(2)
        payload.setframerate(8_000)
        payload.writeframes(bytes(index % 251 for index in range(frames * 2)))
    return output.getvalue()


def _error_code(response) -> str:
    return response.json()["error"]["error_code"]


def test_artifact_endpoints_require_bootstrap(client: TestClient) -> None:
    artifact_id = uuid4()
    for suffix in ("", "/content", "/download"):
        response = client.get(f"/api/v1/artifacts/{artifact_id}{suffix}")
        assert response.status_code == 409
        assert _error_code(response) == "WORKSPACE_BOOTSTRAP_REQUIRED"
        assert response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(
    "retention_status,has_links",
    [
        ("active", True),
        ("quarantined", False),
        ("expired", False),
        ("pending_delete", False),
        ("deleted", False),
    ],
)
def test_metadata_is_owner_scoped_and_supports_retention_matrix(
    artifact_api: ArtifactApiFixture,
    retention_status: str,
    has_links: bool,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload, retention_status=retention_status)

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == response.json()["request_id"]
    data = response.json()["data"]
    expected_fields = {
        "artifact_id",
        "asset_version_id",
        "artifact_kind",
        "media_type",
        "size_bytes",
        "checksum_algorithm",
        "artifact_checksum",
        "producer_type",
        "producer_id",
        "run_id",
        "retention_status",
        "created_at",
    }
    if has_links:
        expected_fields |= {"content_url", "download_url"}
    assert set(data) == expected_fields
    assert data["retention_status"] == retention_status
    assert data["size_bytes"] == len(payload)
    assert data.get("content_url") == (
        f"/api/v1/artifacts/{artifact_id}/content" if has_links else None
    )
    assert data.get("download_url") == (
        f"/api/v1/artifacts/{artifact_id}/download" if has_links else None
    )
    assert {
        "storage_location_id",
        "storage_backend",
        "storage_domain",
        "storage_key",
        "path",
    }.isdisjoint(data)


def test_metadata_hides_cross_owner_missing_and_soft_deleted_parent(
    artifact_api: ArtifactApiFixture,
) -> None:
    payload = _wav_payload()
    cross_owner_id = artifact_api.ingest(payload, owner_id=uuid4())
    soft_deleted_id = artifact_api.ingest(payload)
    with artifact_api.session_factory() as session, session.begin():
        artifact = session.get(Artifact, soft_deleted_id)
        assert artifact is not None
        asset = session.get(Asset, artifact.asset_version.asset_id)
        assert asset is not None
        asset.deleted_at = datetime.now(UTC)

    for artifact_id in (cross_owner_id, soft_deleted_id, uuid4()):
        response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}")
        assert response.status_code == 404
        assert _error_code(response) == "ARTIFACT_NOT_FOUND"
        assert response.headers[REQUEST_ID_HEADER]


def test_artifact_routes_reject_public_owner_input(
    artifact_api: ArtifactApiFixture,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload())
    response = artifact_api.client.get(
        f"/api/v1/artifacts/{artifact_id}",
        params={"owner_id": str(artifact_api.owner_id)},
    )
    assert response.status_code == 422
    assert _error_code(response) == "INVALID_INPUT"


@pytest.mark.parametrize("suffix", ["/content", "/download"])
def test_full_delivery_streams_exact_payload_and_headers(
    artifact_api: ArtifactApiFixture,
    suffix: str,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}{suffix}")

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    if suffix == "/content":
        assert disposition == "inline"
    else:
        assert disposition == f'attachment; filename="audio-{artifact_id}.wav"'
    assert response.headers[REQUEST_ID_HEADER]


def test_download_uses_bin_for_unmapped_authoritative_media_type(
    artifact_api: ArtifactApiFixture,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload())
    artifact_api.update_artifact(
        artifact_id,
        media_type="application/octet-stream",
        artifact_kind='../../unsafe\r\n"kind',
    )

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}/download")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="unsafekind-{artifact_id}.bin"'
    )
    assert "\r" not in response.headers["content-disposition"]
    assert "\n" not in response.headers["content-disposition"]
    assert "/" not in response.headers["content-disposition"]
    assert "\\" not in response.headers["content-disposition"]


@pytest.mark.parametrize(
    "range_header,start,end",
    [
        ("bytes=0-0", 0, 0),
        ("bytes=0-9", 0, 9),
        ("bytes=10-", 10, None),
        ("bytes=-10", -10, None),
        ("bytes=0-999999", 0, None),
    ],
)
@pytest.mark.parametrize("suffix", ["/content", "/download"])
def test_single_range_returns_exact_206_body_and_headers(
    artifact_api: ArtifactApiFixture,
    suffix: str,
    range_header: str,
    start: int,
    end: int | None,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)
    normalized_start = len(payload) + start if start < 0 else start
    normalized_end = len(payload) - 1 if end is None else end

    response = artifact_api.client.get(
        f"/api/v1/artifacts/{artifact_id}{suffix}",
        headers={"Range": range_header},
    )

    assert response.status_code == 206
    assert response.content == payload[normalized_start : normalized_end + 1]
    assert response.headers["content-range"] == (
        f"bytes {normalized_start}-{normalized_end}/{len(payload)}"
    )
    assert response.headers["content-length"] == str(normalized_end - normalized_start + 1)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "audio/wav"
    if suffix == "/download":
        assert response.headers["content-disposition"].startswith("attachment;")


def test_last_byte_and_full_length_range_are_partial(
    artifact_api: ArtifactApiFixture,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)
    path = f"/api/v1/artifacts/{artifact_id}/content"

    last = artifact_api.client.get(
        path,
        headers={"Range": f"bytes={len(payload) - 1}-{len(payload) - 1}"},
    )
    full = artifact_api.client.get(
        path,
        headers={"Range": f"bytes=0-{len(payload) - 1}"},
    )

    assert last.status_code == 206
    assert last.content == payload[-1:]
    assert full.status_code == 206
    assert full.content == payload


@pytest.mark.parametrize(
    "range_header",
    [
        "bytes=",
        "items=0-1",
        "bytes=a-b",
        "bytes=20-10",
        "bytes=0-1,4-5",
        "bytes=-0",
        "bytes= 0-1",
        " bytes=0-1",
        "bytes=0-1 ",
        "bytes=999999999999999999999999999999999999-",
    ],
)
def test_invalid_ranges_return_enveloped_416(
    artifact_api: ArtifactApiFixture,
    range_header: str,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)

    response = artifact_api.client.get(
        f"/api/v1/artifacts/{artifact_id}/content",
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert _error_code(response) == "INVALID_RANGE"
    assert response.headers["content-range"] == f"bytes */{len(payload)}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers[REQUEST_ID_HEADER] == response.json()["error"]["request_id"]


@pytest.mark.parametrize(
    "retention_status,error_code,status_code",
    [
        ("quarantined", "ARTIFACT_QUARANTINED", 409),
        ("expired", "ARTIFACT_GONE", 410),
        ("pending_delete", "ARTIFACT_GONE", 410),
        ("deleted", "ARTIFACT_GONE", 410),
        ("unknown", "ARTIFACT_CONTENT_UNAVAILABLE", 409),
    ],
)
def test_content_retention_error_mapping(
    artifact_api: ArtifactApiFixture,
    retention_status: str,
    error_code: str,
    status_code: int,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload())
    artifact_api.update_artifact(artifact_id, retention_status=retention_status)

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}/content")

    assert response.status_code == status_code
    assert _error_code(response) == error_code
    assert response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize("drift", ["missing", "size", "checksum"])
def test_integrity_gate_blocks_delivery_without_internal_leak(
    artifact_api: ArtifactApiFixture,
    drift: str,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)
    path = artifact_api.payload_path(artifact_id)
    if drift == "missing":
        path.unlink()
        expected_code, expected_status = "ARTIFACT_CONTENT_UNAVAILABLE", 409
    elif drift == "size":
        artifact_api.update_artifact(artifact_id, size_bytes=len(payload) + 1)
        expected_code, expected_status = "ARTIFACT_INTEGRITY_ERROR", 500
    else:
        artifact_api.update_artifact(artifact_id, artifact_checksum="0" * 64)
        expected_code, expected_status = "ARTIFACT_INTEGRITY_ERROR", 500

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}/content")

    assert response.status_code == expected_status
    assert _error_code(response) == expected_code
    body = response.text
    assert str(path) not in body
    assert str(artifact_api.root) not in body
    assert "SELECT " not in body


def test_integrity_gate_runs_before_invalid_range(
    artifact_api: ArtifactApiFixture,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload())
    artifact_api.update_artifact(artifact_id, artifact_checksum="0" * 64)

    response = artifact_api.client.get(
        f"/api/v1/artifacts/{artifact_id}/content",
        headers={"Range": "bytes=invalid"},
    )

    assert response.status_code == 500
    assert _error_code(response) == "ARTIFACT_INTEGRITY_ERROR"


def test_cross_owner_is_rejected_before_missing_payload_lookup(
    artifact_api: ArtifactApiFixture,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload(), owner_id=uuid4())
    artifact_api.payload_path(artifact_id).unlink()

    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}/content")

    assert response.status_code == 404
    assert _error_code(response) == "ARTIFACT_NOT_FOUND"


def test_stream_descriptor_is_closed_after_repeated_responses(
    artifact_api: ArtifactApiFixture,
) -> None:
    payload = _wav_payload()
    artifact_id = artifact_api.ingest(payload)
    path = artifact_api.payload_path(artifact_id)
    for _ in range(3):
        response = artifact_api.client.get(
            f"/api/v1/artifacts/{artifact_id}/content",
            headers={"Range": "bytes=0-0"},
        )
        assert response.status_code == 206
    moved = path.with_suffix(".moved")
    path.replace(moved)
    moved.replace(path)
    assert path.read_bytes() == payload


def test_range_parser_contract() -> None:
    assert parse_single_byte_range(None, size_bytes=10) is None
    assert parse_single_byte_range("bytes=0-0", size_bytes=10) == ByteRange(0, 0)
    assert parse_single_byte_range("bytes=3-", size_bytes=10) == ByteRange(3, 9)
    assert parse_single_byte_range("bytes=-20", size_bytes=10) == ByteRange(0, 9)
    assert parse_single_byte_range("bytes=3-99", size_bytes=10) == ByteRange(3, 9)
    assert ByteRange(3, 9).length == 7
    with pytest.raises(RangeNotSatisfiable):
        parse_single_byte_range("bytes=0-0", size_bytes=0)


def test_openapi_registers_three_unique_artifact_operations(
    artifact_api: ArtifactApiFixture,
) -> None:
    schema = artifact_api.client.get("/openapi.json").json()
    paths = schema["paths"]
    expected = {
        "/api/v1/artifacts/{artifact_id}": "get_artifact",
        "/api/v1/artifacts/{artifact_id}/content": "get_artifact_content",
        "/api/v1/artifacts/{artifact_id}/download": "download_artifact",
    }
    for path, operation_id in expected.items():
        operation = paths[path]["get"]
        assert operation["operationId"] == operation_id
        assert not {"post", "patch", "delete", "head"}.intersection(paths[path])
    content_operation = paths["/api/v1/artifacts/{artifact_id}/content"]["get"]
    assert any(
        parameter["name"] == "Range" and parameter["in"] == "header"
        for parameter in content_operation["parameters"]
    )
    operation_ids = [
        operation["operationId"]
        for methods in paths.values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_artifact_api_does_not_mutate_schema_or_resource_rows(
    artifact_api: ArtifactApiFixture,
) -> None:
    artifact_id = artifact_api.ingest(_wav_payload())
    with artifact_api.session_factory() as session:
        before = _row_counts(session)
    response = artifact_api.client.get(f"/api/v1/artifacts/{artifact_id}/content")
    with artifact_api.session_factory() as session:
        after = _row_counts(session)
    assert response.status_code == 200
    assert before == after


def _row_counts(session: Session) -> tuple[int, int, int]:
    return (
        session.query(Asset).count(),
        session.query(AssetVersion).count(),
        session.query(Artifact).count(),
    )
