"""Fail-closed contract for the complete public FastAPI/OpenAPI surface."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

import pytest
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from backend.app.factory import create_app

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
EXPECTED_SURFACE_FINGERPRINT = "81b398c306b03a75d8a8b82469712c10668740c180ee27ed9687e76b7a24ffe4"
CRITICAL_OPERATIONS = {
    ("GET", "/api/v1/workspaces"),
    ("GET", "/api/v1/projects"),
    ("POST", "/api/v1/projects"),
    ("GET", "/api/v1/assets"),
    ("GET", "/api/v1/assets/{asset_id}/versions/{asset_version_id}"),
    ("GET", "/api/v1/artifacts/{artifact_id}/content"),
    ("GET", "/api/v1/snapshots/{composition_snapshot_id}"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/projects/{project_id}/working-composition"),
    ("GET", "/api/v1/projects/{project_id}/working-composition/history"),
    ("POST", "/api/v1/projects/{project_id}/working-composition/history/undo"),
    ("POST", "/api/v1/projects/{project_id}/working-composition/history/redo"),
    ("POST", "/api/v1/projects/{project_id}/working-composition/preview"),
    ("POST", "/api/v1/projects/{project_id}/working-composition/commit"),
    ("PATCH", "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/gain"),
    ("PATCH", "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/fade"),
    ("PATCH", "/api/v1/projects/{project_id}/working-composition/clips/{clip_id}/loop"),
    ("PATCH", "/api/v1/projects/{project_id}/working-composition/tracks/{track_id}/mixer"),
    ("PATCH", "/api/v1/projects/{project_id}/working-composition/master-gain"),
    ("GET", "/api/pipelines/{job_id}/files/{file_id}/content"),
    ("HEAD", "/api/pipelines/{job_id}/files/{file_id}/content"),
    ("POST", "/api/voice-enrollments"),
    ("POST", "/api/generations"),
    ("POST", "/api/lyrics"),
    ("GET", "/api/history"),
}


def _flatten(routes: Iterable[BaseRoute]) -> list[BaseRoute]:
    flattened: list[BaseRoute] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is None:
            flattened.append(route)
        else:
            flattened.extend(_flatten(original_router.routes))
    return flattened


def _operations(schema: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    ]


def _schema_at_ref(schema: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise AssertionError(f"external OpenAPI reference is not allowed: {reference}")
    target: Any = schema
    for token in reference[2:].split("/"):
        target = target[token.replace("~1", "/").replace("~0", "~")]
    return target


def _assert_integrity(schema: Mapping[str, Any]) -> None:
    operations = _operations(schema)
    operation_ids = [operation.get("operationId") for _, _, operation in operations]
    assert all(operation_ids)
    assert len(operation_ids) == len(set(operation_ids))
    method_paths = [(method, path) for method, path, _ in operations]
    assert len(method_paths) == len(set(method_paths))

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if "$ref" in value:
                _schema_at_ref(schema, value["$ref"])
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(schema)
    for method, path, operation in operations:
        placeholders = set(re.findall(r"{([^{}]+)}", path))
        parameters = [
            *schema["paths"][path].get("parameters", []),
            *operation.get("parameters", []),
        ]
        path_parameters = {
            item["name"] for item in parameters if item.get("in") == "path" and item["required"]
        }
        assert path_parameters == placeholders, f"{method} {path}"


def _surface_fingerprint(schema: Mapping[str, Any]) -> str:
    normalized = []
    for method, path, operation in _operations(schema):
        parameters = [
            *schema["paths"][path].get("parameters", []),
            *operation.get("parameters", []),
        ]
        normalized.append(
            {
                "path": path,
                "method": method,
                "operationId": operation["operationId"],
                "parameters": sorted(
                    (
                        {
                            "name": item.get("name"),
                            "in": item.get("in"),
                            "required": bool(item.get("required")),
                            "schema": item.get("schema"),
                        }
                        for item in parameters
                    ),
                    key=lambda item: (str(item["in"]), str(item["name"])),
                ),
                "body": operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema"),
                "success": sorted(
                    (
                        {
                            "status": status,
                            "schema": response.get("content", {})
                            .get("application/json", {})
                            .get("schema"),
                        }
                        for status, response in operation["responses"].items()
                        if status.startswith("2")
                    ),
                    key=lambda item: item["status"],
                ),
            }
        )
    payload = json.dumps(
        sorted(normalized, key=lambda item: (item["path"], item["method"])),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_complete_runtime_api_surface_is_stable_and_valid() -> None:
    app = create_app()
    routes = _flatten(app.routes)
    api_routes = [route for route in routes if isinstance(route, APIRoute)]
    schema = app.openapi()
    operations = _operations(schema)

    _assert_integrity(schema)
    assert len(routes) == 109
    assert len(api_routes) == 105
    assert len(schema["paths"]) == 84
    assert len(operations) == 105
    assert Counter(method for method, _, _ in operations) == {
        "GET": 40,
        "POST": 39,
        "PATCH": 15,
        "DELETE": 9,
        "HEAD": 2,
    }
    assert sum(not route.include_in_schema for route in api_routes) == 0
    assert {(method, path) for method, path, _ in operations} >= CRITICAL_OPERATIONS
    assert _surface_fingerprint(schema) == EXPECTED_SURFACE_FINGERPRINT
    assert _surface_fingerprint(schema) == _surface_fingerprint(deepcopy(schema))


def test_duplicate_operation_id_is_rejected_by_contract_validator() -> None:
    schema = create_app().openapi()
    schema["paths"]["/health"]["get"]["operationId"] = schema["paths"]["/api/history"]["get"][
        "operationId"
    ]

    with pytest.raises(AssertionError):
        _assert_integrity(schema)
