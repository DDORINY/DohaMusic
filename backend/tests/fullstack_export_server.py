"""Isolated production-like Backend runtime for the Playwright Export product gate."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from backend.app.factory import create_app
from backend.core.config import Settings
from backend.storage.artifact_resolver import APPROVED_STORAGE_DOMAINS, ArtifactStorageRoots
from backend.tests.test_export_worker_runner import _seed_product_snapshot


def build_app():
    runtime_root = Path(os.environ["DOHA_E2E_RUNTIME_ROOT"]).resolve()
    allowed_parent = Path(os.environ["DOHA_E2E_ALLOWED_PARENT"]).resolve()
    runtime_root.relative_to(allowed_parent)
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    runtime_root.mkdir(parents=True)
    artifact_root = runtime_root / "artifacts"
    staging_root = runtime_root / "staging"
    staging_root.mkdir()
    for domain in APPROVED_STORAGE_DOMAINS:
        (artifact_root / domain).mkdir(parents=True)

    app = create_app(
        Settings(
            database_url=f"sqlite:///{(runtime_root / 'browser.db').as_posix()}",
            auto_migrate=True,
            cursor_signing_key="test-fullstack-export-cursor-key-32-bytes",
            storage_root=runtime_root / "legacy-storage",
            artifact_root=artifact_root,
            artifact_staging_root=staging_root,
            export_worker_poll_interval_seconds=0.01,
            log_level="WARNING",
        )
    )
    production_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def seeded_lifespan(application):
        async with production_lifespan(application):
            roots = ArtifactStorageRoots.from_base_root(artifact_root)
            project_id, snapshot_id = _seed_product_snapshot(
                SimpleNamespace(app=application), staging_root, roots
            )
            workspace = application.state.workspace_service.list_workspaces(limit=1)[0]
            application.state.composition_service.set_project_selection(
                project_id,
                selected_snapshot_id=snapshot_id,
                effective_owner_id=workspace.owner_id,
            )
            initialized = application.state.working_composition_service.initialize(
                project_id,
                effective_owner_id=workspace.owner_id,
                idempotency_key="fullstack-working-initialize",
            )
            application.state.working_composition_service.checkout(
                project_id,
                working_composition_id=initialized.identities["working_composition_id"],
                composition_snapshot_id=snapshot_id,
                expected_revision=initialized.completed_revision,
                effective_owner_id=workspace.owner_id,
                idempotency_key="fullstack-working-checkout",
            )
            seed_file = Path(os.environ["DOHA_E2E_SEED_FILE"])
            seed_file.parent.mkdir(parents=True, exist_ok=True)
            seed_file.write_text(
                json.dumps(
                    {
                        "project_id": str(project_id),
                        "snapshot_id": str(snapshot_id),
                    }
                ),
                encoding="utf-8",
            )
            try:
                yield
            finally:
                seed_file.unlink(missing_ok=True)

    app.router.lifespan_context = seeded_lifespan
    return app


app = build_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
