"""Alembic 0030 durable Export publication ledger contracts."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260907_0030"
CURRENT_HEAD = "20260908_0032"
PREVIOUS_REVISION = "20260906_0029"
TABLE = "job_export_publications"
EXPECTED_COLUMNS = {
    "job_id",
    "composition_snapshot_id",
    "export_format",
    "storage_domain",
    "storage_key",
    "state",
    "expected_sha256",
    "expected_size_bytes",
    "artifact_id",
    "version",
    "created_at",
    "updated_at",
}


def _config(database_path: Path) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version")).scalar_one()


def test_0030_upgrade_downgrade_reupgrade_roundtrip(tmp_path: Path) -> None:
    database_path = tmp_path / "export-publication-ledger.db"
    config = _config(database_path)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, PREVIOUS_REVISION)
    before_tables = set(inspect(engine).get_table_names())
    assert TABLE not in before_tables

    command.upgrade(config, REVISION)
    inspector = inspect(engine)
    assert _revision(engine) == REVISION
    assert set(inspector.get_table_names()) == before_tables | {TABLE}
    assert {column["name"] for column in inspector.get_columns(TABLE)} == EXPECTED_COLUMNS
    assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys(TABLE)} == {
        "jobs",
        "composition_snapshots",
        "artifacts",
    }
    assert {index["name"] for index in inspector.get_indexes(TABLE)} == {
        "ix_job_export_publications_snapshot",
        "ix_job_export_publications_state",
    }

    command.downgrade(config, PREVIOUS_REVISION)
    assert _revision(engine) == PREVIOUS_REVISION
    assert set(inspect(engine).get_table_names()) == before_tables

    command.upgrade(config, REVISION)
    assert _revision(engine) == REVISION
    assert set(inspect(engine).get_table_names()) == before_tables | {TABLE}
    engine.dispose()


def test_0030_fresh_upgrade_reaches_single_head(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh-export-publication-ledger.db"
    config = _config(database_path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    assert TABLE in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
