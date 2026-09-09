"""Canonical Track/Master mixer DSP shared by Preview and Export."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, pi, sin

import numpy as np
from numpy.typing import NDArray

Pcm = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class TrackMixerState:
    gain_db: float = 0.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.gain_db):
            raise ValueError("TRACK_GAIN_INVALID")
        if not isfinite(self.pan) or not -1.0 <= self.pan <= 1.0:
            raise ValueError("TRACK_PAN_INVALID")


def db_to_linear(gain_db: float) -> float:
    if not isfinite(gain_db):
        raise ValueError("GAIN_INVALID")
    return 10.0 ** (gain_db / 20.0)


def stereo_pan_matrix(pan: float) -> tuple[float, float, float, float]:
    """Return W3C StereoPanner-compatible LL, LR, RL, RR coefficients."""
    if not isfinite(pan) or not -1.0 <= pan <= 1.0:
        raise ValueError("TRACK_PAN_INVALID")
    if pan <= 0.0:
        angle = (pan + 1.0) * (pi / 2.0)
        return 1.0, cos(angle), 0.0, sin(angle)
    angle = pan * (pi / 2.0)
    return cos(angle), 0.0, sin(angle), 1.0


def mix_tracks(tracks: list[tuple[Pcm, TrackMixerState]], *, master_gain_db: float = 0.0) -> Pcm:
    """Apply Solo/Mute, Track Gain/Pan, sum, then Master Gain."""
    if not isfinite(master_gain_db):
        raise ValueError("MASTER_GAIN_INVALID")
    if not tracks:
        return np.zeros((0, 2), dtype=np.float64)
    frame_count = max(samples.shape[0] for samples, _ in tracks)
    output = np.zeros((frame_count, 2), dtype=np.float64)
    any_solo = any(state.solo for _, state in tracks)
    for samples, state in tracks:
        if state.muted or (any_solo and not state.solo):
            continue
        mixed = _apply_gain_and_pan(samples, state)
        output[: mixed.shape[0]] += mixed
    output *= db_to_linear(master_gain_db)
    return output


def _apply_gain_and_pan(samples: Pcm, state: TrackMixerState) -> Pcm:
    if samples.ndim != 2 or samples.shape[1] not in (1, 2):
        raise ValueError("TRACK_PCM_CHANNELS_INVALID")
    gained = samples.astype(np.float64, copy=False) * db_to_linear(state.gain_db)
    pan = state.pan
    if gained.shape[1] == 1:
        angle = ((pan + 1.0) / 2.0) * (pi / 2.0)
        mono = gained[:, 0]
        return np.column_stack((mono * cos(angle), mono * sin(angle)))
    left, right = gained[:, 0], gained[:, 1]
    ll, lr, rl, rr = stereo_pan_matrix(pan)
    return np.column_stack((left * ll + right * lr, left * rl + right * rr))
