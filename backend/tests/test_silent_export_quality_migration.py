"""Nullable exact-silence Export quality migration contracts."""

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.tests.test_export_publication_migration import _config, _revision

PREVIOUS_REVISION = "20260907_0031"
REVISION = "20260908_0032"


def _nullable(engine) -> dict[str, bool]:
    return {
        column["name"]: column["nullable"]
        for column in inspect(engine).get_columns("job_export_results")
    }


def test_0032_upgrade_downgrade_reupgrade_without_silence(tmp_path: Path) -> None:
    database = tmp_path / "silent-quality-roundtrip.db"
    config = _config(database)
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    command.upgrade(config, PREVIOUS_REVISION)
    assert not _nullable(engine)["integrated_loudness_lufs"]
    assert not _nullable(engine)["true_peak_dbtp"]
    command.upgrade(config, REVISION)
    assert _revision(engine) == REVISION
    assert _nullable(engine)["integrated_loudness_lufs"]
    assert _nullable(engine)["true_peak_dbtp"]
    command.downgrade(config, PREVIOUS_REVISION)
    assert _revision(engine) == PREVIOUS_REVISION
    assert not _nullable(engine)["integrated_loudness_lufs"]
    assert not _nullable(engine)["true_peak_dbtp"]
    command.upgrade(config, "head")
    assert _revision(engine) == REVISION
    engine.dispose()


def test_0032_downgrade_with_silent_result_fails_without_fabrication(tmp_path: Path) -> None:
    database = tmp_path / "silent-quality-blocked-downgrade.db"
    config = _config(database)
    command.upgrade(config, REVISION)
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO job_export_results (job_id, composition_snapshot_id, export_format, "
                "render_fingerprint, exported_asset_version_id, exported_artifact_id, "
                "integrated_loudness_lufs, true_peak_dbtp, target_lufs, minimum_lufs, "
                "maximum_lufs, maximum_true_peak_dbtp, loudness_passed, true_peak_passed, "
                "overall_pass, analyzer_name, analyzer_version, created_at) VALUES "
                "('00000000000000000000000000000001', '00000000000000000000000000000002', "
                "'wav', :fingerprint, '00000000000000000000000000000003', "
                "'00000000000000000000000000000004', NULL, NULL, -14, -15, -13, -1, "
                "0, 1, 0, 'test', '1', CURRENT_TIMESTAMP)"
            ),
            {"fingerprint": "0" * 64},
        )
    with pytest.raises(RuntimeError, match="SILENT_EXPORT_QUALITY_DOWNGRADE_BLOCKED"):
        command.downgrade(config, PREVIOUS_REVISION)
    assert _revision(engine) == REVISION
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT integrated_loudness_lufs, true_peak_dbtp FROM job_export_results")
        ).one() == (None, None)
    engine.dispose()
