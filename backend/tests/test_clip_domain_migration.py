"""Clip Domain additive Alembic revision의 upgrade/downgrade 검증."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260824_0020"
SOURCE_HEAD = "20260908_0032"
PREVIOUS_REVISION = "20260821_0019"
TABLES = {
    "working_compositions",
    "composition_tracks",
    "composition_clips",
    "composition_snapshot_tracks",
    "composition_snapshot_clips",
}


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _id() -> str:
    return uuid4().hex


def _seed_legacy_snapshot(database_url: str) -> dict[str, str]:
    ids = {
        "workspace": _id(),
        "owner": _id(),
        "project": _id(),
        "asset": _id(),
        "version": _id(),
        "snapshot": _id(),
        "item": _id(),
    }
    created_at = "2026-08-24T00:00:00+00:00"
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, owner_id, name, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:workspace, :owner, 'Workspace', 'active', :created_at, "
                ":created_at, NULL)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO music_projects "
                "(project_id, workspace_id, title, description, lifecycle_status, "
                "created_by, created_at, updated_at, deleted_at) VALUES "
                "(:project, :workspace, 'Project', NULL, 'active', :owner, "
                ":created_at, :created_at, NULL)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(asset_id, workspace_id, owner_id, asset_type, "
                "selected_asset_version_id, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:asset, :workspace, :owner, 'music', NULL, 'active', "
                ":created_at, :created_at, NULL)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(asset_version_id, asset_id, version_number, version_origin, "
                "parent_asset_version_id, processing_chain_id, provider_id, "
                "model_manifest_id, settings_snapshot, created_by, created_at) "
                "VALUES (:version, :asset, 1, 'generated', NULL, NULL, NULL, "
                "NULL, '{}', :owner, :created_at)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO composition_snapshots "
                "(composition_snapshot_id, project_id, snapshot_version, "
                "processing_chain_id, mix_settings_snapshot, provider_versions, "
                "model_manifest_ids, created_by, created_at) VALUES "
                "(:snapshot, :project, 1, NULL, '{}', '{}', '{}', :owner, "
                ":created_at)"
            ),
            {**ids, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO snapshot_items "
                "(snapshot_item_id, composition_snapshot_id, asset_version_id, "
                "item_role, sort_order, created_at) VALUES "
                "(:item, :snapshot, :version, 'music', 0, :created_at)"
            ),
            {**ids, "created_at": created_at},
        )
    engine.dispose()
    return ids


def test_clip_domain_revision_is_single_head_and_additive() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION


def test_clip_domain_existing_schema_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    ids = _seed_legacy_snapshot(database_url)

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) >= TABLES
        assert connection.execute(text("SELECT count(*) FROM snapshot_items")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT asset_version_id FROM snapshot_items WHERE snapshot_item_id = :item"),
                {"item": ids["item"]},
            ).scalar_one()
            == ids["version"]
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert TABLES.isdisjoint(inspect(connection).get_table_names())
        assert connection.execute(text("SELECT count(*) FROM snapshot_items")).scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_clip_domain_fresh_database_upgrade_has_exact_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) >= TABLES
        assert {column["name"] for column in inspector.get_columns("composition_clips")} >= {
            "clip_id",
            "working_composition_id",
            "track_id",
            "source_asset_version_id",
            "timeline_start",
            "source_in",
            "source_out",
            "source_duration",
            "split_from_clip_id",
            "deleted_at",
        }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == SOURCE_HEAD
        )
    engine.dispose()
