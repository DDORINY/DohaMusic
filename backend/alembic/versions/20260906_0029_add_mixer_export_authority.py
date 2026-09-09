"""Add mixer, typed history target, and export result authority.

Revision ID: 20260906_0029
Revises: 20260905_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0029"
down_revision: str | Sequence[str] | None = "20260905_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "working_compositions",
        sa.Column("master_gain_db", sa.Numeric(8, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "composition_snapshots",
        sa.Column("master_gain_db", sa.Numeric(8, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "working_preview_renders",
        sa.Column("master_gain_db", sa.Numeric(8, 4), nullable=False, server_default="0"),
    )
    for table in ("composition_tracks", "composition_snapshot_tracks"):
        op.add_column(
            table, sa.Column("gain_db", sa.Numeric(8, 4), nullable=False, server_default="0")
        )
        op.add_column(table, sa.Column("pan", sa.Numeric(8, 6), nullable=False, server_default="0"))
        op.add_column(
            table, sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        op.add_column(
            table, sa.Column("solo", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    for column in (
        sa.Column("gain_db", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("pan", sa.Numeric(8, 6), nullable=False, server_default="0"),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("solo", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("working_preview_render_tracks", column)
    with op.batch_alter_table("working_composition_history_entries") as batch:
        batch.add_column(sa.Column("target_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("target_id", sa.Uuid(), nullable=True))
        batch.alter_column("clip_id", existing_type=sa.Uuid(), nullable=True)
    op.execute(
        "UPDATE working_composition_history_entries SET target_type = 'CLIP', target_id = clip_id"
    )
    with op.batch_alter_table("working_composition_history_entries") as batch:
        batch.alter_column("target_type", existing_type=sa.String(32), nullable=False)
        batch.alter_column("target_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_check_constraint(
            "ck_working_history_target_type",
            "target_type IN ('CLIP', 'TRACK', 'WORKING_COMPOSITION')",
        )
    op.create_table(
        "job_export_results",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("composition_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("export_format", sa.String(16), nullable=False),
        sa.Column("render_fingerprint", sa.String(64), nullable=False),
        sa.Column("exported_asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("exported_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("integrated_loudness_lufs", sa.Numeric(8, 2), nullable=False),
        sa.Column("true_peak_dbtp", sa.Numeric(8, 2), nullable=False),
        sa.Column("target_lufs", sa.Numeric(8, 2), nullable=False),
        sa.Column("minimum_lufs", sa.Numeric(8, 2), nullable=False),
        sa.Column("maximum_lufs", sa.Numeric(8, 2), nullable=False),
        sa.Column("maximum_true_peak_dbtp", sa.Numeric(8, 2), nullable=False),
        sa.Column("loudness_passed", sa.Boolean(), nullable=False),
        sa.Column("true_peak_passed", sa.Boolean(), nullable=False),
        sa.Column("overall_pass", sa.Boolean(), nullable=False),
        sa.Column("analyzer_name", sa.String(64), nullable=False),
        sa.Column("analyzer_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["composition_snapshot_id"],
            ["composition_snapshots.composition_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exported_asset_version_id"], ["asset_versions.asset_version_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["exported_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_job_export_results_snapshot", "job_export_results", ["composition_snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_export_results_snapshot", table_name="job_export_results")
    op.drop_table("job_export_results")
    for column in ("solo", "muted", "pan", "gain_db"):
        op.drop_column("working_preview_render_tracks", column)
    op.drop_column("working_preview_renders", "master_gain_db")
    with op.batch_alter_table("working_composition_history_entries") as batch:
        batch.drop_constraint("ck_working_history_target_type", type_="check")
        batch.drop_column("target_id")
        batch.drop_column("target_type")
        batch.alter_column("clip_id", existing_type=sa.Uuid(), nullable=False)
    for table in ("composition_snapshot_tracks", "composition_tracks"):
        for column in ("solo", "muted", "pan", "gain_db"):
            op.drop_column(table, column)
    op.drop_column("composition_snapshots", "master_gain_db")
    op.drop_column("working_compositions", "master_gain_db")
