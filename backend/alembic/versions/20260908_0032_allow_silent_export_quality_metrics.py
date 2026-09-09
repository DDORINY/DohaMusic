"""Allow exact-silence Export quality metrics to remain NULL.

Revision ID: 20260908_0032
Revises: 20260907_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0032"
down_revision: str | Sequence[str] | None = "20260907_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_export_results") as batch:
        batch.alter_column(
            "integrated_loudness_lufs",
            existing_type=sa.Numeric(8, 2),
            nullable=True,
        )
        batch.alter_column(
            "true_peak_dbtp",
            existing_type=sa.Numeric(8, 2),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    null_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM job_export_results "
            "WHERE integrated_loudness_lufs IS NULL OR true_peak_dbtp IS NULL"
        )
    ).scalar_one()
    if null_count:
        raise RuntimeError("SILENT_EXPORT_QUALITY_DOWNGRADE_BLOCKED")
    with op.batch_alter_table("job_export_results") as batch:
        batch.alter_column(
            "integrated_loudness_lufs",
            existing_type=sa.Numeric(8, 2),
            nullable=False,
        )
        batch.alter_column(
            "true_peak_dbtp",
            existing_type=sa.Numeric(8, 2),
            nullable=False,
        )
