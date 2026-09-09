"""Versioned, bounded completion facts for mutation idempotency replay."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

IDEMPOTENCY_RESULT_VERSION = 1
MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES = 8_192


class IdempotencyResultType(StrEnum):
    WORKING_COMPOSITION_INITIALIZE = "WORKING_COMPOSITION_INITIALIZE"
    WORKING_COMPOSITION_CHECKOUT = "WORKING_COMPOSITION_CHECKOUT"
    TRACK_CREATE = "TRACK_CREATE"
    TRACK_DELETE = "TRACK_DELETE"
    TRACK_RESTORE = "TRACK_RESTORE"
    CLIP_CREATE = "CLIP_CREATE"
    CLIP_COPY = "CLIP_COPY"
    CLIP_GAIN_UPDATE = "CLIP_GAIN_UPDATE"
    TRACK_MIXER_UPDATE = "TRACK_MIXER_UPDATE"
    MASTER_GAIN_UPDATE = "MASTER_GAIN_UPDATE"
    CLIP_FADE_UPDATE = "CLIP_FADE_UPDATE"
    CLIP_LOOP_UPDATE = "CLIP_LOOP_UPDATE"
    CLIP_LOOP_RESTORE = "CLIP_LOOP_RESTORE"
    WORKING_HISTORY_UNDO = "WORKING_HISTORY_UNDO"
    WORKING_HISTORY_REDO = "WORKING_HISTORY_REDO"
    CLIP_SPLIT = "CLIP_SPLIT"
    CLIP_DELETE = "CLIP_DELETE"
    CLIP_RESTORE = "CLIP_RESTORE"
    CLIP_UNSPLIT = "CLIP_UNSPLIT"
    CLIP_RESPLIT = "CLIP_RESPLIT"
    COMPOSITION_COMMIT = "COMPOSITION_COMMIT"


_RESULT_PAYLOAD_KEYS: dict[IdempotencyResultType, frozenset[str]] = {
    IdempotencyResultType.WORKING_COMPOSITION_INITIALIZE: frozenset({"working_composition_id"}),
    IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT: frozenset(
        {"working_composition_id", "base_composition_snapshot_id"}
    ),
    IdempotencyResultType.TRACK_CREATE: frozenset({"track_id"}),
    IdempotencyResultType.TRACK_DELETE: frozenset({"track_id"}),
    IdempotencyResultType.TRACK_RESTORE: frozenset({"track_id"}),
    IdempotencyResultType.CLIP_CREATE: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_COPY: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_GAIN_UPDATE: frozenset({"clip_id"}),
    IdempotencyResultType.TRACK_MIXER_UPDATE: frozenset({"track_id"}),
    IdempotencyResultType.MASTER_GAIN_UPDATE: frozenset({"working_composition_id"}),
    IdempotencyResultType.CLIP_FADE_UPDATE: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_LOOP_UPDATE: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_LOOP_RESTORE: frozenset({"clip_id"}),
    IdempotencyResultType.WORKING_HISTORY_UNDO: frozenset({"clip_id"}),
    IdempotencyResultType.WORKING_HISTORY_REDO: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_SPLIT: frozenset(
        {"original_clip_id", "left_clip_id", "right_clip_id"}
    ),
    IdempotencyResultType.CLIP_DELETE: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_RESTORE: frozenset({"clip_id"}),
    IdempotencyResultType.CLIP_UNSPLIT: frozenset(
        {"original_clip_id", "left_clip_id", "right_clip_id"}
    ),
    IdempotencyResultType.CLIP_RESPLIT: frozenset(
        {"original_clip_id", "left_clip_id", "right_clip_id"}
    ),
    IdempotencyResultType.COMPOSITION_COMMIT: frozenset({"composition_snapshot_id"}),
}


@dataclass(frozen=True, slots=True)
class IdempotencyCompletionResult:
    """Canonical successful mutation result persisted with its transaction."""

    result_version: int
    completed_revision: int
    result_type: IdempotencyResultType
    result_payload: Mapping[str, str]

    def __post_init__(self) -> None:
        if type(self.result_version) is not int or self.result_version != 1:
            raise ValueError("IDEMPOTENCY_RESULT_VERSION_UNSUPPORTED")
        if type(self.completed_revision) is not int or self.completed_revision < 0:
            raise ValueError("IDEMPOTENCY_COMPLETED_REVISION_INVALID")
        try:
            normalized_type = IdempotencyResultType(self.result_type)
        except (TypeError, ValueError):
            raise ValueError("IDEMPOTENCY_RESULT_TYPE_INVALID") from None
        if not isinstance(self.result_payload, Mapping):
            raise TypeError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID")

        payload = dict(self.result_payload)
        allowed_keys = _RESULT_PAYLOAD_KEYS[normalized_type]
        history_keys = {
            frozenset({"clip_id"}),
            frozenset({"target_type", "target_id", "clip_id"}),
            frozenset({"target_type", "target_id"}),
        }
        if (
            frozenset(payload) not in history_keys
            if normalized_type
            in {
                IdempotencyResultType.WORKING_HISTORY_UNDO,
                IdempotencyResultType.WORKING_HISTORY_REDO,
            }
            else set(payload) != allowed_keys
        ):
            raise ValueError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID")
        for key, value in payload.items():
            if not isinstance(value, str):
                raise TypeError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID")
            if key == "target_type":
                if value not in {"CLIP", "TRACK", "WORKING_COMPOSITION"}:
                    raise ValueError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID")
                continue
            try:
                parsed = UUID(value)
            except (AttributeError, TypeError, ValueError):
                raise ValueError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID") from None
            if str(parsed) != value:
                raise ValueError("IDEMPOTENCY_RESULT_PAYLOAD_INVALID")

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(serialized.encode("utf-8")) > MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES:
            raise ValueError("IDEMPOTENCY_RESULT_PAYLOAD_TOO_LARGE")

        object.__setattr__(self, "result_type", normalized_type)
        object.__setattr__(self, "result_payload", MappingProxyType(payload))

    @classmethod
    def create(
        cls,
        *,
        completed_revision: int,
        result_type: IdempotencyResultType,
        result_payload: Mapping[str, str],
    ) -> IdempotencyCompletionResult:
        return cls(
            result_version=IDEMPOTENCY_RESULT_VERSION,
            completed_revision=completed_revision,
            result_type=result_type,
            result_payload=result_payload,
        )

    def payload_for_storage(self) -> dict[str, str]:
        return dict(self.result_payload)
