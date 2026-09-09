"""ProjectAsset keyset partial Index와 Query Plan 검증."""

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
REVISION = "20260807_0014"
PREVIOUS_REVISION = "20260807_0013"
SOURCE_HEAD = "20260908_0032"
INDEX_NAME = "ix_project_assets_active_keyset"
INDEX_COLUMNS = ("project_id", "display_order", "project_asset_id")
INDEX_PREDICATE = "deleted_at IS NULL"


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _identifier(namespace: int, value: int) -> str:
    return f"{namespace:02x}{value:030x}"


def _insert_fixtures(database_url: str) -> None:
    engine = create_database_engine(database_url)
    workspace_id = _identifier(1, 1)
    owner_id = _identifier(2, 1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(workspace_id, owner_id, name, lifecycle_status, updated_at, "
                "created_at, deleted_at) VALUES "
                "(:workspace_id, :owner_id, 'Workspace', 'active', "
                "'2026-08-07', '2026-08-07', NULL)"
            ),
            {"workspace_id": workspace_id, "owner_id": owner_id},
        )
        project_rows = [
            {
                "project_id": _identifier(3, project_number),
                "workspace_id": workspace_id,
                "title": f"Project {project_number}",
                "created_by": owner_id,
            }
            for project_number in range(1, 5)
        ]
        connection.execute(
            text(
                "INSERT INTO music_projects "
                "(project_id, workspace_id, title, description, lifecycle_status, "
                "created_by, updated_at, created_at, deleted_at) VALUES "
                "(:project_id, :workspace_id, :title, NULL, 'active', :created_by, "
                "'2026-08-07', '2026-08-07', NULL)"
            ),
            project_rows,
        )
        asset_rows = []
        link_rows = []
        for project_number in range(1, 5):
            project_id = _identifier(3, project_number)
            for item in range(1, 501):
                sequence = project_number * 1_000 + item
                asset_id = _identifier(4, sequence)
                asset_rows.append(
                    {
                        "asset_id": asset_id,
                        "workspace_id": workspace_id,
                        "owner_id": owner_id,
                    }
                )
                link_rows.append(
                    {
                        "project_asset_id": _identifier(5, sequence),
                        "project_id": project_id,
                        "asset_id": asset_id,
                        "display_order": item // 5,
                        "deleted_at": "2026-08-08" if item % 13 == 0 else None,
                    }
                )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(asset_id, workspace_id, owner_id, asset_type, "
                "selected_asset_version_id, lifecycle_status, updated_at, "
                "created_at, deleted_at) VALUES "
                "(:asset_id, :workspace_id, :owner_id, 'music', NULL, 'active', "
                "'2026-08-07', '2026-08-07', NULL)"
            ),
            asset_rows,
        )
        connection.execute(
            text(
                "INSERT INTO project_assets "
                "(project_asset_id, project_id, asset_id, role, display_order, "
                "created_at, deleted_at) VALUES "
                "(:project_asset_id, :project_id, :asset_id, 'music', "
                ":display_order, '2026-08-07', :deleted_at)"
            ),
            link_rows,
        )
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _queries() -> dict[str, tuple[str, tuple[object, ...]]]:
    columns = "project_asset_id, project_id, asset_id, role, display_order, created_at, deleted_at"
    project_id = _identifier(3, 2)
    anchor_id = _identifier(5, 2_250)
    return {
        "first": (
            (
                f"SELECT {columns} FROM project_assets "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY display_order ASC, project_asset_id ASC LIMIT 26"
            ),
            (project_id,),
        ),
        "after": (
            (
                f"SELECT {columns} FROM project_assets "
                "WHERE project_id = ? AND deleted_at IS NULL AND "
                "(display_order > ? OR "
                "(display_order = ? AND project_asset_id > ?)) "
                "ORDER BY display_order ASC, project_asset_id ASC LIMIT 26"
            ),
            (project_id, 50, 50, anchor_id),
        ),
    }


def _plans_and_rows(database_url: str):
    engine = create_database_engine(database_url)
    plans = {}
    rows = {}
    with engine.connect() as connection:
        for name, (query, parameters) in _queries().items():
            plans[name] = " | ".join(
                row[3]
                for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {query}", parameters)
            )
            rows[name] = list(connection.exec_driver_sql(query, parameters))
    engine.dispose()
    return plans, rows


def _create_candidate(database_url: str, *, partial: bool) -> None:
    engine = create_database_engine(database_url)
    name = "candidate_partial" if partial else "candidate_full"
    columns = (
        "project_id, display_order, project_asset_id"
        if partial
        else "project_id, deleted_at, display_order, project_asset_id"
    )
    predicate = " WHERE deleted_at IS NULL" if partial else ""
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE INDEX {name} ON project_assets ({columns}){predicate}")
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _drop_candidate(database_url: str, name: str) -> None:
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {name}")
        connection.exec_driver_sql("ANALYZE")
    engine.dispose()


def _schema_contract(database_url: str):
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        result = {
            "columns": tuple(
                (column["name"], column["nullable"])
                for column in inspector.get_columns("project_assets")
            ),
            "foreign_keys": tuple(
                sorted(
                    (
                        tuple(foreign_key["constrained_columns"]),
                        foreign_key["referred_table"],
                        tuple(foreign_key["referred_columns"]),
                    )
                    for foreign_key in inspector.get_foreign_keys("project_assets")
                )
            ),
            "unique": tuple(
                sorted(
                    tuple(constraint["column_names"])
                    for constraint in inspector.get_unique_constraints("project_assets")
                )
            ),
            "rows": connection.exec_driver_sql("SELECT count(*) FROM project_assets").scalar_one(),
        }
    engine.dispose()
    return result


def test_project_asset_index_metadata_matches_contract() -> None:
    table = Base.metadata.tables["project_assets"]
    index = next(item for item in table.indexes if item.name == INDEX_NAME)

    assert tuple(expression.name for expression in index.expressions) == INDEX_COLUMNS
    assert str(index.dialect_options["sqlite"]["where"]) == INDEX_PREDICATE


def test_project_asset_index_candidates_query_plan_and_round_trip(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "project-asset-keyset-index.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [SOURCE_HEAD]
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_fixtures(database_url)
    baseline_schema = _schema_contract(database_url)
    baseline_plans, baseline_rows = _plans_and_rows(database_url)
    assert all("USE TEMP B-TREE FOR ORDER BY" in plan for plan in baseline_plans.values())

    _create_candidate(database_url, partial=False)
    full_plans, full_rows = _plans_and_rows(database_url)
    assert all("candidate_full" in plan for plan in full_plans.values())
    assert all("USE TEMP B-TREE FOR ORDER BY" not in plan for plan in full_plans.values())
    assert full_rows == baseline_rows
    _drop_candidate(database_url, "candidate_full")

    _create_candidate(database_url, partial=True)
    partial_plans, partial_rows = _plans_and_rows(database_url)
    assert all("candidate_partial" in plan for plan in partial_plans.values())
    assert all("USE TEMP B-TREE FOR ORDER BY" not in plan for plan in partial_plans.values())
    assert partial_rows == baseline_rows
    _drop_candidate(database_url, "candidate_partial")

    command.upgrade(config, REVISION)
    upgraded_plans, upgraded_rows = _plans_and_rows(database_url)
    assert all(INDEX_NAME in plan for plan in upgraded_plans.values())
    assert all("USE TEMP B-TREE FOR ORDER BY" not in plan for plan in upgraded_plans.values())
    assert upgraded_rows == baseline_rows
    for rows in upgraded_rows.values():
        keys = [(row[4], row[0]) for row in rows]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        index = next(
            item
            for item in inspect(connection).get_indexes("project_assets")
            if item["name"] == INDEX_NAME
        )
        assert tuple(index["column_names"]) == INDEX_COLUMNS
        assert str(index["dialect_options"]["sqlite_where"]) == INDEX_PREDICATE
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    downgraded_schema = _schema_contract(database_url)
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        index_names = {item["name"] for item in inspect(connection).get_indexes("project_assets")}
        assert INDEX_NAME not in index_names
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )
        assert connection.exec_driver_sql("PRAGMA quick_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

    assert downgraded_schema == baseline_schema
