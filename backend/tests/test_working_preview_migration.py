"""Working Preview additive manifest migration contract."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260828_0024"
SOURCE_HEAD = "20260908_0032"
PREVIOUS_REVISION = "20260825_0023"
TABLES = {
    "working_preview_assets",
    "working_preview_renders",
    "working_preview_render_tracks",
    "working_preview_render_clips",
}


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_preview_metadata_and_single_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert set(Base.metadata.tables) >= TABLES
    artifact_fk = Base.metadata.tables["artifacts"].c.asset_version_id
    assert artifact_fk.nullable is False


def test_existing_develop_upgrade_is_additive_and_reversible(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'preview-migration.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        before = set(inspect(connection).get_table_names())
        assert TABLES.isdisjoint(before)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) >= TABLES
        artifact_column = next(
            item
            for item in inspector.get_columns("artifacts")
            if item["name"] == "asset_version_id"
        )
        assert artifact_column["nullable"] is False
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert TABLES.isdisjoint(inspect(connection).get_table_names())
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()
