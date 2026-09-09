"""Trusted Artifact duration additive migration 검증."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260824_0020"
REVISION = "20260824_0021"
SOURCE_HEAD = "20260908_0032"


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _seed_artifact(database_url: str) -> str:
    ids = {name: uuid4().hex for name in ("owner", "asset", "version", "artifact")}
    created_at = "2026-08-24T00:00:00+00:00"
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assets "
                "(asset_id, workspace_id, owner_id, asset_type, "
                "selected_asset_version_id, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:asset, NULL, :owner, 'music', NULL, 'active', :created_at, "
                ":created_at, NULL)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(asset_version_id, asset_id, version_number, version_origin, "
                "parent_asset_version_id, processing_chain_id, provider_id, "
                "model_manifest_id, settings_snapshot, created_by, created_at) "
                "VALUES (:version, :asset, 1, 'import', NULL, NULL, NULL, NULL, "
                "'{}', :owner, :created_at)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO artifacts "
                "(artifact_id, asset_version_id, artifact_kind, media_type, "
                "size_bytes, checksum_algorithm, artifact_checksum, producer_type, "
                "producer_id, run_id, retention_status, created_at) VALUES "
                "(:artifact, :version, 'audio', 'audio/wav', 44, 'sha256', "
                ":checksum, 'import', NULL, NULL, 'active', :created_at)"
            ),
            {**ids, "checksum": "a" * 64, "created_at": created_at},
        )
    engine.dispose()
    return ids["artifact"]


def test_duration_revision_is_single_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION


def test_existing_artifact_remains_unknown_without_payload_backfill(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    artifact_id = _seed_artifact(database_url)

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT duration_us FROM artifacts WHERE artifact_id = :id"),
                {"id": artifact_id},
            ).scalar_one()
            is None
        )
        connection.execute(
            text("UPDATE artifacts SET duration_us = 2000 WHERE artifact_id = :id"),
            {"id": artifact_id},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE artifacts SET duration_us = 0 WHERE artifact_id = :id"),
                {"id": artifact_id},
            )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert "duration_us" not in {
            column["name"] for column in inspect(connection).get_columns("artifacts")
        }
        assert connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_fresh_head_has_nullable_positive_duration_column(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        duration = next(
            column
            for column in inspector.get_columns("artifacts")
            if column["name"] == "duration_us"
        )
        assert duration["nullable"] is True
        checks = {item["name"] for item in inspector.get_check_constraints("artifacts")}
        assert "ck_artifacts_positive_duration_us" in checks
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == SOURCE_HEAD
        )
    engine.dispose()
