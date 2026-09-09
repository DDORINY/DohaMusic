"""Clip Gain additive migration과 기존 row 기본값 계약."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.session import create_database_engine
from backend.models.workspace import (
    Artifact,
    Asset,
    AssetType,
    AssetVersion,
    CompositionSnapshot,
    CompositionSnapshotTrack,
    CompositionTrack,
    Job,
    JobStatus,
    MusicProject,
    ProjectAsset,
    WorkingComposition,
    WorkingPreviewAsset,
    WorkingPreviewRender,
    WorkingPreviewRenderTrack,
    Workspace,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260830_0025"
PREVIOUS_REVISION = "20260828_0024"
GAIN_TABLES = (
    "composition_clips",
    "composition_snapshot_clips",
    "working_preview_render_clips",
)


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _seed_pre_gain_rows(database_url: str) -> None:
    engine = create_database_engine(database_url)
    owner_id = uuid4()
    workspace = Workspace(
        workspace_id=uuid4(),
        owner_id=owner_id,
        name="Gain migration",
        lifecycle_status="active",
    )
    project = MusicProject(
        project_id=uuid4(),
        workspace_id=workspace.workspace_id,
        title="Gain migration",
        lifecycle_status="active",
        created_by=owner_id,
    )
    source_asset = Asset(
        asset_id=uuid4(),
        workspace_id=workspace.workspace_id,
        owner_id=owner_id,
        asset_type=AssetType.MUSIC,
        lifecycle_status="active",
    )
    preview_asset = Asset(
        asset_id=uuid4(),
        workspace_id=workspace.workspace_id,
        owner_id=owner_id,
        asset_type=AssetType.MIX,
        lifecycle_status="active",
    )
    source_version = AssetVersion(
        asset_version_id=uuid4(),
        asset_id=source_asset.asset_id,
        version_number=1,
        version_origin="test",
        settings_snapshot={},
        created_by=owner_id,
    )
    artifact = Artifact(
        artifact_id=uuid4(),
        asset_version_id=source_version.asset_version_id,
        artifact_kind="audio",
        media_type="audio/wav",
        size_bytes=100,
        duration_us=1_000_000,
        checksum_algorithm="sha256",
        artifact_checksum="a" * 64,
        producer_type="workspace",
        retention_status="active",
    )
    working = WorkingComposition(
        working_composition_id=uuid4(),
        project_id=project.project_id,
        mix_settings={},
        revision=0,
    )
    track = CompositionTrack(
        track_id=uuid4(),
        working_composition_id=working.working_composition_id,
        track_type="audio",
        name="Audio",
        track_order=0,
    )
    snapshot = CompositionSnapshot(
        composition_snapshot_id=uuid4(),
        project_id=project.project_id,
        snapshot_version=1,
        mix_settings_snapshot={},
        provider_versions={},
        model_manifest_ids={},
        created_by=owner_id,
    )
    snapshot_track = CompositionSnapshotTrack(
        snapshot_track_id=uuid4(),
        composition_snapshot_id=snapshot.composition_snapshot_id,
        canonical_track_id=track.track_id,
        track_type="audio",
        name="Audio",
        track_order=0,
    )
    job = Job(
        job_id=uuid4(),
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
        job_type="working_preview",
        status=JobStatus.QUEUED,
        api_contract_version="working-preview.v1",
        progress_percent=0,
        settings_snapshot={"manifest_schema": 1},
        requested_by=owner_id,
        attempt=0,
    )
    preview_render = WorkingPreviewRender(
        preview_render_id=uuid4(),
        project_id=project.project_id,
        working_composition_id=working.working_composition_id,
        rendered_revision=0,
        workspace_job_id=job.job_id,
        preview_asset_id=preview_asset.asset_id,
        payload_expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    preview_track = WorkingPreviewRenderTrack(
        preview_render_id=preview_render.preview_render_id,
        track_id=track.track_id,
        track_order=0,
    )

    def historical_values(item: object, table: Table) -> dict[str, object]:
        values: dict[str, object] = {}
        now = datetime.now(UTC)
        for column in table.columns:
            value = getattr(item, column.name, None)
            if value is not None:
                value = getattr(value, "value", value)
                values[column.name] = value.hex if isinstance(value, UUID) else value
            elif column.primary_key:
                values[column.name] = uuid4().hex
            elif column.name in {"created_at", "updated_at"}:
                values[column.name] = now
        return values

    project_asset = ProjectAsset(
        project_id=project.project_id,
        asset_id=source_asset.asset_id,
        role="music",
        display_order=0,
    )
    preview_asset_binding = WorkingPreviewAsset(
        project_id=project.project_id, asset_id=preview_asset.asset_id
    )
    ordered_rows = (
        workspace,
        project,
        source_asset,
        preview_asset,
        source_version,
        artifact,
        project_asset,
        working,
        snapshot,
        job,
        track,
        snapshot_track,
        preview_asset_binding,
        preview_render,
        preview_track,
    )
    historical_metadata = MetaData()
    historical_tables: dict[str, Table] = {}
    with engine.begin() as connection:
        for item in ordered_rows:
            table_name = item.__tablename__
            table = historical_tables.setdefault(
                table_name, Table(table_name, historical_metadata, autoload_with=connection)
            )
            connection.execute(table.insert().values(**historical_values(item, table)))

    clip_id = uuid4()
    snapshot_clip_id = uuid4()
    metadata = MetaData()
    composition_clips = Table("composition_clips", metadata, autoload_with=engine)
    snapshot_clips = Table("composition_snapshot_clips", metadata, autoload_with=engine)
    preview_clips = Table("working_preview_render_clips", metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            composition_clips.insert().values(
                clip_id=clip_id.hex,
                working_composition_id=working.working_composition_id.hex,
                track_id=track.track_id.hex,
                source_asset_version_id=source_version.asset_version_id.hex,
                timeline_start=0,
                source_in=0,
                source_out=1_000_000,
                source_duration=1_000_000,
                split_from_clip_id=None,
                deleted_at=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        connection.execute(
            snapshot_clips.insert().values(
                snapshot_clip_id=snapshot_clip_id.hex,
                composition_snapshot_id=snapshot.composition_snapshot_id.hex,
                snapshot_track_id=snapshot_track.snapshot_track_id.hex,
                canonical_clip_id=clip_id.hex,
                source_asset_version_id=source_version.asset_version_id.hex,
                timeline_start=0,
                source_in=0,
                source_out=1_000_000,
                source_duration=1_000_000,
                split_from_clip_id=None,
            )
        )
        connection.execute(
            preview_clips.insert().values(
                preview_render_id=preview_render.preview_render_id.hex,
                clip_id=clip_id.hex,
                track_id=track.track_id.hex,
                canonical_order=0,
                source_asset_version_id=source_version.asset_version_id.hex,
                source_artifact_id=artifact.artifact_id.hex,
                source_in_us=0,
                source_out_us=1_000_000,
                source_duration_us=1_000_000,
                timeline_start_us=0,
            )
        )
    engine.dispose()


def test_clip_gain_revision_is_latest_single_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_revision(REVISION).revision == REVISION
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION


def test_clip_gain_upgrade_defaults_existing_rows_and_is_reversible(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'clip-gain.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed_pre_gain_rows(database_url)

    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name in GAIN_TABLES:
            column = next(
                item for item in inspector.get_columns(table_name) if item["name"] == "gain_db"
            )
            assert column["nullable"] is False
            assert connection.execute(text(f"SELECT gain_db FROM {table_name}")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(text("UPDATE composition_clips SET gain_db = 24.01"))
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name in GAIN_TABLES:
            assert "gain_db" not in {item["name"] for item in inspector.get_columns(table_name)}
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
