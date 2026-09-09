"""Workspace Job schema와 keyset·claim/lease Index Migration 검증."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
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
REVISION = "20260810_0017"
PREVIOUS_REVISION = "20260809_0016"
SOURCE_HEAD = "20260908_0032"
PUBLIC_INDEXES = {
    "ix_jobs_workspace_keyset": (
        "workspace_id",
        "created_at",
        "job_id",
    ),
    "ix_jobs_workspace_project_keyset": (
        "workspace_id",
        "project_id",
        "created_at",
        "job_id",
    ),
    "ix_jobs_workspace_status_keyset": (
        "workspace_id",
        "status",
        "created_at",
        "job_id",
    ),
    "ix_jobs_workspace_type_keyset": (
        "workspace_id",
        "job_type",
        "created_at",
        "job_id",
    ),
}
WORKER_INDEXES = {
    "ix_jobs_claim_queue": (
        "status",
        "cancel_requested_at",
        "created_at",
        "job_id",
    ),
    "ix_jobs_lease_recovery": (
        "status",
        "lease_expires_at",
        "job_id",
    ),
}
NEW_JOB_COLUMNS = {
    "workspace_id",
    "cancel_requested_at",
    "claim_token",
    "claimed_by",
    "lease_expires_at",
    "heartbeat_at",
    "attempt",
}
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
JOB_TYPES = (
    "lyrics_generation",
    "music_generation",
    "stem_separation",
    "voice_conversion",
    "audio_analysis",
    "mix",
    "export",
)


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _identifier(namespace: int, value: int) -> str:
    return f"{namespace:02x}{value:030x}"


def _insert_fixtures(database_url: str, *, job_count: int = 10_000) -> None:
    engine = create_database_engine(database_url)
    origin = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    origin_value = origin.isoformat()
    workspace_rows = [
        {
            "workspace_id": _identifier(1, index),
            "owner_id": _identifier(2, index),
            "name": f"Workspace {index}",
            "created_at": origin_value,
        }
        for index in range(1, 6)
    ]
    project_rows = [
        {
            "project_id": _identifier(3, index),
            "workspace_id": _identifier(1, index % 5 + 1),
            "title": f"Project {index}",
            "created_by": _identifier(2, index % 5 + 1),
            "created_at": origin_value,
        }
        for index in range(1, 51)
    ]
    job_rows = []
    for index in range(1, job_count + 1):
        created_at = (origin - timedelta(seconds=index // 4)).isoformat()
        job_rows.append(
            {
                "job_id": _identifier(4, index),
                "project_id": _identifier(3, index % 50 + 1),
                "job_type": JOB_TYPES[index % len(JOB_TYPES)],
                "status": STATUSES[index % len(STATUSES)],
                "requested_by": _identifier(2, index % 5 + 1),
                "created_at": created_at,
            }
        )

    asset_id = _identifier(5, 1)
    version_id = _identifier(6, 1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, owner_id, name, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:workspace_id, :owner_id, :name, 'active', :created_at, "
                ":created_at, NULL)"
            ),
            workspace_rows,
        )
        connection.execute(
            text(
                "INSERT INTO music_projects "
                "(project_id, workspace_id, title, description, lifecycle_status, "
                "created_by, created_at, updated_at, deleted_at) VALUES "
                "(:project_id, :workspace_id, :title, NULL, 'active', "
                ":created_by, :created_at, :created_at, NULL)"
            ),
            project_rows,
        )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(asset_id, workspace_id, owner_id, asset_type, "
                "selected_asset_version_id, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:asset_id, :workspace_id, :owner_id, 'music', NULL, 'active', "
                ":created_at, :created_at, NULL)"
            ),
            {
                "asset_id": asset_id,
                "workspace_id": _identifier(1, 2),
                "owner_id": _identifier(2, 2),
                "created_at": origin_value,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(asset_version_id, asset_id, version_number, version_origin, "
                "parent_asset_version_id, processing_chain_id, provider_id, "
                "model_manifest_id, settings_snapshot, created_by, created_at) "
                "VALUES (:version_id, :asset_id, 1, 'user_created', NULL, NULL, "
                "NULL, NULL, '{}', :owner_id, :created_at)"
            ),
            {
                "version_id": version_id,
                "asset_id": asset_id,
                "owner_id": _identifier(2, 2),
                "created_at": origin_value,
            },
        )
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(job_id, project_id, composition_snapshot_id, job_type, status, "
                "provider_id, api_contract_version, model_manifest_id, "
                "progress_percent, stage, settings_snapshot, retry_of_job_id, "
                "error_code, error_message, error_retryable, error_details_id, "
                "requested_by, started_at, completed_at, created_at) VALUES "
                "(:job_id, :project_id, NULL, :job_type, :status, NULL, '0.1.0', "
                "NULL, NULL, NULL, '{}', NULL, NULL, NULL, NULL, NULL, "
                ":requested_by, NULL, NULL, :created_at)"
            ),
            job_rows,
        )
        connection.execute(
            text(
                "INSERT INTO job_inputs "
                "(job_input_id, job_id, asset_version_id, artifact_id, "
                "input_order, created_at) VALUES "
                "(:item_id, :job_id, :version_id, NULL, 0, :created_at)"
            ),
            {
                "item_id": _identifier(7, 1),
                "job_id": _identifier(4, 1),
                "version_id": version_id,
                "created_at": origin_value,
            },
        )
        connection.execute(
            text(
                "INSERT INTO job_outputs "
                "(job_output_id, job_id, asset_version_id, artifact_id, "
                "output_order, created_at) VALUES "
                "(:item_id, :job_id, :version_id, NULL, 0, :created_at)"
            ),
            {
                "item_id": _identifier(8, 1),
                "job_id": _identifier(4, 1),
                "version_id": version_id,
                "created_at": origin_value,
            },
        )
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _public_queries(*, migrated: bool) -> dict[str, tuple[str, tuple[object, ...]]]:
    table = (
        "jobs"
        if migrated
        else ("jobs JOIN music_projects ON music_projects.project_id = jobs.project_id")
    )
    scope = "jobs.workspace_id = ?" if migrated else "music_projects.workspace_id = ?"
    workspace_id = _identifier(1, 2)
    project_id = _identifier(3, 1)
    anchor_time = "2026-08-10 11:40:00.000000"
    anchor_id = _identifier(4, 4_800)
    base = f"SELECT jobs.job_id FROM {table} WHERE {scope}"
    order = " ORDER BY jobs.created_at DESC, jobs.job_id DESC LIMIT 25"
    after = " AND (jobs.created_at < ? OR (jobs.created_at = ? AND jobs.job_id < ?))"
    return {
        "workspace_first": (base + order, (workspace_id,)),
        "workspace_next": (
            base + after + order,
            (workspace_id, anchor_time, anchor_time, anchor_id),
        ),
        "project_first": (
            base + " AND jobs.project_id = ?" + order,
            (workspace_id, project_id),
        ),
        "project_next": (
            base + " AND jobs.project_id = ?" + after + order,
            (workspace_id, project_id, anchor_time, anchor_time, anchor_id),
        ),
        "status_first": (
            base + " AND jobs.status = ?" + order,
            (workspace_id, "queued"),
        ),
        "status_next": (
            base + " AND jobs.status = ?" + after + order,
            (workspace_id, "queued", anchor_time, anchor_time, anchor_id),
        ),
        "type_first": (
            base + " AND jobs.job_type = ?" + order,
            (workspace_id, "mix"),
        ),
        "type_next": (
            base + " AND jobs.job_type = ?" + after + order,
            (workspace_id, "mix", anchor_time, anchor_time, anchor_id),
        ),
        "project_status": (
            base + " AND jobs.project_id = ? AND jobs.status = ?" + order,
            (workspace_id, project_id, "queued"),
        ),
        "project_type": (
            base + " AND jobs.project_id = ? AND jobs.job_type = ?" + order,
            (workspace_id, project_id, "mix"),
        ),
        "status_type": (
            base + " AND jobs.status = ? AND jobs.job_type = ?" + order,
            (workspace_id, "queued", "mix"),
        ),
        "all_filters": (
            base
            + " AND jobs.project_id = ? AND jobs.status = ? "
            + "AND jobs.job_type = ?"
            + order,
            (workspace_id, project_id, "queued", "mix"),
        ),
    }


def _plans_and_rows(database_url: str, *, migrated: bool):
    engine = create_database_engine(database_url)
    plans: dict[str, str] = {}
    rows: dict[str, list[tuple[object, ...]]] = {}
    with engine.connect() as connection:
        for name, (query, parameters) in _public_queries(migrated=migrated).items():
            plans[name] = " | ".join(
                row[3]
                for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {query}", parameters)
            )
            rows[name] = list(connection.exec_driver_sql(query, parameters))
    engine.dispose()
    return plans, rows


def _job_digest(database_url: str) -> str:
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT job_id, project_id, job_type, status, api_contract_version, "
            "settings_snapshot, requested_by, created_at FROM jobs ORDER BY job_id"
        ).all()
    engine.dispose()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_job_schema_metadata_matches_migration_contract() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert len(Base.metadata.tables) == 52

    jobs = Base.metadata.tables["jobs"]
    assert set(jobs.columns.keys()) >= NEW_JOB_COLUMNS
    assert jobs.c.workspace_id.nullable is True
    assert jobs.c.attempt.nullable is False
    assert jobs.c.attempt.server_default is not None
    assert Base.metadata.tables["job_inputs"].c.input_role.nullable is True
    assert Base.metadata.tables["job_outputs"].c.output_role.nullable is True

    indexes = {
        index.name: tuple(expression.name for expression in index.expressions)
        for index in jobs.indexes
        if index.name in PUBLIC_INDEXES | WORKER_INDEXES
    }
    assert indexes == PUBLIC_INDEXES | WORKER_INDEXES
    assert "ck_jobs_attempt_nonnegative" in {constraint.name for constraint in jobs.constraints}


def test_job_schema_round_trip_and_query_plans(tmp_path: Path) -> None:
    database_path = tmp_path / "workspace-job-schema.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_fixtures(database_url)

    baseline_plans, baseline_rows = _plans_and_rows(database_url, migrated=False)
    baseline_digest = _job_digest(database_url)
    assert all("USE TEMP B-TREE" in plan for plan in baseline_plans.values())

    command.upgrade(config, REVISION)
    upgraded_plans, upgraded_rows = _plans_and_rows(database_url, migrated=True)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("jobs")}
        reflected_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("jobs")
            if index["name"] in PUBLIC_INDEXES | WORKER_INDEXES
        }
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        counts = {
            table: connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
            for table in ("jobs", "job_inputs", "job_outputs")
        }
        mismatched_workspaces = connection.exec_driver_sql(
            "SELECT count(*) FROM jobs JOIN music_projects "
            "ON music_projects.project_id = jobs.project_id "
            "WHERE jobs.workspace_id != music_projects.workspace_id"
        ).scalar_one()
        roles = connection.exec_driver_sql(
            "SELECT "
            "(SELECT input_role FROM job_inputs LIMIT 1), "
            "(SELECT output_role FROM job_outputs LIMIT 1)"
        ).one()
        attempts = connection.exec_driver_sql("SELECT min(attempt), max(attempt) FROM jobs").one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_list(jobs)").all()
        integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
        foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()

    assert revision == REVISION
    assert columns["workspace_id"]["nullable"] is True
    assert set(columns) >= NEW_JOB_COLUMNS
    assert reflected_indexes == PUBLIC_INDEXES | WORKER_INDEXES
    assert any(
        foreign_key[2] == "workspaces"
        and foreign_key[3] == "workspace_id"
        and foreign_key[6] == "RESTRICT"
        for foreign_key in foreign_keys
    )
    assert counts == {"jobs": 10_000, "job_inputs": 1, "job_outputs": 1}
    assert mismatched_workspaces == 0
    assert roles == (None, None)
    assert attempts == (0, 0)
    assert integrity == "ok"
    assert foreign_key_violations == []
    assert upgraded_rows == baseline_rows
    assert _job_digest(database_url) == baseline_digest

    expected_public_indexes = {
        "workspace_first": ("ix_jobs_workspace_keyset",),
        "workspace_next": ("ix_jobs_workspace_keyset",),
        "project_first": ("ix_jobs_workspace_project_keyset",),
        "project_next": ("ix_jobs_workspace_project_keyset",),
        "status_first": ("ix_jobs_workspace_status_keyset",),
        "status_next": ("ix_jobs_workspace_status_keyset",),
        "type_first": ("ix_jobs_workspace_type_keyset",),
        "type_next": ("ix_jobs_workspace_type_keyset",),
        "project_status": (
            "ix_jobs_workspace_project_keyset",
            "ix_jobs_workspace_status_keyset",
        ),
        "project_type": (
            "ix_jobs_workspace_project_keyset",
            "ix_jobs_workspace_type_keyset",
        ),
        "status_type": (
            "ix_jobs_workspace_status_keyset",
            "ix_jobs_workspace_type_keyset",
        ),
        "all_filters": (
            "ix_jobs_workspace_project_keyset",
            "ix_jobs_workspace_status_keyset",
            "ix_jobs_workspace_type_keyset",
        ),
    }
    for name, plan in upgraded_plans.items():
        assert any(index_name in plan for index_name in expected_public_indexes[name])
        assert "USE TEMP B-TREE" not in plan
        assert "SCAN jobs" not in plan

    with engine.connect() as connection:
        claim_plan = " | ".join(
            row[3]
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT job_id FROM jobs "
                "WHERE status = 'queued' AND cancel_requested_at IS NULL "
                "ORDER BY created_at ASC, job_id ASC LIMIT 1"
            )
        )
        lease_plan = " | ".join(
            row[3]
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT job_id FROM jobs "
                "WHERE status = 'running' AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at <= '2026-08-10T12:00:00+00:00' "
                "ORDER BY lease_expires_at ASC, job_id ASC LIMIT 25"
            )
        )
        assert "ix_jobs_claim_queue" in claim_plan
        assert "ix_jobs_lease_recovery" in lease_plan
        assert "USE TEMP B-TREE" not in claim_plan + lease_plan
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE jobs SET attempt = -1 WHERE job_id = ?",
            (_identifier(4, 1),),
        )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        downgraded_columns = {column["name"] for column in inspector.get_columns("jobs")}
        downgraded_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
        downgraded_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        downgraded_counts = {
            table: connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
            for table in ("jobs", "job_inputs", "job_outputs")
        }
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    assert downgraded_revision == PREVIOUS_REVISION
    assert not NEW_JOB_COLUMNS & downgraded_columns
    assert not (PUBLIC_INDEXES | WORKER_INDEXES).keys() & downgraded_indexes
    assert downgraded_counts == counts
    assert _job_digest(database_url) == baseline_digest
