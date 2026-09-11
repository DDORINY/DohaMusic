from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.audio.export_delivery_validator import (
    ExportDeliveryFormat,
    ExportDeliveryValidationError,
    ExportDeliveryValidationErrorCode,
    ExportDeliveryValidator,
)
from backend.storage.artifact_media import validate_artifact_media

FFMPEG = shutil.which("ffmpeg")


def _require_ffmpeg() -> str:
    if FFMPEG is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg/FFprobe runtime is unavailable")
    return FFMPEG


def _encode(path: Path, *, rate: int = 48_000, channels: int = 2, duration: int = 1) -> None:
    codec = {".wav": "pcm_s16le", ".mp3": "libmp3lame", ".flac": "flac"}[path.suffix]
    command = [
        _require_ffmpeg(),
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={rate}:duration={duration}",
        "-ar",
        str(rate),
        "-ac",
        str(channels),
        "-c:a",
        codec,
    ]
    if path.suffix == ".mp3":
        command.extend(["-b:a", "320k"])
    if path.suffix == ".flac":
        command.extend(["-sample_fmt", "s16"])
    subprocess.run([*command, str(path)], check=True, capture_output=True)


@pytest.fixture
def validator() -> ExportDeliveryValidator:
    return ExportDeliveryValidator(ffmpeg_executable=_require_ffmpeg())


@pytest.mark.parametrize(
    ("suffix", "export_format", "media_type", "codec", "bit_depth"),
    [
        (".wav", ExportDeliveryFormat.WAV, "audio/wav", "pcm_s16le", 16),
        (".mp3", ExportDeliveryFormat.MP3, "audio/mpeg", "mp3", None),
        (".flac", ExportDeliveryFormat.FLAC, "audio/flac", "flac", 16),
    ],
)
def test_valid_delivery_is_fully_decoded(
    tmp_path: Path,
    validator: ExportDeliveryValidator,
    suffix: str,
    export_format: ExportDeliveryFormat,
    media_type: str,
    codec: str,
    bit_depth: int | None,
) -> None:
    payload = tmp_path / f"delivery{suffix}"
    _encode(payload)

    result = validator.validate(
        payload, expected_format=export_format, expected_duration_us=1_000_000
    )

    assert result.media_type == media_type
    assert result.codec == codec
    assert result.sample_rate == 48_000
    assert result.channels == 2
    assert result.bit_depth == bit_depth
    assert result.full_decode_verified is True
    payload.rename(tmp_path / f"released{suffix}")


@pytest.mark.parametrize(
    ("rate", "channels", "code"),
    [
        (44_100, 2, ExportDeliveryValidationErrorCode.SAMPLE_RATE_MISMATCH),
        (48_000, 1, ExportDeliveryValidationErrorCode.CHANNEL_MISMATCH),
    ],
)
def test_mp3_rejects_wrong_canonical_stream_properties(
    tmp_path: Path,
    validator: ExportDeliveryValidator,
    rate: int,
    channels: int,
    code: ExportDeliveryValidationErrorCode,
) -> None:
    payload = tmp_path / "delivery.mp3"
    _encode(payload, rate=rate, channels=channels)

    with pytest.raises(ExportDeliveryValidationError) as caught:
        validator.validate(
            payload, expected_format=ExportDeliveryFormat.MP3, expected_duration_us=1_000_000
        )

    assert caught.value.code is code


def test_expected_format_rejects_container_spoof(
    tmp_path: Path, validator: ExportDeliveryValidator
) -> None:
    payload = tmp_path / "renamed.mp3"
    _encode(tmp_path / "source.flac")
    payload.write_bytes((tmp_path / "source.flac").read_bytes())

    with pytest.raises(ExportDeliveryValidationError) as caught:
        validator.validate(
            payload, expected_format=ExportDeliveryFormat.MP3, expected_duration_us=1_000_000
        )

    assert caught.value.code is ExportDeliveryValidationErrorCode.FORMAT_MISMATCH


def test_first_frame_recognition_does_not_trust_truncated_mp3(
    tmp_path: Path, validator: ExportDeliveryValidator
) -> None:
    complete = tmp_path / "complete.mp3"
    truncated = tmp_path / "truncated.mp3"
    _encode(complete)
    payload = complete.read_bytes()
    truncated.write_bytes(payload[: len(payload) // 2])
    generic = validate_artifact_media(
        truncated, artifact_kind="audio", size_bytes=truncated.stat().st_size
    )
    assert generic.media_type == "audio/mpeg"

    with pytest.raises(ExportDeliveryValidationError) as caught:
        validator.validate(
            truncated,
            expected_format=ExportDeliveryFormat.MP3,
            expected_duration_us=1_000_000,
        )

    assert caught.value.code in {
        ExportDeliveryValidationErrorCode.DECODE_FAILED,
        ExportDeliveryValidationErrorCode.DURATION_MISMATCH,
    }


def test_truncated_flac_is_rejected(tmp_path: Path, validator: ExportDeliveryValidator) -> None:
    complete = tmp_path / "complete.flac"
    truncated = tmp_path / "truncated.flac"
    _encode(complete, duration=4)
    payload = complete.read_bytes()
    truncated.write_bytes(payload[: len(payload) // 2])
    generic = validate_artifact_media(
        truncated, artifact_kind="audio", size_bytes=truncated.stat().st_size
    )
    assert generic.media_type == "audio/flac"

    with pytest.raises(ExportDeliveryValidationError) as caught:
        validator.validate(
            truncated,
            expected_format=ExportDeliveryFormat.FLAC,
            expected_duration_us=4_000_000,
        )

    assert caught.value.code in {
        ExportDeliveryValidationErrorCode.DECODE_FAILED,
        ExportDeliveryValidationErrorCode.DURATION_MISMATCH,
    }


def test_duration_mismatch_is_fail_closed(
    tmp_path: Path, validator: ExportDeliveryValidator
) -> None:
    payload = tmp_path / "delivery.mp3"
    _encode(payload)

    with pytest.raises(ExportDeliveryValidationError) as caught:
        validator.validate(
            payload, expected_format=ExportDeliveryFormat.MP3, expected_duration_us=2_000_000
        )

    assert caught.value.code is ExportDeliveryValidationErrorCode.DURATION_MISMATCH
