"""Workspace REST API v1 공통 기반과 기존 Runtime 호환성 검증."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.routing import BaseRoute

from backend.api.exception_handlers import register_exception_handlers
from backend.api.v1.dependencies import (
    REQUEST_ID_HEADER,
    get_request_id,
    register_request_id_middleware,
)
from backend.api.v1.responses import success_response
from backend.api.v1.router import router as workspace_v1_router
from backend.app.factory import create_app
from backend.core.exceptions import ResourceNotFoundError
from backend.schemas.workspace import CollectionResponse


class InputPayload(BaseModel):
    name: str = Field(min_length=2)


def _flatten_registered_routes(routes: Iterable[BaseRoute]) -> list[BaseRoute]:
    """Normalize expanded and nested FastAPI router representations."""

    registered_routes: list[BaseRoute] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is None:
            registered_routes.append(route)
            continue
        registered_routes.extend(_flatten_registered_routes(original_router.routes))
    return registered_routes


def _test_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/v1/_test/success")
    def v1_success(request: Request) -> dict[str, object]:
        return success_response(data={"status": "ok"}, request_id=get_request_id(request))

    @app.get("/api/v1/_test/not-found")
    def v1_not_found() -> None:
        raise ResourceNotFoundError("Workspace")

    @app.post("/api/v1/_test/validation")
    def v1_validation(_payload: InputPayload) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/_test/internal")
    def v1_internal() -> None:
        raise RuntimeError("C:/private/path should never be returned")

    @app.get("/api/_test/not-found")
    def runtime_not_found() -> None:
        raise ResourceNotFoundError("Runtime")

    @app.post("/api/_test/validation")
    def runtime_validation(_payload: InputPayload) -> dict[str, bool]:
        return {"ok": True}

    register_request_id_middleware(app)
    return app


def test_v1_success_envelope_generates_request_id() -> None:
    response = TestClient(_test_app()).get("/api/v1/_test/success")

    assert response.status_code == 200
    request_id = response.json()["request_id"]
    assert UUID(request_id)
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert response.json()["data"] == {"status": "ok"}


def test_valid_request_id_is_reused_and_invalid_value_is_replaced() -> None:
    client = TestClient(_test_app())
    accepted = "client-request-1234"

    response = client.get(
        "/api/v1/_test/success",
        headers={REQUEST_ID_HEADER: accepted},
    )
    assert response.json()["request_id"] == accepted

    rejected = client.get(
        "/api/v1/_test/success",
        headers={REQUEST_ID_HEADER: "short"},
    )
    assert rejected.json()["request_id"] != "short"
    assert UUID(rejected.json()["request_id"])


def test_v1_app_error_uses_new_contract() -> None:
    response = TestClient(_test_app()).get("/api/v1/_test/not-found")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["error_code"] == "RESOURCE_NOT_FOUND"
    assert error["details"] == []
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert "code" not in error


def test_v1_validation_error_has_safe_details() -> None:
    response = TestClient(_test_app()).post(
        "/api/v1/_test/validation",
        json={"name": "x"},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["error_code"] == "INVALID_INPUT"
    assert error["details"][0]["location"] == ["body", "name"]
    assert "input" not in error["details"][0]


def test_v1_internal_error_does_not_expose_exception_or_path() -> None:
    response = TestClient(_test_app(), raise_server_exceptions=False).get("/api/v1/_test/internal")

    assert response.status_code == 500
    body = response.text
    assert "private" not in body
    assert "RuntimeError" not in body
    assert response.json()["error"]["details"] == []


def test_unregistered_v1_path_uses_v1_error_contract() -> None:
    response = TestClient(_test_app()).get("/api/v1/not-registered")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["error_code"] == "RESOURCE_NOT_FOUND"
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_runtime_error_payload_remains_unchanged() -> None:
    response = TestClient(_test_app()).get("/api/_test/not-found")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "RESOURCE_NOT_FOUND",
            "message": "Runtime을(를) 찾을 수 없습니다.",
        }
    }


def test_runtime_validation_payload_remains_unchanged() -> None:
    response = TestClient(_test_app()).post(
        "/api/_test/validation",
        json={"name": "x"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_INPUT",
            "message": "요청 입력값이 유효하지 않습니다.",
        }
    }


def test_unregistered_runtime_path_keeps_fastapi_default_payload() -> None:
    response = TestClient(_test_app()).get("/api/not-registered")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_collection_schema_matches_documented_contract() -> None:
    result = CollectionResponse[dict[str, str]](
        data=[{"asset_id": "opaque"}],
        pagination={"limit": 20, "next_cursor": None, "has_more": False},
        links={"self": "/api/v1/assets?limit=20", "next": None},
        request_id="request-12345678",
    )

    assert result.pagination.limit == 20
    assert result.links.next is None


def test_v1_router_adds_first_resources_and_runtime_route_count_is_stable() -> None:
    app = create_app()
    registered_routes = _flatten_registered_routes(app.routes)
    api_routes = [route for route in registered_routes if isinstance(route, APIRoute)]
    openapi_paths = app.openapi()["paths"]
    operation_ids = [
        operation["operationId"]
        for path_item in openapi_paths.values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    duplicate_ids = {
        operation_id for operation_id, count in Counter(operation_ids).items() if count > 1
    }

    assert len(registered_routes) == 107
    assert len(api_routes) == 103
    assert len(openapi_paths) == 82
    assert len(operation_ids) == 103
    assert (
        len(
            [
                path
                for path in openapi_paths
                if path.startswith("/api/") and not path.startswith("/api/v1")
            ]
        )
        == 33
    )
    assert "/health" in openapi_paths
    assert len(_flatten_registered_routes(workspace_v1_router.routes)) == 60
    assert len([path for path in openapi_paths if path.startswith("/api/v1")]) == 48
    v1_operations = {
        (method.upper(), path): operation
        for path, path_item in openapi_paths.items()
        if path.startswith("/api/v1")
        for method, operation in path_item.items()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert set(v1_operations) == {
        ("GET", "/api/v1/workspaces"),
        ("GET", "/api/v1/workspaces/{workspace_id}"),
        ("PATCH", "/api/v1/workspaces/{workspace_id}"),
        ("GET", "/api/v1/projects"),
        ("POST", "/api/v1/projects"),
        ("GET", "/api/v1/projects/{project_id}"),
        ("PATCH", "/api/v1/projects/{project_id}"),
        ("DELETE", "/api/v1/projects/{project_id}"),
        ("GET", "/api/v1/projects/{project_id}/assets"),
        ("POST", "/api/v1/projects/{project_id}/assets"),
        ("DELETE", "/api/v1/projects/{project_id}/assets/{asset_id}"),
        ("GET", "/api/v1/projects/{project_id}/composition"),
        ("PATCH", "/api/v1/projects/{project_id}/composition-selection"),
        (
            "GET",
            "/api/v1/projects/{project_id}/asset-versions/{asset_version_id}/media-source",
        ),
        ("GET", "/api/v1/assets"),
        ("POST", "/api/v1/assets"),
        ("GET", "/api/v1/assets/{asset_id}"),
        ("PATCH", "/api/v1/assets/{asset_id}"),
        ("DELETE", "/api/v1/assets/{asset_id}"),
        ("GET", "/api/v1/assets/{asset_id}/versions"),
        ("POST", "/api/v1/assets/{asset_id}/versions"),
        ("GET", "/api/v1/assets/{asset_id}/versions/{asset_version_id}"),
        ("GET", "/api/v1/artifacts/{artifact_id}"),
        ("GET", "/api/v1/artifacts/{artifact_id}/content"),
        ("GET", "/api/v1/artifacts/{artifact_id}/download"),
        ("GET", "/api/v1/snapshots"),
        ("POST", "/api/v1/snapshots"),
        ("GET", "/api/v1/snapshots/{composition_snapshot_id}"),
        ("GET", "/api/v1/jobs"),
        ("POST", "/api/v1/jobs"),
        ("GET", "/api/v1/jobs/{job_id}"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/jobs/{job_id}/retry"),
        ("GET", "/api/v1/projects/{project_id}/working-composition"),
        ("GET", "/api/v1/projects/{project_id}/working-composition/history"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/history/undo"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/history/redo"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/initialize"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/checkout"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/commit"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/preview"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/tracks"),
        ("PATCH", "/api/v1/projects/{project_id}/working-composition/tracks/reorder"),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/tracks/{track_id}",
        ),
        (
            "DELETE",
            "/api/v1/projects/{project_id}/working-composition/tracks/{track_id}",
        ),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/tracks/{track_id}/restore",
        ),
        ("POST", "/api/v1/projects/{project_id}/working-composition/clips"),
        ("POST", "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/copy"),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/move",
        ),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/gain",
        ),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/fade",
        ),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/loop",
        ),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/loop/restore",
        ),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/trim-start",
        ),
        (
            "PATCH",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/trim-end",
        ),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/split",
        ),
        ("DELETE", "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}"),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/restore",
        ),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/clips/{original_clip_id}/unsplit",
        ),
        (
            "POST",
            "/api/v1/projects/{project_id}/working-composition/clips/{original_clip_id}/resplit",
        ),
    }
    assert len({item["operationId"] for item in v1_operations.values()}) == 60
    assert all(item.get("summary") for item in v1_operations.values())
    assert all(item.get("tags") for item in v1_operations.values())
    assert duplicate_ids == set()
