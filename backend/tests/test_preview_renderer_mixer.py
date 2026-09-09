import io
import shutil
import struct
import wave
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.audio.working_preview_renderer import (
    FfmpegWorkingCompositionPreviewRenderer,
    PreviewRenderClip,
    PreviewRenderError,
    PreviewRenderTrack,
)

RATE = 48_000


def _wav(level: int = 4_000) -> bytes:
    payload = io.BytesIO()
    with wave.open(payload, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(struct.pack("<hh", level, level) * 4_800)
    return payload.getvalue()


def _render(
    tmp_path: Path,
    states: list[PreviewRenderTrack],
    *,
    levels: list[int] | None = None,
    master_gain_db: Decimal = Decimal("0"),
) -> tuple[int, ...]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    levels = levels or [4_000] * len(states)
    artifacts = {uuid4(): _wav(level) for level in levels}

    @contextmanager
    def open_artifact(artifact_id: UUID):
        payload = artifacts[artifact_id]
        yield len(payload), io.BytesIO(payload)

    clips = [
        PreviewRenderClip(
            clip_id=uuid4(),
            track_order=index,
            canonical_order=index,
            artifact_id=artifact_id,
            source_in_us=0,
            source_out_us=100_000,
            timeline_start_us=0,
            timeline_duration_us=100_000,
            manifest_schema=5,
        )
        for index, artifact_id in enumerate(artifacts)
    ]
    renderer = FfmpegWorkingCompositionPreviewRenderer(
        ffmpeg_executable=ffmpeg,
        temp_root=tmp_path / str(uuid4()),
        open_artifact=open_artifact,
    )
    with (
        renderer.render(
            clips,
            track_count=len(states),
            tracks=states,
            master_gain_db=master_gain_db,
        ) as rendered,
        wave.open(str(rendered.path), "rb") as output,
    ):
        frames = output.readframes(output.getnframes())
    return struct.unpack(f"<{len(frames) // 2}h", frames)


def _track(
    order: int,
    *,
    gain: str = "0",
    pan: str = "0",
    muted: bool = False,
    solo: bool = False,
) -> PreviewRenderTrack:
    return PreviewRenderTrack(order, Decimal(gain), Decimal(pan), muted, solo)


@pytest.mark.parametrize(("gain", "factor"), [("0", 1.0), ("-6.02", 0.5), ("6.02", 2.0)])
def test_schema_five_applies_frozen_track_gain(tmp_path: Path, gain: str, factor: float) -> None:
    samples = _render(tmp_path, [_track(0, gain=gain)])
    assert max(samples) == pytest.approx(4_000 * factor, rel=0.03)


@pytest.mark.parametrize(
    ("pan", "left", "right"), [("-1", 8_000, 0), ("0", 4_000, 4_000), ("1", 0, 8_000)]
)
def test_schema_five_uses_canonical_stereo_pan(
    tmp_path: Path, pan: str, left: int, right: int
) -> None:
    samples = _render(tmp_path, [_track(0, pan=pan)])
    assert samples[0] == pytest.approx(left, abs=2)
    assert samples[1] == pytest.approx(right, abs=2)


@pytest.mark.parametrize(
    "states",
    [
        [_track(0, muted=True)],
        [_track(0), _track(1, solo=True)],
        [_track(0, solo=True), _track(1, solo=True, muted=True)],
    ],
)
def test_mute_wins_and_solo_filters_the_complete_track_set(
    tmp_path: Path, states: list[PreviewRenderTrack]
) -> None:
    samples = _render(tmp_path, states, levels=[2_000] * len(states))
    expected = 0 if len(states) == 1 else 2_000
    assert max(samples) == pytest.approx(expected, abs=2)


@pytest.mark.parametrize(("gain", "factor"), [("0", 1.0), ("-6.02", 0.5), ("6.02", 2.0)])
def test_master_gain_is_a_post_sum_stage(tmp_path: Path, gain: str, factor: float) -> None:
    samples = _render(
        tmp_path,
        [_track(0), _track(1)],
        levels=[1_000, 1_000],
        master_gain_db=Decimal(gain),
    )
    assert max(samples) == pytest.approx(2_000 * factor, rel=0.03)


def test_track_and_master_gain_are_each_applied_once(tmp_path: Path) -> None:
    samples = _render(
        tmp_path,
        [_track(0, gain="6.02")],
        levels=[1_000],
        master_gain_db=Decimal("6.02"),
    )
    assert max(samples) == pytest.approx(4_000, rel=0.03)


def test_same_frozen_manifest_renders_deterministically(tmp_path: Path) -> None:
    state = [_track(0, gain="-3", pan="0.25")]
    assert _render(tmp_path, state) == _render(tmp_path, state)


@pytest.mark.parametrize(
    "state,master",
    [
        (_track(0, gain="25"), Decimal("0")),
        (_track(0, pan="1.1"), Decimal("0")),
        (_track(0), Decimal("NaN")),
    ],
)
def test_invalid_frozen_mixer_values_use_preview_schema_error(
    tmp_path: Path, state: PreviewRenderTrack, master: Decimal
) -> None:
    with pytest.raises(PreviewRenderError, match="WORKING_PREVIEW_SCHEMA_INVALID"):
        _render(tmp_path, [state], master_gain_db=master)


def test_schema_five_requires_frozen_mixer_state(tmp_path: Path) -> None:
    @contextmanager
    def unopened(_: UUID):
        raise AssertionError("manifest validation must precede media access")
        yield 0, io.BytesIO()

    renderer = FfmpegWorkingCompositionPreviewRenderer(
        ffmpeg_executable="ffmpeg", temp_root=tmp_path, open_artifact=unopened
    )
    clip = PreviewRenderClip(
        uuid4(), 0, 0, uuid4(), 0, 1, 0, timeline_duration_us=1, manifest_schema=5
    )
    with (
        pytest.raises(PreviewRenderError, match="WORKING_PREVIEW_SCHEMA_INVALID"),
        renderer.render([clip], track_count=1),
    ):
        pass


def test_legacy_schema_four_explicitly_uses_unity_mixer_defaults(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    payload = _wav()

    @contextmanager
    def open_artifact(_: UUID):
        yield len(payload), io.BytesIO(payload)

    renderer = FfmpegWorkingCompositionPreviewRenderer(
        ffmpeg_executable=ffmpeg, temp_root=tmp_path, open_artifact=open_artifact
    )
    clip = PreviewRenderClip(uuid4(), 0, 0, uuid4(), 0, 100_000, 0, 100_000, manifest_schema=4)
    with renderer.render([clip], track_count=1) as rendered:
        assert rendered.size_bytes > 0
