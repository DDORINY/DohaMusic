"""Add durable Export publication ledger.

Revision ID: 20260907_0030
Revises: 20260906_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0030"
down_revision: str | Sequence[str] | None = "20260906_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_export_publications",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("composition_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("export_format", sa.String(16), nullable=False),
        sa.Column("storage_domain", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "intended",
                "published",
                "completed",
                "reconciliation_required",
                name="export_publication_state",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("expected_sha256", sa.String(64), nullable=True),
        sa.Column("expected_size_bytes", sa.Integer(), nullable=True),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(expected_sha256 IS NULL AND expected_size_bytes IS NULL) OR "
            "(expected_sha256 IS NOT NULL AND expected_size_bytes IS NOT NULL)",
            name="ck_job_export_publications_integrity_pair",
        ),
        sa.CheckConstraint(
            "expected_size_bytes IS NULL OR expected_size_bytes >= 0",
            name="ck_job_export_publications_size_nonnegative",
        ),
        sa.CheckConstraint("version >= 0", name="ck_job_export_publications_version_nonnegative"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["composition_snapshot_id"],
            ["composition_snapshots.composition_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("artifact_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_job_export_publications_snapshot",
        "job_export_publications",
        ["composition_snapshot_id"],
    )
    op.create_index("ix_job_export_publications_state", "job_export_publications", ["state"])


def downgrade() -> None:
    op.drop_index("ix_job_export_publications_state", table_name="job_export_publications")
    op.drop_index("ix_job_export_publications_snapshot", table_name="job_export_publications")
    op.drop_table("job_export_publications")
