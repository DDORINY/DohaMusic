"""Trusted technical validation for staged Export delivery payloads."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from backend.storage.artifact_media import (
    ArtifactMediaValidationError,
    validate_artifact_media,
)

EXPORT_SAMPLE_RATE = 48_000
EXPORT_CHANNELS = 2
EXPORT_BIT_DEPTH = 16
EXPORT_VALIDATION_TIMEOUT_SECONDS = 60
MP3_SAMPLES_PER_FRAME = 1_152


class ExportDeliveryFormat(StrEnum):
    WAV = "wav"
    MP3 = "mp3"
    FLAC = "flac"


class ExportDeliveryValidationErrorCode(StrEnum):
    CONFIGURATION_ERROR = "EXPORT_DELIVERY_VALIDATOR_UNAVAILABLE"
    INVALID = "EXPORT_DELIVERY_INVALID"
    DECODE_FAILED = "EXPORT_DELIVERY_DECODE_FAILED"
    FORMAT_MISMATCH = "EXPORT_DELIVERY_FORMAT_MISMATCH"
    SAMPLE_RATE_MISMATCH = "EXPORT_DELIVERY_SAMPLE_RATE_MISMATCH"
    CHANNEL_MISMATCH = "EXPORT_DELIVERY_CHANNEL_MISMATCH"
    BIT_DEPTH_MISMATCH = "EXPORT_DELIVERY_BIT_DEPTH_MISMATCH"
    DURATION_MISMATCH = "EXPORT_DELIVERY_DURATION_MISMATCH"


class ExportDeliveryValidationError(RuntimeError):
    def __init__(self, code: ExportDeliveryValidationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ValidatedExportDelivery:
    export_format: ExportDeliveryFormat
    media_type: str
    codec: str
    sample_rate: int
    channels: int
    duration_us: int
    bit_depth: int | None
    full_decode_verified: bool


_FORMAT_AUTHORITY = {
    ExportDeliveryFormat.WAV: ("audio/wav", "pcm_s16le", "wav"),
    ExportDeliveryFormat.MP3: ("audio/mpeg", "mp3", "mp3"),
    ExportDeliveryFormat.FLAC: ("audio/flac", "flac", "flac"),
}


def export_delivery_media_type(export_format: ExportDeliveryFormat) -> str:
    return _FORMAT_AUTHORITY[export_format][0]


def parse_export_delivery_format(value: str) -> ExportDeliveryFormat:
    try:
        return ExportDeliveryFormat(value.strip().lower())
    except (AttributeError, ValueError):
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID) from None


class ExportDeliveryValidator:
    """Fail-closed FFprobe metadata plus complete FFmpeg decode validation."""

    def __init__(
        self,
        *,
        ffmpeg_executable: str,
        timeout_seconds: int = EXPORT_VALIDATION_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds < 1:
            raise ExportDeliveryValidationError(
                ExportDeliveryValidationErrorCode.CONFIGURATION_ERROR
            )
        self._ffmpeg = _resolve_executable(ffmpeg_executable)
        self._ffprobe = _resolve_ffprobe(self._ffmpeg)
        self._timeout = timeout_seconds

    def validate(
        self,
        path: Path,
        *,
        expected_format: ExportDeliveryFormat,
        expected_duration_us: int,
    ) -> ValidatedExportDelivery:
        if not isinstance(expected_format, ExportDeliveryFormat) or expected_duration_us < 1:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)
        try:
            size_bytes = path.stat().st_size
            structural = validate_artifact_media(path, artifact_kind="audio", size_bytes=size_bytes)
        except (OSError, ArtifactMediaValidationError):
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID) from None
        media_type, codec, format_name = _FORMAT_AUTHORITY[expected_format]
        if structural.media_type != media_type:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.FORMAT_MISMATCH)
        probe = self._probe(path)
        streams = probe.get("streams")
        if not isinstance(streams, list) or len(streams) != 1 or not isinstance(streams[0], dict):
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)
        stream = streams[0]
        if stream.get("codec_type") != "audio" or stream.get("codec_name") != codec:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.FORMAT_MISMATCH)
        detected_format = str(probe.get("format", {}).get("format_name", "")).split(",")
        if format_name not in detected_format:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.FORMAT_MISMATCH)
        sample_rate = _positive_int(stream.get("sample_rate"))
        channels = _positive_int(stream.get("channels"))
        if sample_rate != EXPORT_SAMPLE_RATE:
            raise ExportDeliveryValidationError(
                ExportDeliveryValidationErrorCode.SAMPLE_RATE_MISMATCH
            )
        if channels != EXPORT_CHANNELS:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.CHANNEL_MISMATCH)
        bit_depth = _bit_depth(stream)
        if (
            expected_format in {ExportDeliveryFormat.WAV, ExportDeliveryFormat.FLAC}
            and bit_depth != EXPORT_BIT_DEPTH
        ):
            raise ExportDeliveryValidationError(
                ExportDeliveryValidationErrorCode.BIT_DEPTH_MISMATCH
            )
        duration_us = _duration_us(probe, stream)
        tolerance_us = (
            2 * MP3_SAMPLES_PER_FRAME * 1_000_000 // EXPORT_SAMPLE_RATE
            if expected_format is ExportDeliveryFormat.MP3
            else 1
        )
        if abs(duration_us - expected_duration_us) > tolerance_us:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.DURATION_MISMATCH)
        if expected_format is ExportDeliveryFormat.MP3:
            decoded_duration_us = (
                _positive_int(stream.get("nb_read_frames"))
                * MP3_SAMPLES_PER_FRAME
                * 1_000_000
                // sample_rate
            )
            if abs(decoded_duration_us - expected_duration_us) > tolerance_us:
                raise ExportDeliveryValidationError(
                    ExportDeliveryValidationErrorCode.DURATION_MISMATCH
                )
        decoded_frame_count = self._decode(path)
        if expected_format is ExportDeliveryFormat.FLAC:
            expected_frame_count = round(expected_duration_us * sample_rate / 1_000_000)
            if decoded_frame_count != expected_frame_count:
                raise ExportDeliveryValidationError(
                    ExportDeliveryValidationErrorCode.DURATION_MISMATCH
                )
        return ValidatedExportDelivery(
            expected_format,
            media_type,
            codec,
            sample_rate,
            channels,
            duration_us,
            bit_depth,
            True,
        )

    def _probe(self, path: Path) -> dict[str, object]:
        completed = _run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,sample_rate,channels,bits_per_sample,bits_per_raw_sample,duration,nb_read_frames",
                "-count_frames",
                "-of",
                "json",
                str(path),
            ],
            timeout=self._timeout,
            capture_stdout=True,
        )
        try:
            result = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID) from None
        if not isinstance(result, dict):
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)
        return result

    def _decode(self, path: Path) -> int:
        frame_size = EXPORT_CHANNELS * EXPORT_BIT_DEPTH // 8
        with tempfile.TemporaryFile() as decoded:
            _run(
                [
                    self._ffmpeg,
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                timeout=self._timeout,
                capture_stdout=False,
                stdout_file=decoded,
            )
            decoded_size = decoded.tell()
        if decoded_size % frame_size:
            raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.DECODE_FAILED)
        return decoded_size // frame_size


def _run(
    command: list[str],
    *,
    timeout: int,
    capture_stdout: bool,
    stdout_file: BinaryIO | None = None,
) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=(
                stdout_file
                if stdout_file is not None
                else subprocess.PIPE
                if capture_stdout
                else subprocess.DEVNULL
            ),
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ExportDeliveryValidationError(
            ExportDeliveryValidationErrorCode.CONFIGURATION_ERROR
        ) from None
    if completed.returncode != 0:
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.DECODE_FAILED)
    return completed


def _resolve_executable(configured: str) -> str:
    candidate = Path(configured)
    resolved = (
        str(candidate)
        if candidate.is_absolute() and candidate.is_file()
        else shutil.which(configured)
    )
    if resolved is None:
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.CONFIGURATION_ERROR)
    return resolved


def _resolve_ffprobe(ffmpeg: str) -> str:
    executable = Path(ffmpeg)
    sibling = executable.with_name(
        "ffprobe.exe" if executable.suffix.lower() == ".exe" else "ffprobe"
    )
    resolved = str(sibling) if sibling.is_file() else shutil.which("ffprobe")
    if resolved is None:
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.CONFIGURATION_ERROR)
    return resolved


def _positive_int(value: object) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID) from None
    if parsed < 1:
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)
    return parsed


def _bit_depth(stream: dict[str, object]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = stream.get(key)
        if value not in {None, "", 0, "0"}:
            return _positive_int(value)
    return None


def _duration_us(probe: dict[str, object], stream: dict[str, object]) -> int:
    raw = stream.get("duration") or probe.get("format", {}).get("duration")
    try:
        duration_us = round(float(str(raw)) * 1_000_000)
    except (TypeError, ValueError, OverflowError):
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID) from None
    if duration_us < 1:
        raise ExportDeliveryValidationError(ExportDeliveryValidationErrorCode.INVALID)
    return duration_us
