"""Mixer, typed history target, and Export result migration proofs."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from backend.db.session import create_database_engine
from backend.tests.test_clip_gain_migration import _config


def test_mixer_export_migration_is_single_head_and_portable(tmp_path: Path) -> None:
    config = _config(f"sqlite:///{(tmp_path / 'mixer-export.db').as_posix()}")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260908_0032"]
    command.upgrade(config, "head")
    engine = create_database_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        assert "job_export_results" in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()
    command.downgrade(config, "20260905_0028")
    command.upgrade(config, "head")


def test_existing_clip_history_target_is_backfilled(tmp_path: Path) -> None:
    config = _config(f"sqlite:///{(tmp_path / 'history-backfill.db').as_posix()}")
    command.upgrade(config, "20260905_0028")
    engine = create_database_engine(config.get_main_option("sqlalchemy.url"))
    working_id, clip_id, entry_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        project_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, name, owner_id, lifecycle_status, created_at, updated_at) "
                "VALUES (:id, 'w', :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(uuid4()), "owner": str(uuid4())},
        )
        workspace_id = connection.execute(
            text("SELECT workspace_id FROM workspaces LIMIT 1")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO music_projects "
                "(project_id, workspace_id, title, lifecycle_status, created_by, "
                "created_at, updated_at) VALUES "
                "(:id, :workspace, 'p', 'active', :owner, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(project_id), "workspace": workspace_id, "owner": str(uuid4())},
        )
        connection.execute(
            text(
                "INSERT INTO working_compositions "
                "(working_composition_id, project_id, mix_settings, revision, "
                "created_at, updated_at) VALUES "
                "(:id, :project, '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(working_id), "project": str(project_id)},
        )
        connection.execute(
            text(
                "INSERT INTO working_composition_history_entries "
                "(history_entry_id, working_composition_id, sequence, command_type, "
                "clip_id, before_state, after_state, created_at) VALUES "
                "(:entry, :working, 1, 'CLIP_GAIN', :clip, '{}', '{}', CURRENT_TIMESTAMP)"
            ),
            {"entry": str(entry_id), "working": str(working_id), "clip": str(clip_id)},
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_database_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT target_type, target_id, clip_id FROM working_composition_history_entries")
        ).one()
        assert row == ("CLIP", str(clip_id), str(clip_id))
    engine.dispose()
