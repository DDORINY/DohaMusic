"""Artifact Storage Catalog Entity와 additive Migration 계약 검증."""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.db.session import create_database_engine
from backend.models.workspace import (
    ARTIFACT_STORAGE_ENTITY_CLASSES,
    WORKSPACE_ENTITY_CLASSES,
    Artifact,
    ArtifactStorageLocation,
    Asset,
    AssetType,
    AssetVersion,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
    ROOT / "backend" / "alembic" / "versions" / "20260809_0016_add_artifact_storage_locations.py"
)
REVISION = "20260809_0016"
PREVIOUS_REVISION = "20260808_0015"
TABLE = "artifact_storage_locations"
LEGACY_TABLE_COUNT = 14
WORKSPACE_TABLE_COUNT = 35


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _revision_assignment(name: str) -> str:
    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )
    assert assignment.value is not None
    assert isinstance(assignment.value, ast.Constant)
    assert isinstance(assignment.value.value, str)
    return assignment.value.value


def _existing_snapshot(engine) -> tuple[dict[str, int], str]:
    counts: dict[str, int] = {}
    digest_rows: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    with engine.connect() as connection:
        inspector = inspect(connection)
        table_names = sorted(set(inspector.get_table_names()) - {TABLE, "alembic_version"})
        for table_name in table_names:
            columns = [column["name"] for column in inspector.get_columns(table_name)]
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            counts[table_name] = connection.exec_driver_sql(
                f'SELECT count(*) FROM "{table_name}"'
            ).scalar_one()
            primary_keys = inspector.get_pk_constraint(table_name)["constrained_columns"]
            query = f'SELECT {quoted_columns} FROM "{table_name}"'
            if primary_keys:
                query += " ORDER BY " + ", ".join(f'"{column}"' for column in primary_keys)
            rows = tuple(tuple(row) for row in connection.exec_driver_sql(query).all())
            digest_rows.append((table_name, rows))
    digest = hashlib.sha256(repr(digest_rows).encode("utf-8")).hexdigest()
    return counts, digest


def _seed_artifact(engine) -> tuple[UUID, UUID, UUID]:
    owner_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()
    artifact_id = uuid4()
    with Session(engine) as session:
        session.add(
            Asset(
                asset_id=asset_id,
                workspace_id=None,
                owner_id=owner_id,
                asset_type=AssetType.MUSIC,
                selected_asset_version_id=None,
                lifecycle_status="active",
            )
        )
        session.flush()
        session.add(
            AssetVersion(
                asset_version_id=version_id,
                asset_id=asset_id,
                version_number=1,
                version_origin="imported",
                settings_snapshot={},
                created_by=owner_id,
            )
        )
        session.flush()
        # 이 테스트는 historical 0015/0016 schema를 검증한다. 현재 Artifact ORM의
        # 0021 column을 historical table에 암묵적으로 INSERT하지 않는다.
        session.execute(
            insert(Artifact.__table__).values(
                artifact_id=artifact_id,
                asset_version_id=version_id,
                artifact_kind="audio",
                media_type="audio/wav",
                size_bytes=4,
                checksum_algorithm="sha256",
                artifact_checksum="0" * 64,
                producer_type="import",
                retention_status="active",
            )
        )
        session.commit()
    return asset_id, version_id, artifact_id


def _new_location(
    artifact_id: UUID,
    *,
    storage_backend: str = "local",
    storage_domain: str = "audio",
    storage_key: str = "runs/run-1/artifact.wav",
    locator_version: int = 1,
) -> ArtifactStorageLocation:
    return ArtifactStorageLocation(
        artifact_id=artifact_id,
        storage_backend=storage_backend,
        storage_domain=storage_domain,
        storage_key=storage_key,
        locator_version=locator_version,
        published_at=datetime.now(UTC),
    )


def test_catalog_entity_contract_and_relationship_are_exact() -> None:
    configure_mappers()

    assert (ArtifactStorageLocation,) == ARTIFACT_STORAGE_ENTITY_CLASSES
    assert len(WORKSPACE_ENTITY_CLASSES) == WORKSPACE_TABLE_COUNT
    assert ArtifactStorageLocation.__tablename__ == TABLE
    assert set(ArtifactStorageLocation.__table__.columns.keys()) == {
        "storage_location_id",
        "artifact_id",
        "storage_backend",
        "storage_domain",
        "storage_key",
        "locator_version",
        "published_at",
        "created_at",
    }
    assert not any("path" in column.name for column in ArtifactStorageLocation.__table__.columns)
    assert ArtifactStorageLocation.artifact.property.back_populates == "storage_location"
    assert Artifact.storage_location.property.back_populates == "artifact"
    assert Artifact.storage_location.property.uselist is False


def test_catalog_metadata_constraints_match_contract() -> None:
    table = ArtifactStorageLocation.__table__
    foreign_key = next(iter(table.foreign_keys))
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert len(Base.metadata.tables) == 52
    assert foreign_key.target_fullname == "artifacts.artifact_id"
    assert foreign_key.ondelete == "RESTRICT"
    assert unique_constraints == {
        "uq_artifact_storage_locations_artifact": ("artifact_id",),
        "uq_artifact_storage_locations_locator": (
            "storage_backend",
            "storage_domain",
            "storage_key",
        ),
    }
    assert check_names == {
        "ck_artifact_storage_locations_backend_nonempty",
        "ck_artifact_storage_locations_domain",
        "ck_artifact_storage_locations_key_nonempty",
        "ck_artifact_storage_locations_locator_version",
    }
    assert table.indexes == set()


def test_catalog_revision_is_single_additive_table() -> None:
    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    operations = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    }

    assert _revision_assignment("revision") == REVISION
    assert _revision_assignment("down_revision") == PREVIOUS_REVISION
    assert operations == {"create_table", "drop_table"}


def test_catalog_migration_round_trip_preserves_existing_schema_and_rows(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'catalog-migration.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    asset_id, version_id, artifact_id = _seed_artifact(engine)
    baseline_counts, baseline_digest = _existing_snapshot(engine)

    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        reflected_columns = {column["name"] for column in inspector.get_columns(TABLE)}
        reflected_foreign_keys = inspector.get_foreign_keys(TABLE)
        reflected_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(TABLE)
        }
        reflected_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints(TABLE)
        }
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
        foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        quick_check = connection.exec_driver_sql("PRAGMA quick_check").scalar_one()
        integrity_check = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()

    assert revision == REVISION
    assert len(tables) == 36
    assert TABLE in tables
    assert reflected_columns == {
        "storage_location_id",
        "artifact_id",
        "storage_backend",
        "storage_domain",
        "storage_key",
        "locator_version",
        "published_at",
        "created_at",
    }
    assert len(reflected_foreign_keys) == 1
    assert reflected_foreign_keys[0]["constrained_columns"] == ["artifact_id"]
    assert reflected_foreign_keys[0]["referred_table"] == "artifacts"
    assert reflected_foreign_keys[0]["referred_columns"] == ["artifact_id"]
    assert reflected_uniques == {
        "uq_artifact_storage_locations_artifact": ("artifact_id",),
        "uq_artifact_storage_locations_locator": (
            "storage_backend",
            "storage_domain",
            "storage_key",
        ),
    }
    assert reflected_checks == {
        "ck_artifact_storage_locations_backend_nonempty",
        "ck_artifact_storage_locations_domain",
        "ck_artifact_storage_locations_key_nonempty",
        "ck_artifact_storage_locations_locator_version",
    }
    assert len(WORKSPACE_ENTITY_CLASSES) == WORKSPACE_TABLE_COUNT
    assert len(tables - {entity.__tablename__ for entity in WORKSPACE_ENTITY_CLASSES}) == (
        LEGACY_TABLE_COUNT + 1
    )
    assert _existing_snapshot(engine) == (baseline_counts, baseline_digest)
    assert foreign_key_violations == []
    assert quick_check == "ok"
    assert integrity_check == "ok"

    with Session(engine) as session:
        location = _new_location(artifact_id)
        session.add(location)
        session.commit()
        session.refresh(location)
        assert location.artifact_id == artifact_id
        lineage = session.execute(
            text(
                "SELECT av.asset_version_id, av.asset_id "
                "FROM artifacts AS a "
                "JOIN asset_versions AS av "
                "ON av.asset_version_id = a.asset_version_id "
                "WHERE a.artifact_id = :artifact_id"
            ),
            {"artifact_id": artifact_id.hex},
        ).one()
        assert UUID(lineage.asset_version_id) == version_id
        assert UUID(lineage.asset_id) == asset_id

    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        inspector = inspect(connection)
        downgraded_tables = set(inspector.get_table_names()) - {"alembic_version"}
        downgraded_revision = connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()
        artifact_count = connection.execute(
            select(text("count(*)")).select_from(Base.metadata.tables["artifacts"])
        ).scalar_one()
        foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()

    assert downgraded_revision == PREVIOUS_REVISION
    assert len(downgraded_tables) == 35
    assert TABLE not in downgraded_tables
    assert artifact_count == 1
    assert foreign_key_violations == []


def test_catalog_rejects_missing_artifact_and_duplicate_authoritative_location(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'catalog-fk-unique.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)

    with Session(engine) as session:
        session.add(_new_location(uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    _, _, artifact_id = _seed_artifact(engine)
    with Session(engine) as session:
        session.add(_new_location(artifact_id))
        session.commit()
        session.add(
            _new_location(
                artifact_id,
                storage_key="runs/run-2/artifact.wav",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_catalog_rejects_duplicate_locator(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'catalog-locator-unique.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    _, _, first_artifact_id = _seed_artifact(engine)

    owner_id = uuid4()
    second_asset_id = uuid4()
    second_version_id = uuid4()
    second_artifact_id = uuid4()
    with Session(engine) as session:
        session.add(
            Asset(
                asset_id=second_asset_id,
                owner_id=owner_id,
                asset_type=AssetType.MUSIC,
                lifecycle_status="active",
            )
        )
        session.flush()
        session.add(
            AssetVersion(
                asset_version_id=second_version_id,
                asset_id=second_asset_id,
                version_number=1,
                version_origin="imported",
                settings_snapshot={},
                created_by=owner_id,
            )
        )
        session.flush()
        session.execute(
            insert(Artifact.__table__).values(
                artifact_id=second_artifact_id,
                asset_version_id=second_version_id,
                artifact_kind="audio",
                media_type="audio/wav",
                size_bytes=4,
                checksum_algorithm="sha256",
                artifact_checksum="1" * 64,
                producer_type="import",
                retention_status="active",
            )
        )
        session.commit()

        session.add(_new_location(first_artifact_id))
        session.add(_new_location(second_artifact_id))
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


@pytest.mark.parametrize(
    ("overrides", "expected_constraint"),
    [
        ({"storage_backend": ""}, "backend_nonempty"),
        ({"storage_domain": "unknown"}, "domain"),
        ({"storage_key": ""}, "key_nonempty"),
        ({"locator_version": 0}, "locator_version"),
    ],
)
def test_catalog_rejects_invalid_database_invariants(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_constraint: str,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'catalog-{expected_constraint}.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, REVISION)
    engine = create_database_engine(database_url)
    _, _, artifact_id = _seed_artifact(engine)

    values = {
        "storage_backend": "local",
        "storage_domain": "audio",
        "storage_key": "runs/run-1/artifact.wav",
        "locator_version": 1,
    }
    values.update(overrides)
    with Session(engine) as session:
        session.add(_new_location(artifact_id, **values))
        with pytest.raises(IntegrityError, match=expected_constraint):
            session.commit()
    engine.dispose()
