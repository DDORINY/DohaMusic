"""CompositionSnapshot 공식 Resource API transport 계약 검증."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api.v1.dependencies import REQUEST_ID_HEADER
from backend.core.exceptions import ResourceConflictError
from backend.models.idempotency_record import IdempotencyRecord
from backend.models.workspace import (
    Asset,
    AssetType,
    AssetVersion,
    CompositionSnapshot,
    ProjectAsset,
    SnapshotItem,
)
from backend.services.workspace import (
    AssetService,
    CompositionService,
    SnapshotItemInput,
    WorkspaceService,
)


@dataclass(frozen=True)
class Graph:
    owner_id: UUID
    workspace_id: UUID
    project_id: UUID
    asset_id: UUID
    asset_version_id: UUID


def _workspace_service(client: TestClient) -> WorkspaceService:
    service = client.app.state.workspace_service
    assert isinstance(service, WorkspaceService)
    return service


def _asset_service(client: TestClient) -> AssetService:
    service = client.app.state.asset_service
    assert isinstance(service, AssetService)
    return service


def _composition_service(client: TestClient) -> CompositionService:
    service = client.app.state.composition_service
    assert isinstance(service, CompositionService)
    return service


def _seed_graph(
    client: TestClient,
    *,
    owner_id: UUID | None = None,
    workspace_id: UUID | None = None,
    global_asset: bool = False,
    attach: bool = True,
    provider_id: str | None = None,
    model_manifest_id: str | None = None,
) -> Graph:
    owner = owner_id or uuid4()
    workspace_service = _workspace_service(client)
    if workspace_id is None:
        workspace = workspace_service.create_workspace(
            owner_id=owner,
            name=f"Snapshot 작업실-{uuid4()}",
        )
    else:
        workspace = workspace_service.get_workspace(workspace_id)
    project = workspace_service.create_project(
        workspace_id=workspace.workspace_id,
        title=f"Snapshot 곡-{uuid4()}",
        created_by=owner,
    )
    asset = _asset_service(client).create_asset(
        owner_id=owner,
        workspace_id=None if global_asset else workspace.workspace_id,
        asset_type=AssetType.MUSIC,
    )
    version = _asset_service(client).create_asset_version(
        asset_id=asset.asset_id,
        version_origin="user_created",
        settings_snapshot={},
        created_by=owner,
        provider_id=provider_id,
        model_manifest_id=model_manifest_id,
    )
    if attach:
        workspace_service.attach_asset(
            project_id=project.project_id,
            asset_id=asset.asset_id,
            role="music",
            display_order=0,
        )
    return Graph(
        owner,
        workspace.workspace_id,
        project.project_id,
        asset.asset_id,
        version.asset_version_id,
    )


def _body(graph: Graph, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project_id": str(graph.project_id),
        "items": [
            {
                "asset_version_id": str(graph.asset_version_id),
                "item_role": "music",
                "sort_order": 0,
            }
        ],
        "mix_settings_snapshot": {},
        "provider_versions": {},
        "model_manifest_ids": {},
    }
    body.update(changes)
    return body


def _post(
    client: TestClient,
    graph: Graph,
    *,
    key: str = "snapshot-api-request",
    body: dict[str, object] | None = None,
):
    return client.post(
        "/api/v1/snapshots",
        json=body or _body(graph),
        headers={"Idempotency-Key": key},
    )


def _count(client: TestClient, entity_type: type[object]) -> int:
    with client.app.state.session_factory() as session:
        return session.scalar(select(func.count()).select_from(entity_type)) or 0


def test_snapshot_endpoints_require_bootstrap(client: TestClient) -> None:
    snapshot_id = uuid4()
    project_id = uuid4()
    for method, path, body, headers in (
        ("GET", f"/api/v1/snapshots?project_id={project_id}", None, None),
        (
            "POST",
            "/api/v1/snapshots",
            {
                "project_id": str(project_id),
                "items": [
                    {
                        "asset_version_id": str(uuid4()),
                        "item_role": "music",
                        "sort_order": 0,
                    }
                ],
            },
            {"Idempotency-Key": "bootstrap-required"},
        ),
        ("GET", f"/api/v1/snapshots/{snapshot_id}", None, None),
    ):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["error_code"] == ("WORKSPACE_BOOTSTRAP_REQUIRED")
        assert response.headers[REQUEST_ID_HEADER]


def test_snapshot_create_returns_immutable_aggregate_and_request_id(
    client: TestClient,
) -> None:
    graph = _seed_graph(client)
    response = _post(client, graph, key="snapshot-create")

    assert response.status_code == 201
    assert response.headers[REQUEST_ID_HEADER] == response.json()["request_id"]
    data = response.json()["data"]
    assert set(data) == {
        "composition_snapshot_id",
        "project_id",
        "snapshot_version",
        "processing_chain_id",
        "mix_settings_snapshot",
        "provider_versions",
        "model_manifest_ids",
        "created_at",
        "items",
    }
    assert data["project_id"] == str(graph.project_id)
    assert data["snapshot_version"] == 1
    assert set(data["items"][0]) == {
        "snapshot_item_id",
        "asset_version_id",
        "item_role",
        "sort_order",
        "created_at",
    }
    assert data["items"][0]["asset_version_id"] == str(graph.asset_version_id)
    assert "created_by" not in data and "owner_id" not in data
    assert _count(client, CompositionSnapshot) == 1
    assert _count(client, SnapshotItem) == 1
    assert _count(client, IdempotencyRecord) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_version", 1),
        ("created_by", lambda: str(uuid4())),
        ("owner_id", lambda: str(uuid4())),
        ("composition_snapshot_id", lambda: str(uuid4())),
        ("snapshot_item_id", lambda: str(uuid4())),
    ],
)
def test_snapshot_create_rejects_internal_fields(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    graph = _seed_graph(client)
    resolved_value = value() if callable(value) else value
    body = _body(graph)
    if field == "snapshot_item_id":
        body["items"][0][field] = resolved_value  # type: ignore[index]
    else:
        body[field] = resolved_value
    response = _post(client, graph, key=f"forbidden-{field}", body=body)
    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "INVALID_INPUT"
    assert _count(client, CompositionSnapshot) == 0


def test_snapshot_list_uses_project_cursor_and_descending_versions(
    client: TestClient,
) -> None:
    graph = _seed_graph(client)
    for index in range(5):
        assert _post(client, graph, key=f"page-{index}").status_code == 201

    collected: list[dict[str, object]] = []
    path = f"/api/v1/snapshots?project_id={graph.project_id}&limit=2"
    page_count = 0
    while path:
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        collected.extend(body["data"])
        assert body["pagination"]["limit"] == 2
        assert (body["pagination"]["next_cursor"] is not None) == body["pagination"]["has_more"]
        path = body["links"]["next"]
        page_count += 1

    assert page_count == 3
    assert [item["snapshot_version"] for item in collected] == [5, 4, 3, 2, 1]
    assert len({item["composition_snapshot_id"] for item in collected}) == 5
    assert all(
        set(item) == {"composition_snapshot_id", "project_id", "snapshot_version", "created_at"}
        for item in collected
    )


def test_snapshot_list_empty_last_page_and_cursor_errors(client: TestClient) -> None:
    graph = _seed_graph(client)
    empty = client.get("/api/v1/snapshots", params={"project_id": str(graph.project_id)})
    assert empty.status_code == 200
    assert empty.json()["data"] == []
    assert empty.json()["pagination"]["has_more"] is False
    assert empty.json()["pagination"]["next_cursor"] is None

    for index in range(3):
        _post(client, graph, key=f"cursor-{index}")
    first = client.get(
        "/api/v1/snapshots",
        params={"project_id": str(graph.project_id), "limit": 2},
    )
    cursor = first.json()["pagination"]["next_cursor"]
    other = _seed_graph(
        client,
        owner_id=graph.owner_id,
        workspace_id=graph.workspace_id,
    )
    wrong_project = client.get(
        "/api/v1/snapshots",
        params={"project_id": str(other.project_id), "limit": 2, "cursor": cursor},
    )
    tampered = client.get(
        "/api/v1/snapshots",
        params={"project_id": str(graph.project_id), "limit": 2, "cursor": "bad"},
    )
    invalid_limit = client.get(
        "/api/v1/snapshots",
        params={"project_id": str(graph.project_id), "limit": 0},
    )
    assert wrong_project.json()["error"]["error_code"] == "INVALID_CURSOR"
    assert tampered.json()["error"]["error_code"] == "INVALID_CURSOR"
    assert invalid_limit.json()["error"]["error_code"] == "INVALID_LIMIT"


def test_snapshot_detail_returns_sorted_exact_versions(client: TestClient) -> None:
    graph = _seed_graph(client)
    second_version = _asset_service(client).create_asset_version(
        asset_id=graph.asset_id,
        version_origin="user_edited",
        settings_snapshot={},
        created_by=graph.owner_id,
    )
    response = _post(
        client,
        graph,
        key="aggregate-detail",
        body=_body(
            graph,
            items=[
                {
                    "asset_version_id": str(second_version.asset_version_id),
                    "item_role": "vocal",
                    "sort_order": 2,
                },
                {
                    "asset_version_id": str(graph.asset_version_id),
                    "item_role": "music",
                    "sort_order": 1,
                },
            ],
        ),
    )
    snapshot_id = response.json()["data"]["composition_snapshot_id"]
    detail = client.get(
        f"/api/v1/snapshots/{snapshot_id}",
        headers={REQUEST_ID_HEADER: "snapshot-detail-request"},
    )

    assert detail.status_code == 200
    assert detail.headers[REQUEST_ID_HEADER] == "snapshot-detail-request"
    items = detail.json()["data"]["items"]
    assert [(item["item_role"], item["sort_order"]) for item in items] == [
        ("music", 1),
        ("vocal", 2),
    ]
    assert {item["asset_version_id"] for item in items} == {
        str(graph.asset_version_id),
        str(second_version.asset_version_id),
    }


def test_snapshot_idempotency_replays_without_new_rows(client: TestClient) -> None:
    graph = _seed_graph(client)
    first = _post(client, graph, key="replay-key")
    replay = _post(client, graph, key="replay-key")

    assert first.status_code == replay.status_code == 201
    assert first.json()["data"] == replay.json()["data"]
    assert _count(client, CompositionSnapshot) == 1
    assert _count(client, SnapshotItem) == 1
    assert _count(client, IdempotencyRecord) == 1

    conflict = _post(
        client,
        graph,
        key="replay-key",
        body=_body(graph, mix_settings_snapshot={"gain": -1}),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_code"] == "IDEMPOTENCY_KEY_REUSED"


def test_snapshot_idempotency_scope_is_project_specific(client: TestClient) -> None:
    first = _seed_graph(client)
    second = _seed_graph(
        client,
        owner_id=first.owner_id,
        workspace_id=first.workspace_id,
    )
    created_a = _post(client, first, key="shared-project-key")
    created_b = _post(client, second, key="shared-project-key")
    assert created_a.status_code == created_b.status_code == 201
    assert (
        created_a.json()["data"]["composition_snapshot_id"]
        != created_b.json()["data"]["composition_snapshot_id"]
    )


def test_snapshot_global_asset_allowed_and_other_workspace_rejected(
    client: TestClient,
) -> None:
    global_graph = _seed_graph(client, global_asset=True)
    assert _post(client, global_graph, key="global-asset").status_code == 201

    project_graph = _seed_graph(client, owner_id=global_graph.owner_id)
    other_workspace = _workspace_service(client).create_workspace(
        owner_id=global_graph.owner_id,
        name="다른 Workspace",
    )
    other_asset = _asset_service(client).create_asset(
        owner_id=global_graph.owner_id,
        workspace_id=other_workspace.workspace_id,
        asset_type=AssetType.MUSIC,
    )
    other_version = _asset_service(client).create_asset_version(
        asset_id=other_asset.asset_id,
        version_origin="user_created",
        settings_snapshot={},
        created_by=global_graph.owner_id,
    )
    with client.app.state.session_factory.begin() as session:
        session.add(
            ProjectAsset(
                project_id=project_graph.project_id,
                asset_id=other_asset.asset_id,
                role="music",
                display_order=1,
            )
        )
    rejected = _post(
        client,
        project_graph,
        key="cross-workspace",
        body=_body(
            project_graph,
            items=[
                {
                    "asset_version_id": str(other_version.asset_version_id),
                    "item_role": "music",
                    "sort_order": 0,
                }
            ],
        ),
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["error_code"] == "ASSET_VERSION_NOT_FOUND"


def test_snapshot_requires_active_project_asset_and_preserves_detached_history(
    client: TestClient,
) -> None:
    detached = _seed_graph(client, attach=False)
    rejected = _post(client, detached, key="missing-membership")
    assert rejected.status_code == 404
    assert rejected.json()["error"]["error_code"] == "PROJECT_ASSET_NOT_FOUND"

    graph = _seed_graph(client, owner_id=detached.owner_id)
    created = _post(client, graph, key="detach-after-create")
    _workspace_service(client).detach_asset(
        project_id=graph.project_id,
        asset_id=graph.asset_id,
    )
    detail = client.get(f"/api/v1/snapshots/{created.json()['data']['composition_snapshot_id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["items"][0]["asset_version_id"] == str(graph.asset_version_id)


def test_snapshot_missing_asset_version_is_resource_specific(
    client: TestClient,
) -> None:
    graph = _seed_graph(client)
    response = _post(
        client,
        graph,
        key="missing-version",
        body=_body(
            graph,
            items=[
                {
                    "asset_version_id": str(uuid4()),
                    "item_role": "music",
                    "sort_order": 0,
                }
            ],
        ),
    )
    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "ASSET_VERSION_NOT_FOUND"


@pytest.mark.parametrize(
    "item",
    [
        {"item_role": "instrumental", "sort_order": 0},
        {"item_role": "music", "sort_order": -1},
        {"item_role": "music", "sort_order": True},
        {"item_role": "music", "sort_order": 1.5},
    ],
)
def test_snapshot_rejects_invalid_item_contract(
    client: TestClient, item: dict[str, object]
) -> None:
    graph = _seed_graph(client)
    item["asset_version_id"] = str(graph.asset_version_id)
    response = _post(
        client,
        graph,
        key=f"invalid-item-{uuid4()}",
        body=_body(graph, items=[item]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "INVALID_INPUT"


@pytest.mark.parametrize("duplicate_kind", ["role_order", "version_role"])
def test_snapshot_rejects_duplicate_items(client: TestClient, duplicate_kind: str) -> None:
    graph = _seed_graph(client)
    second_version = _asset_service(client).create_asset_version(
        asset_id=graph.asset_id,
        version_origin="user_edited",
        settings_snapshot={},
        created_by=graph.owner_id,
    )
    items = [
        {
            "asset_version_id": str(graph.asset_version_id),
            "item_role": "music",
            "sort_order": 0,
        },
        {
            "asset_version_id": str(
                second_version.asset_version_id
                if duplicate_kind == "role_order"
                else graph.asset_version_id
            ),
            "item_role": "music",
            "sort_order": 0 if duplicate_kind == "role_order" else 1,
        },
    ]
    response = _post(
        client,
        graph,
        key=f"duplicate-{duplicate_kind}",
        body=_body(graph, items=items),
    )
    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == ("COMPOSITION_SNAPSHOT_CONFLICT")


def test_snapshot_validates_processing_and_bounded_lineage(client: TestClient) -> None:
    graph = _seed_graph(
        client,
        provider_id="audio",
        model_manifest_id="audio-manifest-1",
    )
    valid = _post(
        client,
        graph,
        key="bounded-lineage",
        body=_body(
            graph,
            mix_settings_snapshot={"gain": -1},
            provider_versions={"audio": "1.0"},
            model_manifest_ids={"audio": "audio-manifest-1"},
        ),
    )
    assert valid.status_code == 201
    assert valid.json()["data"]["provider_versions"] == {"audio": "1.0"}

    missing_chain = _post(
        client,
        graph,
        key="missing-chain",
        body=_body(
            graph,
            processing_chain_id=str(uuid4()),
            provider_versions={"audio": "1.0"},
            model_manifest_ids={"audio": "audio-manifest-1"},
        ),
    )
    too_deep = _post(
        client,
        graph,
        key="too-deep",
        body=_body(
            graph,
            mix_settings_snapshot={"a": {"b": {"c": {"d": {"e": 1}}}}},
            provider_versions={"audio": "1.0"},
            model_manifest_ids={"audio": "audio-manifest-1"},
        ),
    )
    assert missing_chain.status_code == 422
    assert missing_chain.json()["error"]["error_code"] == "INVALID_INPUT"
    assert too_deep.status_code == 422
    assert too_deep.json()["error"]["error_code"] == "INVALID_INPUT"


def test_snapshot_not_found_and_cross_owner_are_private(client: TestClient) -> None:
    foreign = _seed_graph(client)
    foreign_created = _composition_service(client).create_snapshot(
        project_id=foreign.project_id,
        effective_owner_id=foreign.owner_id,
        items=[SnapshotItemInput(foreign.asset_version_id, "music", 0)],
        mix_settings_snapshot={},
        provider_versions={},
        model_manifest_ids={},
        idempotency_key="foreign-owner-snapshot",
    )
    _seed_graph(client)

    missing = client.get(f"/api/v1/snapshots/{uuid4()}")
    foreign_project = client.get(
        "/api/v1/snapshots", params={"project_id": str(foreign.project_id)}
    )
    foreign_snapshot = client.get(
        f"/api/v1/snapshots/{foreign_created.aggregate.snapshot.composition_snapshot_id}"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["error_code"] == ("COMPOSITION_SNAPSHOT_NOT_FOUND")
    assert foreign_project.status_code == 404
    assert foreign_project.json()["error"]["error_code"] == "PROJECT_NOT_FOUND"
    assert foreign_snapshot.status_code == 404
    assert foreign_snapshot.json()["error"]["error_code"] == "PROJECT_NOT_FOUND"


def test_snapshot_requires_idempotency_key_and_hides_internal_details(
    client: TestClient,
) -> None:
    graph = _seed_graph(client)
    response = client.post("/api/v1/snapshots", json=_body(graph))
    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "INVALID_INPUT"
    assert "fingerprint" not in str(response.json())
    assert "snapshot-api-request" not in str(response.json())
    assert _count(client, CompositionSnapshot) == 0


def test_snapshot_conflict_mapping_hides_database_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _seed_graph(client)

    def fail(**_: object):
        raise ResourceConflictError("CompositionSnapshot")

    monkeypatch.setattr(_composition_service(client), "create_snapshot", fail)
    response = _post(client, graph, key="forced-conflict")
    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == ("COMPOSITION_SNAPSHOT_CONFLICT")
    assert "SQL" not in str(response.json())


def test_snapshot_routes_are_immutable_and_openapi_is_exact(client: TestClient) -> None:
    schema = client.app.openapi()
    snapshot_paths = {
        path: {
            method.upper()
            for method, operation in path_item.items()
            if isinstance(operation, dict) and "operationId" in operation
        }
        for path, path_item in schema["paths"].items()
        if path.startswith("/api/v1/snapshots")
    }
    assert snapshot_paths == {
        "/api/v1/snapshots": {"GET", "POST"},
        "/api/v1/snapshots/{composition_snapshot_id}": {"GET"},
    }
    assert not any("snapshot-items" in path for path in schema["paths"])

    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    duplicates = {
        operation_id for operation_id, count in Counter(operation_ids).items() if count > 1
    }
    loop_restore_path = (
        "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/loop/restore"
    )
    assert schema["paths"][loop_restore_path]["post"]["operationId"] == (
        "restore_working_composition_clip_loop_state"
    )
    assert len(schema["paths"]) == 84
    assert len(operation_ids) == 105
    assert duplicates == set()
    assert {
        "list_composition_snapshots",
        "create_composition_snapshot",
        "get_composition_snapshot",
    }.isdisjoint(duplicates)


def test_snapshot_creation_does_not_create_or_select_artifacts(
    client: TestClient,
) -> None:
    graph = _seed_graph(client)
    before_assets = _count(client, Asset)
    before_versions = _count(client, AssetVersion)
    assert _post(client, graph, key="artifact-boundary").status_code == 201
    assert _count(client, Asset) == before_assets
    assert _count(client, AssetVersion) == before_versions
