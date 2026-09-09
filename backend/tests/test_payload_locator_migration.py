"""PayloadLocator additive Alembic migration contract."""

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
REVISION = "20260825_0023"
PREVIOUS_REVISION = "20260825_0022"
TABLE = "payload_locators"
HEAD_REVISION = "20260908_0032"


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_payload_locator_metadata_matches_single_successor_revision() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == [HEAD_REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert len(Base.metadata.tables) == 52

    table = Base.metadata.tables[TABLE]
    assert {column.name for column in table.columns} >= {
        "payload_locator_id",
        "workspace_job_id",
        "provider_job_binding_id",
        "payload_ordinal",
        "expected_payload_checksum",
        "actual_payload_checksum",
        "staging_status",
        "staging_key",
        "revoked_at",
        "lifecycle_revision",
    }
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_payload_locators_binding_ordinal",
        "uq_payload_locators_source_identity",
        "fk_payload_locators_binding_scope",
        "ck_payload_locators_lifecycle_facts",
        "ck_payload_locators_revocation_pair",
    }


def test_fresh_upgrade_downgrade_and_reupgrade(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert TABLE in inspector.get_table_names()
        assert "uq_provider_job_bindings_workspace_identity" in {
            item["name"] for item in inspector.get_indexes("provider_job_bindings")
        }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert TABLE not in inspector.get_table_names()
        assert "uq_provider_job_bindings_workspace_identity" not in {
            item["name"] for item in inspector.get_indexes("provider_job_bindings")
        }
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert TABLE in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_existing_develop_database_upgrades_without_rewriting_existing_rows(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        before = connection.execute(text("SELECT count(*) FROM jobs")).scalar_one()
    engine.dispose()

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar_one() == before
        assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 0
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
