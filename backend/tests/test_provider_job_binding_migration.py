"""Provider Job binding Alembic upgrade/downgrade contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260821_0019"
PREVIOUS_REVISION = "20260820_0018"
TABLE = "provider_job_bindings"
SOURCE_HEAD = "20260908_0032"


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _id(value: int) -> str:
    return f"{value:032x}"


def _seed_existing_job(database_url: str) -> str:
    engine = create_database_engine(database_url)
    workspace_id = _id(1)
    owner_id = _id(2)
    project_id = _id(3)
    job_id = _id(4)
    created_at = "2026-08-20T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, owner_id, name, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:workspace_id, :owner_id, 'Workspace', 'active', :created_at, "
                ":created_at, NULL)"
            ),
            {
                "workspace_id": workspace_id,
                "owner_id": owner_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO music_projects "
                "(project_id, workspace_id, title, description, lifecycle_status, "
                "created_by, created_at, updated_at, deleted_at) VALUES "
                "(:project_id, :workspace_id, 'Project', NULL, 'active', "
                ":owner_id, :created_at, :created_at, NULL)"
            ),
            {
                "project_id": project_id,
                "workspace_id": workspace_id,
                "owner_id": owner_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(job_id, project_id, workspace_id, composition_snapshot_id, "
                "job_type, status, provider_id, api_contract_version, "
                "model_manifest_id, progress_percent, stage, settings_snapshot, "
                "retry_of_job_id, error_code, error_message, error_retryable, "
                "error_details_id, requested_by, started_at, completed_at, "
                "cancel_requested_at, claim_token, claimed_by, lease_expires_at, "
                "heartbeat_at, attempt, created_at) VALUES "
                "(:job_id, :project_id, :workspace_id, NULL, 'vocal_generation', "
                "'queued', 'dohavocal', '0.1.0', 'manifest-1', NULL, NULL, '{}', "
                "NULL, NULL, NULL, NULL, NULL, :owner_id, NULL, NULL, NULL, NULL, "
                "NULL, NULL, NULL, 0, :created_at)"
            ),
            {
                "job_id": job_id,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "owner_id": owner_id,
                "created_at": created_at,
            },
        )
    engine.dispose()
    return job_id


def test_provider_job_binding_metadata_matches_revision() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert len(Base.metadata.tables) == 52

    table = Base.metadata.tables[TABLE]
    assert {column.name for column in table.columns} == {
        "provider_job_binding_id",
        "workspace_job_id",
        "provider_id",
        "provider_job_id",
        "retry_of_provider_job_id",
        "created_at",
    }
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_provider_job_bindings_identity",
        "fk_provider_job_bindings_retry_identity",
        "ck_provider_job_bindings_no_self_retry",
    }


def test_provider_job_binding_migration_round_trip_and_constraints(tmp_path) -> None:
    database_path = tmp_path / "provider-job-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    job_id = _seed_existing_job(database_url)

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        inspector = inspect(connection)
        assert TABLE in inspector.get_table_names()
        assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT count(*) FROM jobs WHERE job_id = :job_id"),
                {"job_id": job_id},
            ).scalar_one()
            == 1
        )
        connection.execute(
            text(
                f"INSERT INTO {TABLE} "
                "(provider_job_binding_id, workspace_job_id, provider_id, "
                "provider_job_id, retry_of_provider_job_id, created_at) VALUES "
                "(:binding_id, :job_id, 'dohavocal', 'provider-1', NULL, :created_at)"
            ),
            {
                "binding_id": _id(5),
                "job_id": job_id,
                "created_at": "2026-08-20T00:00:01+00:00",
            },
        )
        connection.execute(
            text(
                f"INSERT INTO {TABLE} "
                "(provider_job_binding_id, workspace_job_id, provider_id, "
                "provider_job_id, retry_of_provider_job_id, created_at) VALUES "
                "(:binding_id, :job_id, 'dohavocal', 'provider-2', "
                "'provider-1', :created_at)"
            ),
            {
                "binding_id": _id(6),
                "job_id": job_id,
                "created_at": "2026-08-20T00:00:02+00:00",
            },
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

    duplicate = text(
        f"INSERT INTO {TABLE} "
        "(provider_job_binding_id, workspace_job_id, provider_id, provider_job_id, "
        "retry_of_provider_job_id, created_at) VALUES "
        "(:binding_id, :job_id, 'dohavocal', 'provider-1', NULL, :created_at)"
    )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            duplicate,
            {
                "binding_id": _id(7),
                "job_id": job_id,
                "created_at": "2026-08-20T00:00:03+00:00",
            },
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {TABLE} "
                "(provider_job_binding_id, workspace_job_id, provider_id, "
                "provider_job_id, retry_of_provider_job_id, created_at) VALUES "
                "(:binding_id, :job_id, 'dohavocal', 'orphan-retry', "
                "'missing-parent', :created_at)"
            ),
            {
                "binding_id": _id(8),
                "job_id": job_id,
                "created_at": "2026-08-20T00:00:04+00:00",
            },
        )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert TABLE not in inspect(connection).get_table_names()
        assert (
            connection.execute(
                text("SELECT count(*) FROM jobs WHERE job_id = :job_id"),
                {"job_id": job_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
