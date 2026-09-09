from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260824_0021"
REVISION = "20260825_0022"
SOURCE_HEAD = "20260908_0032"
RESULT_COLUMNS = {
    "completed_revision",
    "result_type",
    "result_version",
    "result_payload",
}


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_idempotency_completion_revision_is_single_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION


def test_existing_legacy_result_is_preserved_without_fabricated_backfill(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO idempotency_records "
                "(id, scope, key_hash, request_fingerprint, status, resource_type, "
                "resource_id, response_status, expires_at, created_at, updated_at) "
                "VALUES ('record', 'legacy', :hash, :fingerprint, 'COMPLETED', "
                "'workspace_job', :resource_id, 201, :time, :time, :time)"
            ),
            {
                "hash": "a" * 64,
                "fingerprint": "b" * 64,
                "resource_id": "12345678-1234-1234-1234-123456789abc",
                "time": "2026-08-25T00:00:00+00:00",
            },
        )
    engine.dispose()

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT resource_type, resource_id, response_status, "
                    "completed_revision, result_type, result_version, result_payload "
                    "FROM idempotency_records WHERE id = 'record'"
                )
            )
            .mappings()
            .one()
        )
        assert row["resource_type"] == "workspace_job"
        assert row["response_status"] == 201
        assert all(row[column] is None for column in RESULT_COLUMNS)
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        columns = {
            column["name"] for column in inspect(connection).get_columns("idempotency_records")
        }
        assert RESULT_COLUMNS.isdisjoint(columns)
        assert (
            connection.scalar(
                text("SELECT resource_id FROM idempotency_records WHERE id = 'record'")
            )
            == "12345678-1234-1234-1234-123456789abc"
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_fresh_head_has_nullable_completion_result_columns(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("idempotency_records")
        }
        assert RESULT_COLUMNS.issubset(columns)
        assert all(columns[name]["nullable"] is True for name in RESULT_COLUMNS)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == SOURCE_HEAD
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO idempotency_records "
                "(id, scope, key_hash, request_fingerprint, status, expires_at, "
                "created_at, updated_at) VALUES "
                "('constraints', 'scope', :hash, :fingerprint, 'IN_PROGRESS', "
                ":time, :time, :time)"
            ),
            {
                "hash": "c" * 64,
                "fingerprint": "d" * 64,
                "time": "2026-08-25T00:00:00+00:00",
            },
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE idempotency_records SET completed_revision = -1 WHERE id = 'constraints'")
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE idempotency_records SET result_version = 0 WHERE id = 'constraints'")
        )
    engine.dispose()
