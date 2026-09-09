"""Deterministic EBU-R128-compatible Export quality analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
from scipy.io import wavfile
from scipy.signal import resample_poly

from backend.audio.export_quality import ExportQualityDecision, evaluate_export_quality

EXPORT_ANALYZER_NAME = "dohamusic-ebur128"
EXPORT_ANALYZER_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ExportAnalysis:
    quality: ExportQualityDecision
    sample_rate: int
    channels: int
    frame_count: int


class ExportAnalysisError(RuntimeError):
    pass


class CanonicalWavExportAnalyzer:
    """Measure LUFS-I and 4x oversampled true peak from canonical PCM WAV."""

    def analyze(self, path: Path) -> ExportAnalysis:
        try:
            sample_rate, raw = wavfile.read(path)
        except (OSError, ValueError):
            raise ExportAnalysisError("EXPORT_QUALITY_ANALYSIS_FAILED") from None
        if sample_rate != 48_000 or raw.ndim != 2 or raw.shape[1] != 2 or raw.size == 0:
            raise ExportAnalysisError("EXPORT_QUALITY_ANALYSIS_FAILED")
        if not np.issubdtype(raw.dtype, np.signedinteger) or raw.dtype.itemsize != 2:
            raise ExportAnalysisError("EXPORT_QUALITY_ANALYSIS_FAILED")
        samples = raw.astype(np.float64) / 32_768.0
        try:
            if np.count_nonzero(raw) == 0:
                quality = evaluate_export_quality(
                    integrated_loudness_lufs=None,
                    true_peak_dbtp=None,
                )
            else:
                loudness = float(pyln.Meter(sample_rate).integrated_loudness(samples))
                oversampled = resample_poly(samples, 4, 1, axis=0)
                peak = float(np.max(np.abs(oversampled)))
                true_peak = 20.0 * math.log10(peak) if peak > 0 else -math.inf
                quality = evaluate_export_quality(
                    integrated_loudness_lufs=loudness,
                    true_peak_dbtp=true_peak,
                )
        except (ArithmeticError, ValueError):
            raise ExportAnalysisError("EXPORT_QUALITY_ANALYSIS_FAILED") from None
        return ExportAnalysis(quality, int(sample_rate), 2, int(raw.shape[0]))
