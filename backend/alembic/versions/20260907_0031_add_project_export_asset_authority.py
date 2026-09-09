"""Add Project-owned Export ProjectAsset authority.

Revision ID: 20260907_0031
Revises: 20260907_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0031"
down_revision: str | Sequence[str] | None = "20260907_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _run_sqlite_without_foreign_keys(_upgrade)
        return
    _upgrade()


def _upgrade() -> None:
    with op.batch_alter_table("project_assets") as batch:
        batch.create_unique_constraint(
            "uq_project_assets_project_identity",
            ["project_id", "project_asset_id"],
        )
    with op.batch_alter_table("music_projects") as batch:
        batch.add_column(sa.Column("export_project_asset_id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint(
            "uq_music_projects_export_project_asset",
            ["export_project_asset_id"],
        )
        batch.create_foreign_key(
            "fk_music_projects_export_project_asset",
            "project_assets",
            ["project_id", "export_project_asset_id"],
            ["project_id", "project_asset_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _run_sqlite_without_foreign_keys(_downgrade)
        return
    _downgrade()


def _downgrade() -> None:
    with op.batch_alter_table("music_projects") as batch:
        batch.drop_constraint("fk_music_projects_export_project_asset", type_="foreignkey")
        batch.drop_constraint("uq_music_projects_export_project_asset", type_="unique")
        batch.drop_column("export_project_asset_id")
    with op.batch_alter_table("project_assets") as batch:
        batch.drop_constraint("uq_project_assets_project_identity", type_="unique")


def _run_sqlite_without_foreign_keys(operation) -> None:
    connection = op.get_bind()
    with op.get_context().autocommit_block():
        foreign_keys_enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            operation()
        finally:
            if foreign_keys_enabled:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
