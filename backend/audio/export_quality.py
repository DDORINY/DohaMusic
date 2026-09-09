"""Immutable diagnostic Export quality policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

TARGET_LUFS = Decimal("-14.00")
MINIMUM_LUFS = Decimal("-15.00")
MAXIMUM_LUFS = Decimal("-13.00")
MAXIMUM_TRUE_PEAK_DBTP = Decimal("-1.00")
_PRECISION = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ExportQualityDecision:
    integrated_loudness_lufs: Decimal | None
    true_peak_dbtp: Decimal | None
    loudness_passed: bool
    true_peak_passed: bool

    @property
    def overall_pass(self) -> bool:
        return self.loudness_passed and self.true_peak_passed


def evaluate_export_quality(
    *, integrated_loudness_lufs: float | None, true_peak_dbtp: float | None
) -> ExportQualityDecision:
    if (integrated_loudness_lufs is None) != (true_peak_dbtp is None):
        raise ValueError("EXPORT_QUALITY_ANALYSIS_FAILED")
    if integrated_loudness_lufs is None:
        return ExportQualityDecision(None, None, False, True)
    if not isfinite(integrated_loudness_lufs) or not isfinite(true_peak_dbtp):
        raise ValueError("EXPORT_QUALITY_ANALYSIS_FAILED")
    loudness = Decimal(str(integrated_loudness_lufs)).quantize(_PRECISION, rounding=ROUND_HALF_UP)
    peak = Decimal(str(true_peak_dbtp)).quantize(_PRECISION, rounding=ROUND_HALF_UP)
    return ExportQualityDecision(
        integrated_loudness_lufs=loudness,
        true_peak_dbtp=peak,
        loudness_passed=MINIMUM_LUFS <= loudness <= MAXIMUM_LUFS,
        true_peak_passed=peak <= MAXIMUM_TRUE_PEAK_DBTP,
    )
