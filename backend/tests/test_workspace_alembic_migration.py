"""Workspace additive Alembic revision의 schema 계약 검증."""

from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.workspace import WORKSPACE_ENTITY_CLASSES

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
    ROOT / "backend" / "alembic" / "versions" / "20260806_0012_add_workspace_entity_tables.py"
)
REVISION = "20260806_0012"
PREVIOUS_REVISION = "20260801_0011"
KEYSET_INDEX_NAMES = {
    "ix_assets_owner_active_keyset",
    "ix_assets_owner_workspace_active_keyset",
    "ix_jobs_claim_queue",
    "ix_jobs_lease_recovery",
    "ix_jobs_workspace_keyset",
    "ix_jobs_workspace_project_keyset",
    "ix_jobs_workspace_status_keyset",
    "ix_jobs_workspace_type_keyset",
    "ix_music_projects_workspace_active_keyset",
    "ix_project_assets_active_keyset",
    "ix_workspaces_active_keyset",
    "ix_workspaces_owner_active_keyset",
}
POST_REVISION_INDEX_NAMES = {
    "ix_artifacts_version_created",
    "uq_composition_snapshots_project_identity",
}
POST_REVISION_CONSTRAINT_NAMES = {
    "ck_artifacts_positive_duration_us",
    "fk_music_projects_export_project_asset",
    "uq_music_projects_export_project_asset",
    "uq_project_assets_project_identity",
}
FORBIDDEN_OPERATIONS = {
    "add_column",
    "alter_column",
    "bulk_insert",
    "create_foreign_key",
    "drop_column",
    "drop_constraint",
    "execute",
    "rename_table",
}


def _workspace_tables() -> set[str]:
    return {
        entity.__tablename__
        for entity in WORKSPACE_ENTITY_CLASSES
        if entity.__tablename__
        not in {
            "composition_clips",
            "composition_snapshot_clips",
            "composition_snapshot_tracks",
            "composition_tracks",
            "project_composition_selections",
            "payload_locators",
            "provider_job_bindings",
            "working_preview_assets",
            "working_preview_render_clips",
            "working_preview_render_tracks",
            "working_preview_renders",
            "working_compositions",
            "working_composition_history_entries",
            "working_composition_history_states",
            "job_export_results",
            "job_export_publications",
        }
    }


def _operations(function_name: str) -> list[tuple[str, str | None]]:
    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    operations: list[tuple[str, str | None]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "op":
            continue
        table_name = None
        if node.args and isinstance(node.args[0], ast.Constant):
            table_name = node.args[0].value
        operations.append((node.func.attr, table_name))
    return operations


def _revision_assignment(name: str) -> str:
    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )
    assert assignment.value is not None
    assert isinstance(assignment.value, ast.Constant)
    assert isinstance(assignment.value.value, str)
    return assignment.value.value


def test_workspace_revision_is_additive_and_matches_metadata() -> None:
    workspace_tables = _workspace_tables()
    upgrade_operations = _operations("upgrade")
    downgrade_operations = _operations("downgrade")

    created_tables = {
        table for operation, table in upgrade_operations if operation == "create_table"
    }
    dropped_tables = {
        table for operation, table in downgrade_operations if operation == "drop_table"
    }
    used_operations = {operation for operation, _ in upgrade_operations + downgrade_operations}

    assert _revision_assignment("revision") == REVISION
    assert _revision_assignment("down_revision") == PREVIOUS_REVISION
    assert len(WORKSPACE_ENTITY_CLASSES) == 35
    assert len(workspace_tables) == 21
    assert created_tables == workspace_tables
    assert dropped_tables == workspace_tables
    assert not used_operations.intersection(FORBIDDEN_OPERATIONS)

    index_names = [
        index.name
        for table_name in workspace_tables
        for index in Base.metadata.tables[table_name].indexes
        if index.name and index.name not in POST_REVISION_INDEX_NAMES
    ]
    constraint_names = [
        constraint.name
        for table_name in workspace_tables
        for constraint in Base.metadata.tables[table_name].constraints
        if constraint.name and constraint.name not in POST_REVISION_CONSTRAINT_NAMES
    ]
    assert len(set(index_names) - KEYSET_INDEX_NAMES) == 109
    assert len(index_names) == 121
    assert len(index_names) == len(set(index_names))
    assert len(constraint_names) == 24
    assert len(constraint_names) == len(set(constraint_names))


def test_workspace_revision_round_trip_on_temporary_sqlite(tmp_path: Path) -> None:
    database_path = tmp_path / "workspace-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, REVISION)

    workspace_tables = _workspace_tables()
    legacy_tables = (
        set(Base.metadata.tables)
        - workspace_tables
        - {
            "artifact_storage_locations",
            "composition_clips",
            "composition_snapshot_clips",
            "composition_snapshot_tracks",
            "composition_tracks",
            "project_composition_selections",
            "payload_locators",
            "provider_job_bindings",
            "working_preview_assets",
            "working_preview_render_clips",
            "working_preview_render_tracks",
            "working_preview_renders",
            "working_compositions",
            "working_composition_history_entries",
            "working_composition_history_states",
            "job_export_results",
            "job_export_publications",
        }
    )
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        inspector = inspect(connection)
        upgraded_tables = set(inspector.get_table_names())
        upgraded_revision = connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()
        workspace_foreign_keys = sum(
            len(inspector.get_foreign_keys(table_name)) for table_name in workspace_tables
        )
        foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()

    assert upgraded_revision == REVISION
    assert upgraded_tables - {"alembic_version"} == (
        set(Base.metadata.tables)
        - {
            "artifact_storage_locations",
            "composition_clips",
            "composition_snapshot_clips",
            "composition_snapshot_tracks",
            "composition_tracks",
            "project_composition_selections",
            "payload_locators",
            "provider_job_bindings",
            "working_preview_assets",
            "working_preview_render_clips",
            "working_preview_render_tracks",
            "working_preview_renders",
            "working_compositions",
            "working_composition_history_entries",
            "working_composition_history_states",
            "job_export_results",
            "job_export_publications",
        }
    )
    assert workspace_foreign_keys == 39
    assert foreign_key_violations == []

    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        downgraded_tables = set(inspect(connection).get_table_names())
        downgraded_revision = connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()
    engine.dispose()

    assert downgraded_revision == PREVIOUS_REVISION
    assert downgraded_tables - {"alembic_version"} == legacy_tables
