import math

import pytest

from backend.audio.export_quality import evaluate_export_quality


@pytest.mark.parametrize("loudness", [-15.0, -14.0, -13.0])
def test_loudness_inclusive_boundaries_pass(loudness: float) -> None:
    assert evaluate_export_quality(
        integrated_loudness_lufs=loudness, true_peak_dbtp=-1.0
    ).overall_pass


@pytest.mark.parametrize("loudness", [-15.01, -12.99])
def test_loudness_outside_target_is_diagnostic(loudness: float) -> None:
    assert not evaluate_export_quality(
        integrated_loudness_lufs=loudness, true_peak_dbtp=-1.0
    ).overall_pass


def test_true_peak_gate_is_inclusive() -> None:
    assert evaluate_export_quality(integrated_loudness_lufs=-14.0, true_peak_dbtp=-1.0).overall_pass
    assert not evaluate_export_quality(
        integrated_loudness_lufs=-14.0, true_peak_dbtp=-0.99
    ).overall_pass


def test_exact_silence_uses_nullable_diagnostic_metrics() -> None:
    decision = evaluate_export_quality(integrated_loudness_lufs=None, true_peak_dbtp=None)
    assert decision.integrated_loudness_lufs is None
    assert decision.true_peak_dbtp is None
    assert not decision.loudness_passed
    assert decision.true_peak_passed
    assert not decision.overall_pass


@pytest.mark.parametrize("value", [-math.inf, math.inf, math.nan])
def test_non_silent_invalid_analysis_fails_closed(value: float) -> None:
    with pytest.raises(ValueError, match="EXPORT_QUALITY_ANALYSIS_FAILED"):
        evaluate_export_quality(integrated_loudness_lufs=value, true_peak_dbtp=-1.0)


def test_partial_nullable_metrics_fail_closed() -> None:
    with pytest.raises(ValueError, match="EXPORT_QUALITY_ANALYSIS_FAILED"):
        evaluate_export_quality(integrated_loudness_lufs=None, true_peak_dbtp=-1.0)
