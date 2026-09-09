import numpy as np
import pytest

from backend.audio.composition_mixer import TrackMixerState, mix_tracks


def test_stereo_center_is_exact_and_master_gain_follows_track_gain() -> None:
    samples = np.array([[0.25, -0.5]], dtype=np.float64)
    result = mix_tracks([(samples, TrackMixerState(gain_db=6.0))], master_gain_db=-6.0)
    np.testing.assert_allclose(result, samples, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize(("pan", "expected"), [(-1.0, [2.0, 0.0]), (1.0, [0.0, 2.0])])
def test_stereo_pan_uses_w3c_cross_feed(pan: float, expected: list[float]) -> None:
    result = mix_tracks([(np.array([[1.0, 1.0]]), TrackMixerState(pan=pan))])
    np.testing.assert_allclose(result[0], expected, atol=1e-12)


def test_mono_pan_is_equal_power_without_downmix() -> None:
    result = mix_tracks([(np.array([[1.0]]), TrackMixerState(pan=0.0))])
    np.testing.assert_allclose(result[0], [2**-0.5, 2**-0.5], atol=1e-12)


def test_mute_wins_over_solo_and_multiple_solo_tracks_sum() -> None:
    one = np.array([[1.0, 0.0]])
    result = mix_tracks(
        [
            (one, TrackMixerState(solo=True)),
            (one, TrackMixerState(solo=True, muted=True)),
            (one, TrackMixerState()),
        ]
    )
    np.testing.assert_allclose(result, one)


def test_pan_rejects_nonfinite_or_out_of_range() -> None:
    for pan in (-1.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="TRACK_PAN_INVALID"):
            TrackMixerState(pan=pan)
