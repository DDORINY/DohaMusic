"""WorkingComposition product API request and response contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkingCompositionInitializeRequest(_StrictModel):
    pass


class WorkingPreviewCreateRequest(_StrictModel):
    expected_revision: int = Field(ge=0)


class WorkingCompositionCommitRequest(_StrictModel):
    expected_revision: int = Field(ge=0)


class WorkingPreviewCreateResult(_StrictModel):
    job_id: UUID
    preview_render_id: UUID
    working_composition_id: UUID
    rendered_revision: int = Field(ge=0)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    replayed: bool


class WorkingMutationRequest(_StrictModel):
    working_composition_id: UUID
    expected_revision: int = Field(ge=0)


class WorkingHistoryMutationRequest(WorkingMutationRequest):
    pass


class WorkingHistoryDetail(_StrictModel):
    working_composition_id: UUID
    revision: int = Field(ge=0)
    cursor: int = Field(ge=0)
    command_count: int = Field(ge=0)
    can_undo: bool
    can_redo: bool


class WorkingCompositionCheckoutRequest(WorkingMutationRequest):
    composition_snapshot_id: UUID


class TrackCreateRequest(WorkingMutationRequest):
    name: str = Field(min_length=1, max_length=200)


class TrackRenameRequest(WorkingMutationRequest):
    name: str = Field(min_length=1, max_length=200)


class TrackReorderRequest(WorkingMutationRequest):
    ordered_track_ids: list[UUID] = Field(min_length=1)


class TrackRestoreRequest(WorkingMutationRequest):
    target_track_order: int = Field(ge=0)


class ClipCreateRequest(WorkingMutationRequest):
    track_id: UUID
    source_asset_version_id: UUID
    timeline_start: Decimal = Field(ge=0)
    source_in: Decimal = Field(ge=0)
    source_out: Decimal = Field(gt=0)


class ClipCopyRequest(WorkingMutationRequest):
    target_track_id: UUID
    target_timeline_start: Decimal = Field(ge=0)


class ClipMoveRequest(WorkingMutationRequest):
    timeline_start: Decimal = Field(ge=0)


class ClipGainUpdateRequest(WorkingMutationRequest):
    gain_db: Decimal

    @field_validator("gain_db", mode="before")
    @classmethod
    def reject_non_numeric_json_values(cls, value: object) -> object:
        if isinstance(value, (str, bool)):
            raise ValueError("gain_db는 JSON number여야 합니다.")
        return value


class TrackMixerUpdateRequest(WorkingMutationRequest):
    gain_db: Decimal
    pan: Decimal
    muted: bool
    solo: bool

    @field_validator("gain_db", "pan", mode="before")
    @classmethod
    def reject_non_numeric_json_values(cls, value: object) -> object:
        if isinstance(value, (str, bool)):
            raise ValueError("Mixer numeric values must be JSON numbers.")
        return value


class MasterGainUpdateRequest(WorkingMutationRequest):
    master_gain_db: Decimal

    @field_validator("master_gain_db", mode="before")
    @classmethod
    def reject_non_numeric_json_values(cls, value: object) -> object:
        if isinstance(value, (str, bool)):
            raise ValueError("master_gain_db must be a JSON number.")
        return value


class ClipFadeUpdateRequest(WorkingMutationRequest):
    fade_in: Decimal
    fade_out: Decimal

    @field_validator("fade_in", "fade_out", mode="before")
    @classmethod
    def reject_non_numeric_json_values(cls, value: object) -> object:
        if isinstance(value, (str, bool)):
            raise ValueError("Fade duration은 JSON number여야 합니다.")
        return value


class ClipLoopUpdateRequest(WorkingMutationRequest):
    loop_enabled: bool
    timeline_duration: Decimal = Field(gt=0)


class ClipLoopRestoreRequest(ClipLoopUpdateRequest):
    loop_phase: Decimal = Field(ge=0)


class ClipTrimStartRequest(WorkingMutationRequest):
    timeline_start: Decimal = Field(ge=0)
    source_in: Decimal = Field(ge=0)


class ClipTrimEndRequest(WorkingMutationRequest):
    source_out: Decimal = Field(gt=0)


class ClipSplitRequest(WorkingMutationRequest):
    split_at: Decimal = Field(gt=0)


class ClipToggleRequest(WorkingMutationRequest):
    left_clip_id: UUID
    right_clip_id: UUID


class TrackDetail(_StrictModel):
    track_id: UUID
    track_type: str
    name: str
    track_order: int = Field(ge=0)
    gain_db: Decimal
    pan: Decimal
    muted: bool
    solo: bool


class ClipDetail(_StrictModel):
    clip_id: UUID
    track_id: UUID
    source_asset_version_id: UUID
    timeline_start: Decimal
    source_in: Decimal
    source_out: Decimal
    source_duration: Decimal
    timeline_duration: Decimal
    loop_enabled: bool
    loop_phase: Decimal
    gain_db: Decimal
    fade_in: Decimal
    fade_out: Decimal
    split_from_clip_id: UUID | None


class ClipMediaSourceDetail(_StrictModel):
    asset_version_id: UUID
    artifact_id: UUID
    media_type: Literal["audio/wav", "audio/flac", "audio/mpeg"]
    size_bytes: int = Field(gt=0)
    artifact_checksum: str
    duration_seconds: Decimal = Field(gt=0)
    content_url: str = Field(pattern=r"^/api/v1/artifacts/[0-9a-fA-F-]{36}/content$")


class WorkingCompositionDetail(_StrictModel):
    working_composition_id: UUID
    project_id: UUID
    base_composition_snapshot_id: UUID | None
    revision: int = Field(ge=0)
    mix_settings: dict[str, object]
    master_gain_db: Decimal
    tracks: list[TrackDetail]
    clips: list[ClipDetail]
    timeline_duration: Decimal


class InitializeResult(_StrictModel):
    working_composition_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class CheckoutResult(_StrictModel):
    working_composition_id: UUID
    base_composition_snapshot_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class CompositionCommitResult(_StrictModel):
    working_composition_id: UUID
    composition_snapshot_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class TrackMutationResult(_StrictModel):
    track_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class MasterGainMutationResult(_StrictModel):
    working_composition_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class HistoryMutationResult(_StrictModel):
    target_type: Literal["CLIP", "TRACK", "WORKING_COMPOSITION"]
    target_id: UUID
    clip_id: UUID | None = None
    completed_revision: int = Field(ge=0)
    replayed: bool


class ReorderTracksResult(_StrictModel):
    working_composition_id: UUID
    completed_revision: int = Field(ge=0)


class ClipMutationResult(_StrictModel):
    clip_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool


class SplitClipResult(_StrictModel):
    original_clip_id: UUID
    left_clip_id: UUID
    right_clip_id: UUID
    completed_revision: int = Field(ge=0)
    replayed: bool
