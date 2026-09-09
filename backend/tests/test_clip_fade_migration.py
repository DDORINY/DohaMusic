"""Clip Fade additive migration과 기존 manifest 호환 계약."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.session import create_database_engine
from backend.tests.test_clip_gain_migration import _config, _seed_pre_gain_rows

REVISION = "20260903_0026"
PREVIOUS_REVISION = "20260830_0025"
FADE_COLUMNS = {
    "composition_clips": ("fade_in", "fade_out"),
    "composition_snapshot_clips": ("fade_in", "fade_out"),
    "working_preview_render_clips": ("fade_in_us", "fade_out_us"),
}


def test_clip_fade_revision_is_latest_single_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == ["20260908_0032"]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION


def test_clip_fade_upgrade_defaults_existing_rows_and_is_reversible(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'clip-fade.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed_pre_gain_rows(database_url)

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name, columns in FADE_COLUMNS.items():
            actual = {item["name"]: item for item in inspector.get_columns(table_name)}
            for column_name in columns:
                assert actual[column_name]["nullable"] is False
                assert (
                    connection.execute(text(f"SELECT {column_name} FROM {table_name}")).scalar_one()
                    == 0
                )
        assert connection.execute(text("SELECT gain_db FROM composition_clips")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"

    valid_updates = (
        "UPDATE composition_clips SET fade_in = 1, fade_out = 999999",
        "UPDATE composition_snapshot_clips SET fade_in = 1000000, fade_out = 0",
        "UPDATE working_preview_render_clips SET fade_in_us = 400000, fade_out_us = 600000",
    )
    with engine.begin() as connection:
        for statement in valid_updates:
            connection.execute(text(statement))
    invalid_updates = (
        "UPDATE composition_clips SET fade_in = -1",
        "UPDATE composition_snapshot_clips SET fade_out = -1",
        "UPDATE working_preview_render_clips SET fade_in_us = 500001, fade_out_us = 500000",
    )
    for statement in invalid_updates:
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(text(statement))
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name, columns in FADE_COLUMNS.items():
            actual = {item["name"] for item in inspector.get_columns(table_name)}
            assert actual.isdisjoint(columns)
            assert connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one() == 1
        assert "gain_db" in {item["name"] for item in inspector.get_columns("composition_clips")}
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_clip_fade_fresh_bootstrap_reaches_head(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'clip-fade-fresh.db').as_posix()}"
    command.upgrade(_config(database_url), "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name, columns in FADE_COLUMNS.items():
            actual = {item["name"] for item in inspector.get_columns(table_name)}
            assert actual.issuperset(columns)
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "20260908_0032"
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
