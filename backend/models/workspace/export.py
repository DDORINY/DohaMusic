"""Canonical immutable export result authority."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base
from backend.models.workspace.mixins import CreatedAtMixin, TimestampMixin


class ExportPublicationState(StrEnum):
    INTENDED = "intended"
    PUBLISHED = "published"
    COMPLETED = "completed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class JobExportPublication(TimestampMixin, Base):
    """Durable physical-publication authority for one Export Job."""

    __tablename__ = "job_export_publications"
    __table_args__ = (
        CheckConstraint(
            "(expected_sha256 IS NULL AND expected_size_bytes IS NULL) OR "
            "(expected_sha256 IS NOT NULL AND expected_size_bytes IS NOT NULL)",
            name="ck_job_export_publications_integrity_pair",
        ),
        CheckConstraint(
            "expected_size_bytes IS NULL OR expected_size_bytes >= 0",
            name="ck_job_export_publications_size_nonnegative",
        ),
        CheckConstraint("version >= 0", name="ck_job_export_publications_version_nonnegative"),
    )

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="RESTRICT"), primary_key=True
    )
    composition_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("composition_snapshots.composition_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    export_format: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_domain: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    state: Mapped[ExportPublicationState] = mapped_column(
        SAEnum(
            ExportPublicationState,
            name="export_publication_state",
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class JobExportResult(CreatedAtMixin, Base):
    __tablename__ = "job_export_results"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="RESTRICT"), primary_key=True
    )
    composition_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("composition_snapshots.composition_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    export_format: Mapped[str] = mapped_column(String(16), nullable=False)
    render_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    exported_asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.asset_version_id", ondelete="RESTRICT"), nullable=False
    )
    exported_artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    integrated_loudness_lufs: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    true_peak_dbtp: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    target_lufs: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    minimum_lufs: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    maximum_lufs: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    maximum_true_peak_dbtp: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    loudness_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    true_peak_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    overall_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    analyzer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
