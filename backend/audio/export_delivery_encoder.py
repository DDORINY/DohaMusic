"""Canonical WAV to trusted Export delivery encoding."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from backend.audio.export_delivery_validator import ExportDeliveryFormat


class ExportDeliveryEncodingErrorCode(StrEnum):
    ENCODE_FAILED = "EXPORT_DELIVERY_ENCODE_FAILED"


class ExportDeliveryEncodingError(RuntimeError):
    def __init__(self, code: ExportDeliveryEncodingErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class EncodedExportDelivery:
    path: Path
    export_format: ExportDeliveryFormat


class CanonicalExportDeliveryEncoder:
    """Encode one canonical PCM16 WAV with static, non-user-controlled arguments."""

    def __init__(self, *, ffmpeg_executable: str, temp_root: Path) -> None:
        self._ffmpeg = ffmpeg_executable
        self._temp_root = temp_root

    @contextmanager
    def encode(
        self, canonical_wav: Path, *, export_format: ExportDeliveryFormat
    ) -> Iterator[EncodedExportDelivery]:
        if export_format is ExportDeliveryFormat.WAV:
            yield EncodedExportDelivery(canonical_wav, export_format)
            return
        self._temp_root.mkdir(parents=True, exist_ok=True)
        output = self._temp_root / f"delivery-{uuid4()}.{export_format.value}"
        codec_args = (
            ["-c:a", "libmp3lame", "-b:a", "320k", "-write_xing", "1"]
            if export_format is ExportDeliveryFormat.MP3
            else ["-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "8"]
        )
        command = [
            self._ffmpeg,
            "-v",
            "error",
            "-xerror",
            "-i",
            str(canonical_wav),
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-ar",
            "48000",
            "-ac",
            "2",
            *codec_args,
            "-y",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
            if completed.returncode != 0 or not output.is_file():
                raise ExportDeliveryEncodingError(ExportDeliveryEncodingErrorCode.ENCODE_FAILED)
            yield EncodedExportDelivery(output, export_format)
        except OSError:
            raise ExportDeliveryEncodingError(
                ExportDeliveryEncodingErrorCode.ENCODE_FAILED
            ) from None
        finally:
            output.unlink(missing_ok=True)
