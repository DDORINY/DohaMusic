"""Asset Owner scope keyset Index와 Query Plan 검증."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260808_0015"
PREVIOUS_REVISION = "20260807_0014"
SOURCE_HEAD = "20260908_0032"
INDEXES = {
    "ix_assets_owner_active_keyset": (
        "owner_id",
        "deleted_at",
        "created_at",
        "asset_id",
    ),
    "ix_assets_owner_workspace_active_keyset": (
        "owner_id",
        "workspace_id",
        "deleted_at",
        "created_at",
        "asset_id",
    ),
}


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _identifier(namespace: int, value: int) -> str:
    return f"{namespace:02x}{value:030x}"


def _insert_fixtures(database_url: str) -> None:
    engine = create_database_engine(database_url)
    origin = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    owners = [_identifier(1, value) for value in range(1, 5)]
    workspaces = [
        {
            "workspace_id": _identifier(2, value),
            "owner_id": owners[(value - 1) % len(owners)],
            "name": f"Workspace {value}",
            "created_at": origin,
        }
        for value in range(1, 13)
    ]
    kinds = ("lyrics", "music", "vocal", "stem", "recording", "mix", "export")
    statuses = ("draft", "active", "archived")
    assets = []
    for value in range(1, 6_001):
        owner_index = (value - 1) % len(owners)
        created_at = origin - timedelta(minutes=value // 12)
        workspace_id = (
            None if value % 19 == 0 else _identifier(2, owner_index + 1 + 4 * ((value // 4) % 3))
        )
        assets.append(
            {
                "asset_id": _identifier(3, value),
                "workspace_id": workspace_id,
                "owner_id": owners[owner_index],
                "asset_type": kinds[value % len(kinds)],
                "lifecycle_status": statuses[value % len(statuses)],
                "created_at": created_at,
                "deleted_at": (created_at + timedelta(days=1) if value % 11 == 0 else None),
            }
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, owner_id, name, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:workspace_id, :owner_id, :name, 'active', :created_at, "
                ":created_at, NULL)"
            ),
            workspaces,
        )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(asset_id, workspace_id, owner_id, asset_type, "
                "selected_asset_version_id, lifecycle_status, created_at, "
                "updated_at, deleted_at) VALUES "
                "(:asset_id, :workspace_id, :owner_id, :asset_type, NULL, "
                ":lifecycle_status, :created_at, :created_at, :deleted_at)"
            ),
            assets,
        )
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _queries() -> dict[str, tuple[str, tuple[object, ...], str]]:
    columns = (
        "asset_id, workspace_id, owner_id, asset_type, lifecycle_status, created_at, deleted_at"
    )
    owner_id = _identifier(1, 1)
    workspace_id = _identifier(2, 1)
    anchor_time = "2026-08-06 12:00:00.000000"
    anchor_id = _identifier(3, 3_000)
    owner_index = "ix_assets_owner_active_keyset"
    workspace_index = "ix_assets_owner_workspace_active_keyset"
    return {
        "owner_first": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? "
            "AND deleted_at IS NULL "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id,),
            owner_index,
        ),
        "owner_after": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? "
            "AND deleted_at IS NULL AND "
            "(created_at < ? OR (created_at = ? AND asset_id < ?)) "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, anchor_time, anchor_time, anchor_id),
            owner_index,
        ),
        "owner_type_first": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? "
            "AND asset_type = 'music' AND deleted_at IS NULL "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id,),
            owner_index,
        ),
        "owner_type_after": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? "
            "AND asset_type = 'music' AND deleted_at IS NULL AND "
            "(created_at < ? OR (created_at = ? AND asset_id < ?)) "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, anchor_time, anchor_time, anchor_id),
            owner_index,
        ),
        "workspace_first": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? AND workspace_id = ? "
            "AND deleted_at IS NULL "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, workspace_id),
            workspace_index,
        ),
        "workspace_after": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? AND workspace_id = ? "
            "AND deleted_at IS NULL AND "
            "(created_at < ? OR (created_at = ? AND asset_id < ?)) "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, workspace_id, anchor_time, anchor_time, anchor_id),
            workspace_index,
        ),
        "workspace_type_first": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? AND workspace_id = ? "
            "AND asset_type = 'music' AND deleted_at IS NULL "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, workspace_id),
            workspace_index,
        ),
        "workspace_type_after": (
            f"SELECT {columns} FROM assets WHERE owner_id = ? AND workspace_id = ? "
            "AND asset_type = 'music' AND deleted_at IS NULL AND "
            "(created_at < ? OR (created_at = ? AND asset_id < ?)) "
            "ORDER BY created_at DESC, asset_id DESC LIMIT 26",
            (owner_id, workspace_id, anchor_time, anchor_time, anchor_id),
            workspace_index,
        ),
    }


def _plans_and_rows(database_url: str):
    engine = create_database_engine(database_url)
    plans: dict[str, str] = {}
    rows: dict[str, list[tuple[object, ...]]] = {}
    with engine.connect() as connection:
        for name, (query, parameters, _) in _queries().items():
            plans[name] = " | ".join(
                row[3]
                for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {query}", parameters)
            )
            rows[name] = list(connection.exec_driver_sql(query, parameters))
    engine.dispose()
    return plans, rows


def _create_candidates(database_url: str, *, partial: bool) -> None:
    suffix = "partial" if partial else "full"
    predicate = " WHERE deleted_at IS NULL" if partial else ""
    owner_columns = (
        "owner_id, created_at, asset_id"
        if partial
        else "owner_id, deleted_at, created_at, asset_id"
    )
    workspace_columns = (
        "owner_id, workspace_id, created_at, asset_id"
        if partial
        else "owner_id, workspace_id, deleted_at, created_at, asset_id"
    )
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE INDEX candidate_owner_{suffix} ON assets ({owner_columns}){predicate}"
        )
        connection.exec_driver_sql(
            f"CREATE INDEX candidate_workspace_{suffix} ON assets ({workspace_columns}){predicate}"
        )
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _drop_candidates(database_url: str, *, partial: bool) -> None:
    suffix = "partial" if partial else "full"
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX candidate_workspace_{suffix}")
        connection.exec_driver_sql(f"DROP INDEX candidate_owner_{suffix}")
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _database_contract(database_url: str) -> dict[str, object]:
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        table_names = sorted(
            name for name in inspect(connection).get_table_names() if name != "alembic_version"
        )
        rows = connection.exec_driver_sql(
            "SELECT asset_id, workspace_id, owner_id, asset_type, "
            "lifecycle_status, created_at, deleted_at FROM assets "
            "ORDER BY asset_id"
        ).all()
        digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
        result = {
            "tables": tuple(table_names),
            "asset_count": len(rows),
            "asset_digest": digest,
            "quick_check": connection.exec_driver_sql("PRAGMA quick_check").scalar_one(),
            "integrity_check": connection.exec_driver_sql("PRAGMA integrity_check").scalar_one(),
            "foreign_key_check": connection.exec_driver_sql("PRAGMA foreign_key_check").all(),
        }
    engine.dispose()
    return result


def test_asset_index_metadata_matches_contract() -> None:
    table = Base.metadata.tables["assets"]
    actual = {
        index.name: tuple(expression.name for expression in index.expressions)
        for index in table.indexes
        if index.name in INDEXES
    }
    assert actual == INDEXES
    for index in table.indexes:
        if index.name in INDEXES:
            assert index.dialect_options["sqlite"]["where"] is None


def test_asset_index_candidates_query_plan_and_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "asset-keyset-indexes.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_fixtures(database_url)
    baseline_contract = _database_contract(database_url)
    baseline_plans, baseline_rows = _plans_and_rows(database_url)
    assert len(baseline_contract["tables"]) == 35
    assert all("USE TEMP B-TREE FOR ORDER BY" in plan for plan in baseline_plans.values())

    _create_candidates(database_url, partial=False)
    full_plans, full_rows = _plans_and_rows(database_url)
    assert all("candidate_" in plan for plan in full_plans.values())
    assert all("_full" in plan for plan in full_plans.values())
    assert all("USE TEMP B-TREE FOR ORDER BY" not in plan for plan in full_plans.values())
    assert full_rows == baseline_rows
    _drop_candidates(database_url, partial=False)

    _create_candidates(database_url, partial=True)
    partial_plans, partial_rows = _plans_and_rows(database_url)
    assert all("USE TEMP B-TREE FOR ORDER BY" in plan for plan in partial_plans.values())
    assert partial_rows == baseline_rows
    _drop_candidates(database_url, partial=True)

    command.upgrade(config, REVISION)
    upgraded_contract = _database_contract(database_url)
    upgraded_plans, upgraded_rows = _plans_and_rows(database_url)
    assert upgraded_contract == baseline_contract
    assert upgraded_rows == baseline_rows
    for name, plan in upgraded_plans.items():
        expected_index = _queries()[name][2]
        assert expected_index in plan
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan
        assert "SCAN assets" not in plan
        keys = [(row[5], row[0]) for row in upgraded_rows[name]]
        assert keys == sorted(keys, reverse=True)
        assert len(keys) == len(set(keys))

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        reflected = {
            index["name"]: tuple(index["column_names"])
            for index in inspect(connection).get_indexes("assets")
            if index["name"] in INDEXES
        }
        assert reflected == INDEXES
        assert all(
            index["dialect_options"].get("sqlite_where") is None
            for index in inspect(connection).get_indexes("assets")
            if index["name"] in INDEXES
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    downgraded_contract = _database_contract(database_url)
    assert downgraded_contract == baseline_contract
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        remaining = {index["name"] for index in inspect(connection).get_indexes("assets")}
        assert not INDEXES.keys() & remaining
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )
    engine.dispose()
