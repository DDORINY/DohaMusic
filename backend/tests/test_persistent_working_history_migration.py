"""Persistent WorkingComposition history migration proofs."""

from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from backend.db.session import create_database_engine
from backend.tests.test_clip_gain_migration import _config


def test_persistent_history_is_single_head_and_additive(tmp_path: Path) -> None:
    config = _config(f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260908_0032"]
    assert script.get_revision("20260906_0029").down_revision == "20260905_0028"
    command.upgrade(config, "head")
    engine = create_database_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert {
            "working_composition_history_states",
            "working_composition_history_entries",
        } <= tables
        count = connection.execute(
            text("SELECT count(*) FROM working_composition_history_entries")
        ).scalar_one()
        assert count == 0
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()
