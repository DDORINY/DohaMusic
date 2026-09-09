"""Atomic WorkingComposition, Track, and Clip product mutations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from itertools import pairwise
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    ApplicationValidationError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    ResourceNotFoundError,
    WorkspaceBootstrapRequiredError,
)
from backend.core.idempotency_completion import (
    IdempotencyCompletionResult,
    IdempotencyResultType,
)
from backend.models.workspace import (
    AssetType,
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    MusicProject,
    WorkingComposition,
)
from backend.repositories.idempotency_repository import (
    IdempotencyClaim,
    IdempotencyRepository,
)
from backend.repositories.workspace import (
    AssetRepository,
    CompositionHistoryRepository,
    CompositionRepository,
    WorkspaceRepository,
)
from backend.services.workspace.trusted_media_metadata_service import (
    TrustedClipSourceMetadata,
    TrustedMediaMetadataError,
    TrustedMediaMetadataErrorCode,
    TrustedMediaMetadataService,
)

IDEMPOTENCY_TTL_HOURS = 24
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_TRACK_NAME_LENGTH = 200
MICROSECONDS_PER_SECOND = Decimal(1000000)
MIN_CLIP_GAIN_DB = Decimal("-24.00")
MAX_CLIP_GAIN_DB = Decimal("24.00")
CLIP_GAIN_DB_QUANTUM = Decimal("0.01")
AUDIO_ASSET_TYPES = frozenset(
    {
        AssetType.MUSIC,
        AssetType.VOCAL,
        AssetType.STEM,
        AssetType.RECORDING,
        AssetType.MIX,
        AssetType.EXPORT,
    }
)


class WorkingCompositionErrorCode(StrEnum):
    WORKING_COMPOSITION_NOT_FOUND = "WORKING_COMPOSITION_NOT_FOUND"
    WORKING_COMPOSITION_ALREADY_EXISTS = "WORKING_COMPOSITION_ALREADY_EXISTS"
    WORKING_COMPOSITION_REVISION_CONFLICT = "WORKING_COMPOSITION_REVISION_CONFLICT"
    WORKING_COMPOSITION_EMPTY = "WORKING_COMPOSITION_EMPTY"
    TRACK_NOT_FOUND = "TRACK_NOT_FOUND"
    TRACK_NOT_EMPTY = "TRACK_NOT_EMPTY"
    TRACK_ALREADY_ACTIVE = "TRACK_ALREADY_ACTIVE"
    TRACK_RESTORE_ORDER_INVALID = "TRACK_RESTORE_ORDER_INVALID"
    CLIP_NOT_FOUND = "CLIP_NOT_FOUND"
    CLIP_ALREADY_ACTIVE = "CLIP_ALREADY_ACTIVE"
    CLIP_OVERLAP = "CLIP_OVERLAP"
    CLIP_GAIN_OUT_OF_RANGE = "CLIP_GAIN_OUT_OF_RANGE"
    CLIP_FADE_OUT_OF_RANGE = "CLIP_FADE_OUT_OF_RANGE"
    CLIP_LOOP_GEOMETRY_INVALID = "CLIP_LOOP_GEOMETRY_INVALID"
    TRACK_MIXER_OUT_OF_RANGE = "TRACK_MIXER_OUT_OF_RANGE"
    MASTER_GAIN_OUT_OF_RANGE = "MASTER_GAIN_OUT_OF_RANGE"
    WORKING_HISTORY_EMPTY = "WORKING_HISTORY_EMPTY"
    WORKING_HISTORY_STRUCTURE_CONFLICT = "WORKING_HISTORY_STRUCTURE_CONFLICT"
    SPLIT_STRUCTURE_CONFLICT = "SPLIT_STRUCTURE_CONFLICT"
    INVALID_CLIP_RANGE = "INVALID_CLIP_RANGE"
    SOURCE_ASSET_UNAVAILABLE = "SOURCE_ASSET_UNAVAILABLE"
    SOURCE_ARTIFACT_AMBIGUOUS = "SOURCE_ARTIFACT_AMBIGUOUS"
    SOURCE_DURATION_UNAVAILABLE = "SOURCE_DURATION_UNAVAILABLE"
    SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE = "SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE"


_SAFE_ERROR_MESSAGES = {
    WorkingCompositionErrorCode.WORKING_COMPOSITION_EMPTY: (
        "활성 Clip이 없는 WorkingComposition은 Commit할 수 없습니다."
    ),
    WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND: (
        "WorkingComposition을 찾을 수 없습니다."
    ),
    WorkingCompositionErrorCode.WORKING_COMPOSITION_ALREADY_EXISTS: (
        "Project에 WorkingComposition이 이미 존재합니다."
    ),
    WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT: (
        "WorkingComposition revision이 현재 상태와 일치하지 않습니다."
    ),
    WorkingCompositionErrorCode.TRACK_NOT_FOUND: "Track을 찾을 수 없습니다.",
    WorkingCompositionErrorCode.TRACK_NOT_EMPTY: "활성 Clip이 있는 Track은 삭제할 수 없습니다.",
    WorkingCompositionErrorCode.TRACK_ALREADY_ACTIVE: "Track이 이미 활성 상태입니다.",
    WorkingCompositionErrorCode.TRACK_RESTORE_ORDER_INVALID: (
        "Track 복원 순서가 유효하지 않습니다."
    ),
    WorkingCompositionErrorCode.CLIP_NOT_FOUND: "Clip을 찾을 수 없습니다.",
    WorkingCompositionErrorCode.CLIP_ALREADY_ACTIVE: "Clip이 이미 활성 상태입니다.",
    WorkingCompositionErrorCode.CLIP_OVERLAP: "같은 Track의 활성 Clip은 겹칠 수 없습니다.",
    WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE: (
        "Clip Gain은 -24.00 dB 이상 24.00 dB 이하의 0.01 dB 단위 유한값이어야 합니다."
    ),
    WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE: (
        "Clip Fade는 0 이상이며 합이 Clip 길이 이하인 0.000001초 단위 유한값이어야 합니다."
    ),
    WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID: "Clip Loop geometry is invalid.",
    WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE: "Track Mixer state is invalid.",
    WorkingCompositionErrorCode.MASTER_GAIN_OUT_OF_RANGE: "Master Gain is invalid.",
    WorkingCompositionErrorCode.WORKING_HISTORY_EMPTY: (
        "No WorkingComposition history command is available."
    ),
    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT: (
        "The history target no longer has compatible structure."
    ),
    WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT: (
        "Split 원본과 child의 canonical geometry가 일치하지 않습니다."
    ),
    WorkingCompositionErrorCode.INVALID_CLIP_RANGE: "Clip 시간 범위가 유효하지 않습니다.",
    WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE: (
        "Clip source AssetVersion을 사용할 수 없습니다."
    ),
    WorkingCompositionErrorCode.SOURCE_ARTIFACT_AMBIGUOUS: (
        "Clip source Artifact를 하나로 결정할 수 없습니다."
    ),
    WorkingCompositionErrorCode.SOURCE_DURATION_UNAVAILABLE: (
        "Clip source의 신뢰할 수 있는 길이가 없습니다."
    ),
    WorkingCompositionErrorCode.SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE: (
        "Snapshot에 checkout 가능한 arrangement가 없습니다."
    ),
}


class WorkingCompositionError(RuntimeError):
    def __init__(self, code: WorkingCompositionErrorCode) -> None:
        super().__init__(_SAFE_ERROR_MESSAGES[code])
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkingCompositionAggregate:
    working_composition: WorkingComposition
    tracks: tuple[CompositionTrack, ...]
    clips: tuple[CompositionClip, ...]
    timeline_duration_us: int


@dataclass(frozen=True, slots=True)
class ClipMediaSource:
    asset_version_id: UUID
    artifact_id: UUID
    media_type: str
    size_bytes: int
    artifact_checksum: str
    duration_us: int
    content_url: str


@dataclass(frozen=True, slots=True)
class WorkingMutationResult:
    completed_revision: int
    replayed: bool
    result_type: IdempotencyResultType | None
    identities: Mapping[str, UUID | str]


class WorkingCompositionService:
    """Own the transaction boundary for canonical composition editing."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def get_working_composition(
        self, project_id: UUID, *, effective_owner_id: UUID
    ) -> WorkingCompositionAggregate:
        _validate_uuid(project_id, "project_id")
        _validate_uuid(effective_owner_id, "effective_owner_id")
        with self.session_factory() as session:
            self._require_project_scope(session, project_id, effective_owner_id)
            repository = CompositionRepository(session)
            working = repository.get_project_working_composition(project_id)
            if working is None:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND
                )
            return self._load_aggregate(repository, working)

    def get_history_state(
        self, project_id: UUID, *, working_composition_id: UUID, effective_owner_id: UUID
    ) -> dict[str, object]:
        with self.session_factory() as session:
            self._require_project_scope(session, project_id, effective_owner_id)
            repository = CompositionRepository(session)
            working = self._require_working(repository, project_id, working_composition_id)
            cursor, maximum = CompositionHistoryRepository(session).read_state(
                working_composition_id
            )
            return {
                "working_composition_id": working_composition_id,
                "revision": working.revision,
                "cursor": cursor,
                "command_count": maximum,
                "can_undo": cursor > 0,
                "can_redo": cursor < maximum,
            }

    def undo_history(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        return self._move_history_cursor(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
            redo=False,
        )

    def redo_history(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        return self._move_history_cursor(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
            redo=True,
        )

    def _move_history_cursor(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
        redo: bool,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        operation = (
            IdempotencyResultType.WORKING_HISTORY_REDO
            if redo
            else IdempotencyResultType.WORKING_HISTORY_UNDO
        )

        def mutate(
            repository: CompositionRepository, session: Session, working: WorkingComposition
        ) -> tuple[str, UUID, UUID | None]:
            history = CompositionHistoryRepository(session)
            entry = (
                history.current_redo(working_composition_id)
                if redo
                else history.current_undo(working_composition_id)
            )
            if entry is None:
                raise WorkingCompositionError(WorkingCompositionErrorCode.WORKING_HISTORY_EMPTY)
            state = entry.after_state if redo else entry.before_state
            self._apply_history_state(repository, working, entry, state)
            history.move_cursor(working_composition_id, 1 if redo else -1)
            return entry.target_type, entry.target_id, entry.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=None,
            body={},
            mutate=mutate,
            payload=lambda identity: {
                "target_type": identity[0],
                "target_id": identity[1],
                **({"clip_id": identity[2]} if identity[2] is not None else {}),
            },
            resource_type="working_history_target",
            resource_id=lambda identity: identity[1],
            response_status=200,
        )

    def _apply_history_state(self, repository, working, entry, state: Mapping[str, object]) -> None:
        matrix = {
            "CLIP_GAIN": "CLIP",
            "CLIP_FADE": "CLIP",
            "CLIP_LOOP": "CLIP",
            "TRACK_MIXER": "TRACK",
            "MASTER_GAIN": "WORKING_COMPOSITION",
        }
        if matrix.get(entry.command_type) != entry.target_type:
            raise WorkingCompositionError(
                WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
            )
        if entry.target_type == "CLIP":
            clip = repository.get_composition_clip(working.working_composition_id, entry.target_id)
            if clip is None or entry.clip_id != entry.target_id:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
                )
            self._apply_history_clip_state(repository, working, clip, entry.command_type, state)
        elif entry.target_type == "TRACK":
            track = repository.get_composition_track(
                working.working_composition_id, entry.target_id
            )
            if track is None or set(state) != {"gain_db", "pan", "muted", "solo"}:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
                )
            track.gain_db = _normalize_mixer_gain(Decimal(str(state["gain_db"])), master=False)
            track.pan = _normalize_pan(Decimal(str(state["pan"])))
            track.muted, track.solo = _history_bool(state["muted"]), _history_bool(state["solo"])
            repository.flush()
        elif entry.target_type == "WORKING_COMPOSITION":
            if entry.target_id != working.working_composition_id or set(state) != {
                "master_gain_db"
            }:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
                )
            working.master_gain_db = _normalize_mixer_gain(
                Decimal(str(state["master_gain_db"])), master=True
            )
            repository.flush()
        else:
            raise WorkingCompositionError(
                WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
            )

    def _apply_history_clip_state(
        self,
        repository: CompositionRepository,
        working: WorkingComposition,
        clip: CompositionClip,
        command_type: str,
        state: Mapping[str, object],
    ) -> None:
        if command_type == "CLIP_GAIN":
            clip.gain_db = _normalize_clip_gain_db(Decimal(str(state["gain_db"])))
        elif command_type == "CLIP_FADE":
            fade_in = int(state["fade_in"])
            fade_out = int(state["fade_out"])
            _validate_clip_fade(
                fade_in=fade_in, fade_out=fade_out, clip_duration=clip.timeline_duration
            )
            clip.fade_in, clip.fade_out = fade_in, fade_out
        elif command_type == "CLIP_LOOP":
            duration = int(state["timeline_duration"])
            phase = int(state["loop_phase"])
            enabled = bool(state["loop_enabled"])
            window = clip.source_out - clip.source_in
            if (
                duration <= 0
                or phase < 0
                or phase >= window
                or (not enabled and (duration != window or phase != 0))
            ):
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
                )
            _validate_clip_fade(
                fade_in=clip.fade_in, fade_out=clip.fade_out, clip_duration=duration
            )
            if repository.active_clip_overlap_exists(
                working_composition_id=working.working_composition_id,
                track_id=clip.track_id,
                timeline_start=clip.timeline_start,
                timeline_end=clip.timeline_start + duration,
                exclude_clip_id=clip.clip_id,
            ):
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
                )
            clip.loop_enabled, clip.timeline_duration, clip.loop_phase = enabled, duration, phase
        else:
            raise WorkingCompositionError(
                WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
            )
        repository.flush()

    def resolve_clip_media_source(
        self,
        project_id: UUID,
        asset_version_id: UUID,
        *,
        effective_owner_id: UUID,
    ) -> ClipMediaSource:
        """Resolve one currently eligible exact-version Clip source without payload I/O."""

        _validate_uuid(project_id, "project_id")
        _validate_uuid(asset_version_id, "asset_version_id")
        _validate_uuid(effective_owner_id, "effective_owner_id")
        with self.session_factory() as session:
            project = self._require_project_scope(session, project_id, effective_owner_id)
            source = self._resolve_source_metadata(
                session,
                project_id=project_id,
                workspace_id=project.workspace_id,
                effective_owner_id=effective_owner_id,
                asset_version_id=asset_version_id,
            )
        return ClipMediaSource(
            asset_version_id=source.asset_version_id,
            artifact_id=source.artifact_id,
            media_type=source.media_type,
            size_bytes=source.size_bytes,
            artifact_checksum=source.artifact_checksum,
            duration_us=source.duration_us,
            content_url=f"/api/v1/artifacts/{source.artifact_id}/content",
        )

    def initialize(
        self,
        project_id: UUID,
        *,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        _validate_uuid(project_id, "project_id")
        _validate_uuid(effective_owner_id, "effective_owner_id")
        key = _normalize_idempotency_key(idempotency_key)
        operation = IdempotencyResultType.WORKING_COMPOSITION_INITIALIZE
        fingerprint = _fingerprint(
            effective_owner_id=effective_owner_id,
            project_id=project_id,
            working_composition_id=None,
            operation=operation.value,
            expected_revision=None,
            target_identity=None,
            body={},
        )
        scope = f"working-composition:{effective_owner_id}:{project_id}:initialize"
        try:
            with self.session_factory() as session, session.begin():
                self._require_project_scope(session, project_id, effective_owner_id)
                idempotency = IdempotencyRepository(session)
                claim = _claim_with_result(
                    idempotency,
                    scope=scope,
                    key=key,
                    fingerprint=fingerprint,
                )
                replay = _replay_result(claim, operation)
                if replay is not None:
                    return replay
                repository = CompositionRepository(session)
                if repository.get_project_working_composition(project_id) is not None:
                    raise WorkingCompositionError(
                        WorkingCompositionErrorCode.WORKING_COMPOSITION_ALREADY_EXISTS
                    )
                working = repository.add_working_composition(
                    WorkingComposition(
                        project_id=project_id,
                        base_composition_snapshot_id=None,
                        mix_settings={},
                        revision=0,
                    )
                )
                result = _complete_result(
                    idempotency,
                    claim,
                    completed_revision=0,
                    result_type=operation,
                    payload={"working_composition_id": working.working_composition_id},
                    resource_type="working_composition",
                    resource_id=working.working_composition_id,
                    response_status=201,
                )
            return result
        except IntegrityError:
            if self._project_has_working_composition(project_id, effective_owner_id):
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_COMPOSITION_ALREADY_EXISTS
                ) from None
            raise

    def commit(
        self,
        project_id: UUID,
        *,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        """Freeze the canonical working arrangement as one new immutable Snapshot."""

        _validate_uuid(project_id, "project_id")
        _validate_uuid(effective_owner_id, "effective_owner_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ApplicationValidationError("expected_revision은 0 이상의 정수여야 합니다.")
        key = _normalize_idempotency_key(idempotency_key)
        operation = IdempotencyResultType.COMPOSITION_COMMIT

        with self.session_factory() as session, session.begin():
            self._require_project_scope(session, project_id, effective_owner_id)
            repository = CompositionRepository(session)
            working = repository.get_project_working_composition(project_id)
            if working is None:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND
                )
            fingerprint = _fingerprint(
                effective_owner_id=effective_owner_id,
                project_id=project_id,
                working_composition_id=working.working_composition_id,
                operation=operation.value,
                expected_revision=expected_revision,
                target_identity=None,
                body={},
            )
            scope = (
                f"working-composition:{effective_owner_id}:{project_id}:"
                f"{working.working_composition_id}:{operation.value}"
            )
            idempotency = IdempotencyRepository(session)
            claim = _claim_with_result(
                idempotency,
                scope=scope,
                key=key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(claim, operation)
            if replay is not None:
                return WorkingMutationResult(
                    completed_revision=replay.completed_revision,
                    replayed=True,
                    result_type=replay.result_type,
                    identities={
                        **replay.identities,
                        "working_composition_id": working.working_composition_id,
                    },
                )

            _require_expected_revision(working, expected_revision)
            tracks = repository.list_active_composition_tracks(working.working_composition_id)
            clips = repository.list_working_composition_clips(working.working_composition_id)
            if not clips:
                raise WorkingCompositionError(WorkingCompositionErrorCode.WORKING_COMPOSITION_EMPTY)
            revision = self._increment_revision(
                repository, working.working_composition_id, expected_revision
            )

            snapshot = repository.add_snapshot(
                CompositionSnapshot(
                    project_id=project_id,
                    snapshot_version=repository.get_next_snapshot_version(project_id),
                    processing_chain_id=None,
                    mix_settings_snapshot=dict(working.mix_settings),
                    master_gain_db=working.master_gain_db,
                    provider_versions={},
                    model_manifest_ids={},
                    created_by=effective_owner_id,
                )
            )
            snapshot_track_ids: dict[UUID, UUID] = {}
            for track in tracks:
                frozen = repository.add_snapshot_track(
                    CompositionSnapshotTrack(
                        composition_snapshot_id=snapshot.composition_snapshot_id,
                        canonical_track_id=track.track_id,
                        track_type=track.track_type,
                        name=track.name,
                        track_order=track.track_order,
                        gain_db=track.gain_db,
                        pan=track.pan,
                        muted=track.muted,
                        solo=track.solo,
                    )
                )
                snapshot_track_ids[track.track_id] = frozen.snapshot_track_id
            for clip in clips:
                repository.add_snapshot_clip(
                    CompositionSnapshotClip(
                        composition_snapshot_id=snapshot.composition_snapshot_id,
                        snapshot_track_id=snapshot_track_ids[clip.track_id],
                        canonical_clip_id=clip.clip_id,
                        source_asset_version_id=clip.source_asset_version_id,
                        timeline_start=clip.timeline_start,
                        source_in=clip.source_in,
                        source_out=clip.source_out,
                        source_duration=clip.source_duration,
                        timeline_duration=clip.timeline_duration,
                        loop_enabled=clip.loop_enabled,
                        loop_phase=clip.loop_phase,
                        gain_db=clip.gain_db,
                        fade_in=clip.fade_in,
                        fade_out=clip.fade_out,
                        split_from_clip_id=clip.split_from_clip_id,
                    )
                )

            repository.set_project_selection(project_id, snapshot.composition_snapshot_id)
            working.base_composition_snapshot_id = snapshot.composition_snapshot_id
            CompositionHistoryRepository(session).clear(working.working_composition_id)
            repository.flush()
            completed = _complete_result(
                idempotency,
                claim,
                completed_revision=revision,
                result_type=operation,
                payload={"composition_snapshot_id": snapshot.composition_snapshot_id},
                resource_type="composition_snapshot",
                resource_id=snapshot.composition_snapshot_id,
                response_status=201,
            )
            return WorkingMutationResult(
                completed_revision=completed.completed_revision,
                replayed=False,
                result_type=completed.result_type,
                identities={
                    **completed.identities,
                    "working_composition_id": working.working_composition_id,
                },
            )

    def checkout(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        composition_snapshot_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(composition_snapshot_id, "composition_snapshot_id")
        operation = IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT
        body = {"composition_snapshot_id": str(composition_snapshot_id)}
        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=composition_snapshot_id,
            body=body,
            mutate=lambda repository, session, working: self._checkout_rows_with_history_barrier(
                repository,
                session,
                working,
                composition_snapshot_id=composition_snapshot_id,
            ),
            payload=lambda _identity: {
                "working_composition_id": working_composition_id,
                "base_composition_snapshot_id": composition_snapshot_id,
            },
            resource_type="working_composition",
            resource_id=lambda _identity: working_composition_id,
            response_status=200,
        )

    def create_track(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        name: str,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        track_name = _normalize_track_name(name)
        operation = IdempotencyResultType.TRACK_CREATE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            track = repository.add_composition_track(
                CompositionTrack(
                    working_composition_id=working.working_composition_id,
                    track_type="audio",
                    name=track_name,
                    track_order=len(
                        repository.list_active_composition_tracks(working.working_composition_id)
                    ),
                )
            )
            return track.track_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=None,
            body={"name": track_name},
            mutate=mutate,
            payload=lambda track_id: {"track_id": track_id},
            resource_type="composition_track",
            resource_id=lambda track_id: track_id,
            response_status=201,
        )

    def rename_track(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        track_id: UUID,
        name: str,
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(track_id, "track_id")
        track_name = _normalize_track_name(name)

        def mutate(repository: CompositionRepository, working: WorkingComposition) -> UUID:
            track = self._require_track(repository, working, track_id)
            track.name = track_name
            repository.flush()
            return track.track_id

        return self._run_absolute_mutation(**normalized, mutate=mutate)

    def reorder_tracks(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        ordered_track_ids: Sequence[UUID],
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        order = tuple(ordered_track_ids)
        if not order or len(set(order)) != len(order):
            raise ApplicationValidationError(
                "ordered_track_ids는 중복 없는 전체 active Track 목록이어야 합니다."
            )
        for track_id in order:
            _validate_uuid(track_id, "ordered_track_ids")

        def mutate(repository: CompositionRepository, working: WorkingComposition) -> UUID:
            try:
                repository.reorder_active_composition_tracks(working.working_composition_id, order)
            except ValueError:
                raise ApplicationValidationError(
                    "ordered_track_ids는 전체 active Track과 정확히 일치해야 합니다."
                ) from None
            return working.working_composition_id

        return self._run_absolute_mutation(**normalized, mutate=mutate)

    def delete_track(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        track_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(track_id, "track_id")
        operation = IdempotencyResultType.TRACK_DELETE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            track = self._require_track(repository, working, track_id)
            if repository.count_active_composition_clips(
                working_composition_id=working.working_composition_id,
                track_id=track.track_id,
            ):
                raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_NOT_EMPTY)
            repository.tombstone_composition_track(track)
            return track.track_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=track_id,
            body={},
            mutate=mutate,
            payload=lambda identity: {"track_id": identity},
            resource_type="composition_track",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def restore_track(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        track_id: UUID,
        target_track_order: int,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(track_id, "track_id")
        if type(target_track_order) is not int or target_track_order < 0:
            raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_RESTORE_ORDER_INVALID)
        operation = IdempotencyResultType.TRACK_RESTORE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            track = self._require_track_any(repository, working, track_id)
            if track.deleted_at is None:
                raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_ALREADY_ACTIVE)
            try:
                repository.restore_composition_track(track, target_track_order=target_track_order)
            except ValueError as error:
                code = (
                    WorkingCompositionErrorCode.TRACK_ALREADY_ACTIVE
                    if str(error) == "TRACK_ALREADY_ACTIVE"
                    else WorkingCompositionErrorCode.TRACK_RESTORE_ORDER_INVALID
                )
                raise WorkingCompositionError(code) from None
            return track.track_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=track_id,
            body={"target_track_order": target_track_order},
            mutate=mutate,
            payload=lambda identity: {"track_id": identity},
            resource_type="composition_track",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def create_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        track_id: UUID,
        source_asset_version_id: UUID,
        timeline_start: object,
        source_in: object,
        source_out: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(track_id, "track_id")
        _validate_uuid(source_asset_version_id, "source_asset_version_id")
        start_us = _seconds_to_microseconds(timeline_start, "timeline_start")
        source_in_us = _seconds_to_microseconds(source_in, "source_in")
        source_out_us = _seconds_to_microseconds(source_out, "source_out")
        operation = IdempotencyResultType.CLIP_CREATE

        def mutate(
            repository: CompositionRepository,
            session: Session,
            working: WorkingComposition,
        ) -> UUID:
            self._require_track(repository, working, track_id)
            source_duration = self._resolve_source_duration(
                session,
                project_id=project_id,
                workspace_id=self._project_workspace_id(session, project_id, effective_owner_id),
                effective_owner_id=effective_owner_id,
                asset_version_id=source_asset_version_id,
            )
            _validate_clip_range(
                timeline_start=start_us,
                source_in=source_in_us,
                source_out=source_out_us,
                source_duration=source_duration,
            )
            clip = CompositionClip(
                working_composition_id=working.working_composition_id,
                track_id=track_id,
                source_asset_version_id=source_asset_version_id,
                timeline_start=start_us,
                source_in=source_in_us,
                source_out=source_out_us,
                source_duration=source_duration,
                timeline_duration=source_out_us - source_in_us,
                loop_enabled=False,
                loop_phase=0,
                split_from_clip_id=None,
            )
            try:
                repository.add_composition_clip(clip)
            except ValueError:
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP) from None
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=track_id,
            body={
                "source_asset_version_id": str(source_asset_version_id),
                "timeline_start_us": start_us,
                "source_in_us": source_in_us,
                "source_out_us": source_out_us,
            },
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=201,
        )

    def move_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        timeline_start: object,
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> WorkingMutationResult:
        start_us = _seconds_to_microseconds(timeline_start, "timeline_start")
        return self._mutate_clip_absolute(
            project_id=project_id,
            working_composition_id=working_composition_id,
            clip_id=clip_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            changes=lambda clip: {"timeline_start": start_us},
        )

    def set_clip_gain(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        gain_db: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        """Persist one absolute Clip-level static gain value."""

        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        normalized_gain = _normalize_clip_gain_db(gain_db)
        operation = IdempotencyResultType.CLIP_GAIN_UPDATE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            before = {"gain_db": f"{clip.gain_db:.2f}"}
            clip.gain_db = normalized_gain
            repository.flush()
            CompositionHistoryRepository(_session).append(
                working_composition_id=working.working_composition_id,
                command_type="CLIP_GAIN",
                clip_id=clip.clip_id,
                before_state=before,
                after_state={"gain_db": f"{normalized_gain:.2f}"},
            )
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={"gain_db": f"{normalized_gain:.2f}"},
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def set_track_mixer(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        track_id: UUID,
        gain_db: object,
        pan: object,
        muted: bool,
        solo: bool,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(track_id, "track_id")
        gain, normalized_pan = _normalize_mixer_gain(gain_db, master=False), _normalize_pan(pan)
        if type(muted) is not bool or type(solo) is not bool:
            raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE)

        def mutate(repository, session, working):
            track = self._require_track(repository, working, track_id)
            before = _track_mixer_state(track)
            track.gain_db, track.pan, track.muted, track.solo = gain, normalized_pan, muted, solo
            repository.flush()
            CompositionHistoryRepository(session).append(
                working_composition_id=working.working_composition_id,
                command_type="TRACK_MIXER",
                target_type="TRACK",
                target_id=track.track_id,
                before_state=before,
                after_state=_track_mixer_state(track),
            )
            return track.track_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=IdempotencyResultType.TRACK_MIXER_UPDATE,
            target_identity=track_id,
            body={
                "gain_db": f"{gain:.2f}",
                "pan": f"{normalized_pan:.2f}",
                "muted": muted,
                "solo": solo,
            },
            mutate=mutate,
            payload=lambda identity: {"track_id": identity},
            resource_type="composition_track",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def set_master_gain(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        master_gain_db: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        gain = _normalize_mixer_gain(master_gain_db, master=True)

        def mutate(repository, session, working):
            before = {"master_gain_db": f"{working.master_gain_db:.2f}"}
            working.master_gain_db = gain
            repository.flush()
            CompositionHistoryRepository(session).append(
                working_composition_id=working.working_composition_id,
                command_type="MASTER_GAIN",
                target_type="WORKING_COMPOSITION",
                target_id=working.working_composition_id,
                before_state=before,
                after_state={"master_gain_db": f"{gain:.2f}"},
            )
            return working.working_composition_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=IdempotencyResultType.MASTER_GAIN_UPDATE,
            target_identity=working_composition_id,
            body={"master_gain_db": f"{gain:.2f}"},
            mutate=mutate,
            payload=lambda identity: {"working_composition_id": identity},
            resource_type="working_composition",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def set_clip_fade(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        fade_in: object,
        fade_out: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        """Persist one absolute Clip-level linear fade envelope."""

        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        fade_in_us = _normalize_fade_duration(fade_in)
        fade_out_us = _normalize_fade_duration(fade_out)
        operation = IdempotencyResultType.CLIP_FADE_UPDATE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            before = {"fade_in": clip.fade_in, "fade_out": clip.fade_out}
            _validate_clip_fade(
                fade_in=fade_in_us,
                fade_out=fade_out_us,
                clip_duration=clip.timeline_duration,
            )
            clip.fade_in = fade_in_us
            clip.fade_out = fade_out_us
            repository.flush()
            CompositionHistoryRepository(_session).append(
                working_composition_id=working.working_composition_id,
                command_type="CLIP_FADE",
                clip_id=clip.clip_id,
                before_state=before,
                after_state={"fade_in": fade_in_us, "fade_out": fade_out_us},
            )
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={"fade_in_us": fade_in_us, "fade_out_us": fade_out_us},
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def set_clip_loop(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        loop_enabled: bool,
        timeline_duration: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        duration_us = _seconds_to_microseconds(timeline_duration, "timeline_duration")
        operation = IdempotencyResultType.CLIP_LOOP_UPDATE

        def mutate(
            repository: CompositionRepository, _session: Session, working: WorkingComposition
        ) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            before = {
                "loop_enabled": clip.loop_enabled,
                "timeline_duration": clip.timeline_duration,
                "loop_phase": clip.loop_phase,
            }
            window = clip.source_out - clip.source_in
            if duration_us <= 0 or (not loop_enabled and duration_us != window):
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID
                )
            _validate_clip_fade(
                fade_in=clip.fade_in, fade_out=clip.fade_out, clip_duration=duration_us
            )
            end = clip.timeline_start + duration_us
            if repository.active_clip_overlap_exists(
                working_composition_id=working.working_composition_id,
                track_id=clip.track_id,
                timeline_start=clip.timeline_start,
                timeline_end=end,
                exclude_clip_id=clip.clip_id,
            ):
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
            clip.loop_enabled = loop_enabled
            clip.timeline_duration = duration_us
            if not loop_enabled or not clip.loop_enabled:
                clip.loop_phase = 0
            repository.flush()
            CompositionHistoryRepository(_session).append(
                working_composition_id=working.working_composition_id,
                command_type="CLIP_LOOP",
                clip_id=clip.clip_id,
                before_state=before,
                after_state={
                    "loop_enabled": clip.loop_enabled,
                    "timeline_duration": clip.timeline_duration,
                    "loop_phase": clip.loop_phase,
                },
            )
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={"loop_enabled": loop_enabled, "timeline_duration_us": duration_us},
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def restore_clip_loop_state(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        loop_enabled: bool,
        timeline_duration: object,
        loop_phase: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        """Restore one canonical Loop state for Frontend-owned Undo/Redo history."""

        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        duration_us = _seconds_to_microseconds(timeline_duration, "timeline_duration")
        phase_us = _seconds_to_microseconds(loop_phase, "loop_phase")
        operation = IdempotencyResultType.CLIP_LOOP_RESTORE

        def mutate(
            repository: CompositionRepository, _session: Session, working: WorkingComposition
        ) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            window = clip.source_out - clip.source_in
            valid_geometry = (loop_enabled and 0 <= phase_us < window) or (
                not loop_enabled and duration_us == window and phase_us == 0
            )
            if duration_us <= 0 or not valid_geometry:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID
                )
            _validate_clip_fade(
                fade_in=clip.fade_in, fade_out=clip.fade_out, clip_duration=duration_us
            )
            end = clip.timeline_start + duration_us
            if repository.active_clip_overlap_exists(
                working_composition_id=working.working_composition_id,
                track_id=clip.track_id,
                timeline_start=clip.timeline_start,
                timeline_end=end,
                exclude_clip_id=clip.clip_id,
            ):
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
            clip.loop_enabled = loop_enabled
            clip.timeline_duration = duration_us
            clip.loop_phase = phase_us
            repository.flush()
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={
                "loop_enabled": loop_enabled,
                "timeline_duration_us": duration_us,
                "loop_phase_us": phase_us,
            },
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def copy_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        target_track_id: UUID,
        target_timeline_start: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        """Copy one active Clip to an explicit destination with a new identity."""

        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        _validate_uuid(target_track_id, "target_track_id")
        target_start_us = _seconds_to_microseconds(target_timeline_start, "target_timeline_start")
        operation = IdempotencyResultType.CLIP_COPY

        def mutate(
            repository: CompositionRepository,
            session: Session,
            working: WorkingComposition,
        ) -> UUID:
            source = self._require_clip(repository, working, clip_id)
            self._require_track(repository, working, target_track_id)
            self._validate_restore_source(
                session,
                project_id=project_id,
                effective_owner_id=effective_owner_id,
                clip=source,
            )
            _validate_clip_range(
                timeline_start=target_start_us,
                source_in=source.source_in,
                source_out=source.source_out,
                source_duration=source.source_duration,
            )
            copied = CompositionClip(
                working_composition_id=working.working_composition_id,
                track_id=target_track_id,
                source_asset_version_id=source.source_asset_version_id,
                timeline_start=target_start_us,
                source_in=source.source_in,
                source_out=source.source_out,
                source_duration=source.source_duration,
                timeline_duration=source.timeline_duration,
                loop_enabled=source.loop_enabled,
                loop_phase=source.loop_phase,
                gain_db=source.gain_db,
                fade_in=source.fade_in,
                fade_out=source.fade_out,
                split_from_clip_id=None,
            )
            try:
                repository.add_composition_clip(copied)
            except ValueError:
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP) from None
            return copied.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={
                "target_track_id": str(target_track_id),
                "target_timeline_start_us": target_start_us,
            },
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=201,
        )

    def trim_clip_start(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        timeline_start: object,
        source_in: object,
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> WorkingMutationResult:
        start_us = _seconds_to_microseconds(timeline_start, "timeline_start")
        source_in_us = _seconds_to_microseconds(source_in, "source_in")
        return self._mutate_clip_absolute(
            project_id=project_id,
            working_composition_id=working_composition_id,
            clip_id=clip_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            changes=lambda clip: _trim_start_changes(
                clip, timeline_start=start_us, source_in=source_in_us
            ),
        )

    def trim_clip_end(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        source_out: object,
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> WorkingMutationResult:
        source_out_us = _seconds_to_microseconds(source_out, "source_out")
        return self._mutate_clip_absolute(
            project_id=project_id,
            working_composition_id=working_composition_id,
            clip_id=clip_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            changes=lambda clip: _trim_end_changes(clip, source_out=source_out_us),
        )

    def split_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        split_at: object,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        split_at_us = _seconds_to_microseconds(split_at, "split_at")
        operation = IdempotencyResultType.CLIP_SPLIT

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> tuple[UUID, UUID, UUID]:
            original = self._require_clip(repository, working, clip_id)
            timeline_end = original.timeline_start + original.timeline_duration
            if not original.timeline_start < split_at_us < timeline_end:
                raise WorkingCompositionError(WorkingCompositionErrorCode.INVALID_CLIP_RANGE)
            split_offset = split_at_us - original.timeline_start
            source_split = original.source_in + split_offset
            clip_duration = original.timeline_duration
            if split_offset < original.fade_in or split_offset > clip_duration - original.fade_out:
                raise WorkingCompositionError(WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT)
            repository.tombstone_composition_clip(original)
            left = CompositionClip(
                working_composition_id=working.working_composition_id,
                track_id=original.track_id,
                source_asset_version_id=original.source_asset_version_id,
                timeline_start=original.timeline_start,
                source_in=original.source_in,
                source_out=original.source_out if original.loop_enabled else source_split,
                source_duration=original.source_duration,
                timeline_duration=split_offset,
                loop_enabled=original.loop_enabled,
                loop_phase=original.loop_phase,
                gain_db=original.gain_db,
                fade_in=original.fade_in,
                fade_out=0,
                split_from_clip_id=original.clip_id,
            )
            right = CompositionClip(
                working_composition_id=working.working_composition_id,
                track_id=original.track_id,
                source_asset_version_id=original.source_asset_version_id,
                timeline_start=split_at_us,
                source_in=original.source_in if original.loop_enabled else source_split,
                source_out=original.source_out,
                source_duration=original.source_duration,
                timeline_duration=original.timeline_duration - split_offset,
                loop_enabled=original.loop_enabled,
                loop_phase=(
                    (original.loop_phase + split_offset)
                    % (original.source_out - original.source_in)
                    if original.loop_enabled
                    else 0
                ),
                gain_db=original.gain_db,
                fade_in=0,
                fade_out=original.fade_out,
                split_from_clip_id=original.clip_id,
            )
            try:
                repository.add_composition_clip(left)
                repository.add_composition_clip(right)
            except ValueError:
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP) from None
            return original.clip_id, left.clip_id, right.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={"split_at_us": split_at_us},
            mutate=mutate,
            payload=lambda identities: {
                "original_clip_id": identities[0],
                "left_clip_id": identities[1],
                "right_clip_id": identities[2],
            },
            resource_type="composition_clip_split",
            resource_id=lambda identities: identities[0],
            response_status=200,
        )

    def delete_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        operation = IdempotencyResultType.CLIP_DELETE

        def mutate(
            repository: CompositionRepository,
            _session: Session,
            working: WorkingComposition,
        ) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            repository.tombstone_composition_clip(clip)
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={},
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def restore_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")
        operation = IdempotencyResultType.CLIP_RESTORE

        def mutate(
            repository: CompositionRepository,
            session: Session,
            working: WorkingComposition,
        ) -> UUID:
            clip = self._require_clip_any(repository, working, clip_id)
            if clip.deleted_at is None:
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_ALREADY_ACTIVE)
            self._require_track(repository, working, clip.track_id)
            self._validate_restore_source(
                session,
                project_id=project_id,
                effective_owner_id=effective_owner_id,
                clip=clip,
            )
            timeline_end = clip.timeline_start + clip.timeline_duration
            if repository.active_clip_overlap_exists(
                working_composition_id=working.working_composition_id,
                track_id=clip.track_id,
                timeline_start=clip.timeline_start,
                timeline_end=timeline_end,
            ):
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
            repository.restore_composition_clip(clip)
            return clip.clip_id

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=clip_id,
            body={},
            mutate=mutate,
            payload=lambda identity: {"clip_id": identity},
            resource_type="composition_clip",
            resource_id=lambda identity: identity,
            response_status=200,
        )

    def unsplit_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        original_clip_id: UUID,
        left_clip_id: UUID,
        right_clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        return self._toggle_split(
            project_id=project_id,
            working_composition_id=working_composition_id,
            original_clip_id=original_clip_id,
            left_clip_id=left_clip_id,
            right_clip_id=right_clip_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
            operation=IdempotencyResultType.CLIP_UNSPLIT,
            activate_original=True,
        )

    def resplit_clip(
        self,
        project_id: UUID,
        *,
        working_composition_id: UUID,
        original_clip_id: UUID,
        left_clip_id: UUID,
        right_clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
    ) -> WorkingMutationResult:
        return self._toggle_split(
            project_id=project_id,
            working_composition_id=working_composition_id,
            original_clip_id=original_clip_id,
            left_clip_id=left_clip_id,
            right_clip_id=right_clip_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
            operation=IdempotencyResultType.CLIP_RESPLIT,
            activate_original=False,
        )

    def _toggle_split(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        original_clip_id: UUID,
        left_clip_id: UUID,
        right_clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
        operation: IdempotencyResultType,
        activate_original: bool,
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        identities = (original_clip_id, left_clip_id, right_clip_id)
        for identity in identities:
            _validate_uuid(identity, "clip_id")
        if len(set(identities)) != 3:
            raise WorkingCompositionError(WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT)

        def mutate(
            repository: CompositionRepository,
            session: Session,
            working: WorkingComposition,
        ) -> tuple[UUID, UUID, UUID]:
            original = self._require_clip_any(repository, working, original_clip_id)
            left = self._require_clip_any(repository, working, left_clip_id)
            right = self._require_clip_any(repository, working, right_clip_id)
            if activate_original:
                valid_state = (
                    original.deleted_at is not None
                    and left.deleted_at is None
                    and right.deleted_at is None
                )
            else:
                valid_state = (
                    original.deleted_at is None
                    and left.deleted_at is not None
                    and right.deleted_at is not None
                )
            if not valid_state or not _split_geometry_matches(original, left, right):
                raise WorkingCompositionError(WorkingCompositionErrorCode.SPLIT_STRUCTURE_CONFLICT)
            self._require_track(repository, working, original.track_id)
            self._validate_restore_source(
                session,
                project_id=project_id,
                effective_owner_id=effective_owner_id,
                clip=original,
            )

            if activate_original:
                repository.tombstone_composition_clip(left)
                repository.tombstone_composition_clip(right)
                original_end = original.timeline_start + original.timeline_duration
                if repository.active_clip_overlap_exists(
                    working_composition_id=working.working_composition_id,
                    track_id=original.track_id,
                    timeline_start=original.timeline_start,
                    timeline_end=original_end,
                ):
                    raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
                repository.restore_composition_clip(original)
            else:
                repository.tombstone_composition_clip(original)
                for child in (left, right):
                    child_end = child.timeline_start + child.timeline_duration
                    if repository.active_clip_overlap_exists(
                        working_composition_id=working.working_composition_id,
                        track_id=child.track_id,
                        timeline_start=child.timeline_start,
                        timeline_end=child_end,
                    ):
                        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
                repository.restore_composition_clip(left)
                repository.restore_composition_clip(right)
            return identities

        return self._run_idempotent_mutation(
            **normalized,
            idempotency_key=idempotency_key,
            operation=operation,
            target_identity=original_clip_id,
            body={
                "left_clip_id": str(left_clip_id),
                "right_clip_id": str(right_clip_id),
            },
            mutate=mutate,
            payload=lambda value: {
                "original_clip_id": value[0],
                "left_clip_id": value[1],
                "right_clip_id": value[2],
            },
            resource_type="composition_clip_split",
            resource_id=lambda value: value[0],
            response_status=200,
        )

    def _mutate_clip_absolute(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        clip_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        changes: Callable[[CompositionClip], dict[str, int]],
    ) -> WorkingMutationResult:
        normalized = self._normalize_mutation(
            project_id=project_id,
            working_composition_id=working_composition_id,
            expected_revision=expected_revision,
            effective_owner_id=effective_owner_id,
        )
        _validate_uuid(clip_id, "clip_id")

        def mutate(repository: CompositionRepository, working: WorkingComposition) -> UUID:
            clip = self._require_clip(repository, working, clip_id)
            values = changes(clip)
            timeline_start = values.get("timeline_start", clip.timeline_start)
            source_in = values.get("source_in", clip.source_in)
            source_out = values.get("source_out", clip.source_out)
            _validate_clip_range(
                timeline_start=timeline_start,
                source_in=source_in,
                source_out=source_out,
                source_duration=clip.source_duration,
            )
            timeline_duration = values.get("timeline_duration", clip.timeline_duration)
            loop_enabled = bool(values.get("loop_enabled", clip.loop_enabled))
            loop_phase = values.get("loop_phase", clip.loop_phase)
            _validate_loop_geometry(
                source_in=source_in,
                source_out=source_out,
                timeline_duration=timeline_duration,
                loop_enabled=loop_enabled,
                loop_phase=loop_phase,
            )
            _validate_clip_fade(
                fade_in=clip.fade_in,
                fade_out=clip.fade_out,
                clip_duration=timeline_duration,
            )
            timeline_end = timeline_start + timeline_duration
            if repository.active_clip_overlap_exists(
                working_composition_id=working.working_composition_id,
                track_id=clip.track_id,
                timeline_start=timeline_start,
                timeline_end=timeline_end,
                exclude_clip_id=clip.clip_id,
            ):
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)
            for field_name, value in values.items():
                setattr(clip, field_name, value)
            repository.flush()
            return clip.clip_id

        return self._run_absolute_mutation(**normalized, mutate=mutate)

    def _run_absolute_mutation(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        mutate: Callable[[CompositionRepository, WorkingComposition], UUID],
    ) -> WorkingMutationResult:
        with self.session_factory() as session, session.begin():
            self._require_project_scope(session, project_id, effective_owner_id)
            repository = CompositionRepository(session)
            working = self._require_working(repository, project_id, working_composition_id)
            _require_expected_revision(working, expected_revision)
            identity = mutate(repository, working)
            CompositionHistoryRepository(session).clear(working_composition_id)
            revision = self._increment_revision(
                repository, working_composition_id, expected_revision
            )
            return WorkingMutationResult(
                completed_revision=revision,
                replayed=False,
                result_type=None,
                identities={"resource_id": identity},
            )

    def _run_idempotent_mutation(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
        idempotency_key: str,
        operation: IdempotencyResultType,
        target_identity: UUID | None,
        body: Mapping[str, object],
        mutate: Callable[[CompositionRepository, Session, WorkingComposition], object],
        payload: Callable[[object], Mapping[str, UUID]],
        resource_type: str,
        resource_id: Callable[[object], UUID],
        response_status: int,
    ) -> WorkingMutationResult:
        key = _normalize_idempotency_key(idempotency_key)
        fingerprint = _fingerprint(
            effective_owner_id=effective_owner_id,
            project_id=project_id,
            working_composition_id=working_composition_id,
            operation=operation.value,
            expected_revision=expected_revision,
            target_identity=target_identity,
            body=body,
        )
        scope = (
            f"working-composition:{effective_owner_id}:{project_id}:"
            f"{working_composition_id}:{operation.value}"
        )
        with self.session_factory() as session, session.begin():
            self._require_project_scope(session, project_id, effective_owner_id)
            idempotency = IdempotencyRepository(session)
            claim = _claim_with_result(
                idempotency,
                scope=scope,
                key=key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(claim, operation)
            if replay is not None:
                return replay
            repository = CompositionRepository(session)
            working = self._require_working(repository, project_id, working_composition_id)
            _require_expected_revision(working, expected_revision)
            identity = mutate(repository, session, working)
            if operation not in {
                IdempotencyResultType.CLIP_GAIN_UPDATE,
                IdempotencyResultType.CLIP_FADE_UPDATE,
                IdempotencyResultType.CLIP_LOOP_UPDATE,
                IdempotencyResultType.TRACK_MIXER_UPDATE,
                IdempotencyResultType.MASTER_GAIN_UPDATE,
                IdempotencyResultType.WORKING_HISTORY_UNDO,
                IdempotencyResultType.WORKING_HISTORY_REDO,
                IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT,
            }:
                CompositionHistoryRepository(session).clear(working_composition_id)
            revision = self._increment_revision(
                repository, working_composition_id, expected_revision
            )
            result_payload = payload(identity)
            return _complete_result(
                idempotency,
                claim,
                completed_revision=revision,
                result_type=operation,
                payload=result_payload,
                resource_type=resource_type,
                resource_id=resource_id(identity),
                response_status=response_status,
            )

    def _checkout_rows_with_history_barrier(
        self,
        repository: CompositionRepository,
        session: Session,
        working: WorkingComposition,
        *,
        composition_snapshot_id: UUID,
    ) -> UUID:
        identity = self._checkout_rows(
            repository, working, composition_snapshot_id=composition_snapshot_id
        )
        CompositionHistoryRepository(session).clear(working.working_composition_id)
        return identity

    def _checkout_rows(
        self,
        repository: CompositionRepository,
        working: WorkingComposition,
        *,
        composition_snapshot_id: UUID,
    ) -> UUID:
        snapshot = repository.get_project_snapshot(working.project_id, composition_snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("CompositionSnapshot")
        snapshot_tracks = repository.list_snapshot_tracks(composition_snapshot_id)
        if not snapshot_tracks:
            raise WorkingCompositionError(
                WorkingCompositionErrorCode.SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE
            )
        snapshot_clips = repository.list_snapshot_clips_for_snapshot(composition_snapshot_id)
        self._validate_checkout_overlap(snapshot_tracks, snapshot_clips)

        for clip in repository.list_working_composition_clips(working.working_composition_id):
            repository.tombstone_composition_clip(clip)
        for track in repository.list_active_composition_tracks(working.working_composition_id):
            repository.tombstone_composition_track(track)

        track_by_snapshot_id = {}
        for source in snapshot_tracks:
            target = repository.get_composition_track(
                working.working_composition_id,
                source.canonical_track_id,
                include_deleted=True,
            )
            if target is None:
                target = CompositionTrack(
                    track_id=source.canonical_track_id,
                    working_composition_id=working.working_composition_id,
                    track_type=source.track_type,
                    name=source.name,
                    track_order=source.track_order,
                    gain_db=source.gain_db,
                    pan=source.pan,
                    muted=source.muted,
                    solo=source.solo,
                )
                repository.add_composition_track(target)
            else:
                target.track_type = source.track_type
                target.name = source.name
                target.track_order = source.track_order
                target.gain_db = source.gain_db
                target.pan = source.pan
                target.muted = source.muted
                target.solo = source.solo
                target.deleted_at = None
                repository.flush()
            track_by_snapshot_id[source.snapshot_track_id] = target.track_id

        for source in snapshot_clips:
            target = repository.get_composition_clip(
                working.working_composition_id,
                source.canonical_clip_id,
                include_deleted=True,
            )
            values = {
                "track_id": track_by_snapshot_id[source.snapshot_track_id],
                "source_asset_version_id": source.source_asset_version_id,
                "timeline_start": source.timeline_start,
                "source_in": source.source_in,
                "source_out": source.source_out,
                "source_duration": source.source_duration,
                "timeline_duration": source.timeline_duration,
                "loop_enabled": source.loop_enabled,
                "loop_phase": source.loop_phase,
                "gain_db": source.gain_db,
                "fade_in": source.fade_in,
                "fade_out": source.fade_out,
                "split_from_clip_id": source.split_from_clip_id,
            }
            if target is None:
                repository.add_composition_clip(
                    CompositionClip(
                        clip_id=source.canonical_clip_id,
                        working_composition_id=working.working_composition_id,
                        **values,
                    )
                )
            else:
                for field_name, value in values.items():
                    setattr(target, field_name, value)
                target.deleted_at = None
                repository.flush()
        working.base_composition_snapshot_id = composition_snapshot_id
        working.master_gain_db = snapshot.master_gain_db
        repository.flush()
        return working.working_composition_id

    @staticmethod
    def _validate_checkout_overlap(snapshot_tracks: Sequence, snapshot_clips: Sequence) -> None:
        known_tracks = {track.snapshot_track_id for track in snapshot_tracks}
        by_track: dict[UUID, list[tuple[int, int]]] = {}
        for clip in snapshot_clips:
            if clip.snapshot_track_id not in known_tracks:
                raise WorkingCompositionError(
                    WorkingCompositionErrorCode.SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE
                )
            _validate_clip_range(
                timeline_start=clip.timeline_start,
                source_in=clip.source_in,
                source_out=clip.source_out,
                source_duration=clip.source_duration,
            )
            interval = (
                clip.timeline_start,
                clip.timeline_start + clip.timeline_duration,
            )
            by_track.setdefault(clip.snapshot_track_id, []).append(interval)
        for intervals in by_track.values():
            ordered = sorted(intervals)
            if any(left[1] > right[0] for left, right in pairwise(ordered)):
                raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_OVERLAP)

    def _resolve_source_duration(
        self,
        session: Session,
        *,
        project_id: UUID,
        workspace_id: UUID,
        effective_owner_id: UUID,
        asset_version_id: UUID,
    ) -> int:
        return self._resolve_source_metadata(
            session,
            project_id=project_id,
            workspace_id=workspace_id,
            effective_owner_id=effective_owner_id,
            asset_version_id=asset_version_id,
        ).duration_us

    def _resolve_source_metadata(
        self,
        session: Session,
        *,
        project_id: UUID,
        workspace_id: UUID,
        effective_owner_id: UUID,
        asset_version_id: UUID,
    ) -> TrustedClipSourceMetadata:
        assets = AssetRepository(session)
        version = assets.get_asset_version(asset_version_id)
        if version is None:
            raise WorkingCompositionError(WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE)
        asset = assets.get_asset(version.asset_id)
        if (
            asset is None
            or asset.owner_id != effective_owner_id
            or asset.workspace_id not in {None, workspace_id}
            or asset.deleted_at is not None
            or asset.lifecycle_status != "active"
            or asset.asset_type not in AUDIO_ASSET_TYPES
            or not WorkspaceRepository(session).project_asset_exists(project_id, asset.asset_id)
        ):
            raise WorkingCompositionError(WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE)
        try:
            return TrustedMediaMetadataService(assets).resolve_clip_source(asset_version_id)
        except TrustedMediaMetadataError as error:
            mapping = {
                TrustedMediaMetadataErrorCode.SOURCE_ARTIFACT_NOT_FOUND: (
                    WorkingCompositionErrorCode.SOURCE_ASSET_UNAVAILABLE
                ),
                TrustedMediaMetadataErrorCode.SOURCE_ARTIFACT_AMBIGUOUS: (
                    WorkingCompositionErrorCode.SOURCE_ARTIFACT_AMBIGUOUS
                ),
                TrustedMediaMetadataErrorCode.SOURCE_DURATION_UNAVAILABLE: (
                    WorkingCompositionErrorCode.SOURCE_DURATION_UNAVAILABLE
                ),
            }
            raise WorkingCompositionError(mapping[error.code]) from None

    def _validate_restore_source(
        self,
        session: Session,
        *,
        project_id: UUID,
        effective_owner_id: UUID,
        clip: CompositionClip,
    ) -> None:
        """Recheck current eligibility without replacing frozen Clip geometry."""

        self._resolve_source_duration(
            session,
            project_id=project_id,
            workspace_id=self._project_workspace_id(session, project_id, effective_owner_id),
            effective_owner_id=effective_owner_id,
            asset_version_id=clip.source_asset_version_id,
        )

    def _normalize_mutation(
        self,
        *,
        project_id: UUID,
        working_composition_id: UUID,
        expected_revision: int,
        effective_owner_id: UUID,
    ) -> dict[str, object]:
        _validate_uuid(project_id, "project_id")
        _validate_uuid(working_composition_id, "working_composition_id")
        _validate_uuid(effective_owner_id, "effective_owner_id")
        _validate_expected_revision(expected_revision)
        return {
            "project_id": project_id,
            "working_composition_id": working_composition_id,
            "expected_revision": expected_revision,
            "effective_owner_id": effective_owner_id,
        }

    @staticmethod
    def _require_working(
        repository: CompositionRepository,
        project_id: UUID,
        working_composition_id: UUID,
    ) -> WorkingComposition:
        working = repository.get_working_composition(working_composition_id)
        if working is None or working.project_id != project_id:
            raise WorkingCompositionError(WorkingCompositionErrorCode.WORKING_COMPOSITION_NOT_FOUND)
        return working

    @staticmethod
    def _require_track(
        repository: CompositionRepository,
        working: WorkingComposition,
        track_id: UUID,
    ) -> CompositionTrack:
        track = repository.get_composition_track(working.working_composition_id, track_id)
        if track is None:
            raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_NOT_FOUND)
        return track

    @staticmethod
    def _require_track_any(
        repository: CompositionRepository,
        working: WorkingComposition,
        track_id: UUID,
    ) -> CompositionTrack:
        track = repository.get_composition_track(
            working.working_composition_id, track_id, include_deleted=True
        )
        if track is None:
            raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_NOT_FOUND)
        return track

    @staticmethod
    def _require_clip(
        repository: CompositionRepository,
        working: WorkingComposition,
        clip_id: UUID,
    ) -> CompositionClip:
        clip = repository.get_composition_clip(working.working_composition_id, clip_id)
        if clip is None:
            raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_NOT_FOUND)
        return clip

    @staticmethod
    def _require_clip_any(
        repository: CompositionRepository,
        working: WorkingComposition,
        clip_id: UUID,
    ) -> CompositionClip:
        clip = repository.get_composition_clip(
            working.working_composition_id, clip_id, include_deleted=True
        )
        if clip is None:
            raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_NOT_FOUND)
        return clip

    @staticmethod
    def _increment_revision(
        repository: CompositionRepository,
        working_composition_id: UUID,
        expected_revision: int,
    ) -> int:
        revision = repository.increment_working_revision(
            working_composition_id, expected_revision=expected_revision
        )
        if revision is None:
            raise WorkingCompositionError(
                WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
            )
        return revision

    @staticmethod
    def _require_project_scope(
        session: Session,
        project_id: UUID,
        effective_owner_id: UUID,
    ) -> MusicProject:
        repository = WorkspaceRepository(session)
        if not repository.list_workspaces(owner_id=effective_owner_id, limit=1):
            raise WorkspaceBootstrapRequiredError()
        project = repository.get_project(project_id)
        if project is None or project.lifecycle_status != "active":
            raise ResourceNotFoundError("MusicProject")
        workspace = repository.get_workspace_for_owner(project.workspace_id, effective_owner_id)
        if workspace is None or workspace.lifecycle_status != "active":
            raise ResourceNotFoundError("MusicProject")
        return project

    def _project_workspace_id(
        self, session: Session, project_id: UUID, effective_owner_id: UUID
    ) -> UUID:
        return self._require_project_scope(session, project_id, effective_owner_id).workspace_id

    def _project_has_working_composition(self, project_id: UUID, effective_owner_id: UUID) -> bool:
        with self.session_factory() as session:
            self._require_project_scope(session, project_id, effective_owner_id)
            return (
                CompositionRepository(session).get_project_working_composition(project_id)
                is not None
            )

    @staticmethod
    def _load_aggregate(
        repository: CompositionRepository, working: WorkingComposition
    ) -> WorkingCompositionAggregate:
        tracks = repository.list_active_composition_tracks(working.working_composition_id)
        clips = repository.list_working_composition_clips(working.working_composition_id)
        duration = max(
            (clip.timeline_start + clip.timeline_duration for clip in clips),
            default=0,
        )
        return WorkingCompositionAggregate(
            working_composition=working,
            tracks=tuple(tracks),
            clips=tuple(clips),
            timeline_duration_us=duration,
        )


def _claim_with_result(
    repository: IdempotencyRepository,
    *,
    scope: str,
    key: str,
    fingerprint: str,
) -> IdempotencyClaim:
    try:
        return repository.claim_with_result(
            scope=scope,
            key=key,
            fingerprint=fingerprint,
            now=datetime.now(UTC),
            ttl_hours=IDEMPOTENCY_TTL_HOURS,
        )
    except ValueError as error:
        if str(error) == "IDEMPOTENCY_CONFLICT":
            raise IdempotencyConflictError() from None
        if str(error) == "IDEMPOTENCY_IN_PROGRESS":
            raise IdempotencyInProgressError() from None
        raise IdempotencyConflictError() from None


def _replay_result(
    claim: IdempotencyClaim,
    expected_type: IdempotencyResultType,
) -> WorkingMutationResult | None:
    if not claim.replayed:
        return None
    result = claim.completion_result
    if result is None or result.result_type is not expected_type:
        raise IdempotencyConflictError()
    return WorkingMutationResult(
        completed_revision=result.completed_revision,
        replayed=True,
        result_type=result.result_type,
        identities={
            key: (value if key == "target_type" else UUID(value))
            for key, value in result.result_payload.items()
        },
    )


def _complete_result(
    repository: IdempotencyRepository,
    claim: IdempotencyClaim,
    *,
    completed_revision: int,
    result_type: IdempotencyResultType,
    payload: Mapping[str, UUID | str],
    resource_type: str,
    resource_id: UUID,
    response_status: int,
) -> WorkingMutationResult:
    completion = IdempotencyCompletionResult.create(
        completed_revision=completed_revision,
        result_type=result_type,
        result_payload={key: str(value) for key, value in payload.items()},
    )
    repository.complete_with_result(
        claim.record,
        resource_type=resource_type,
        resource_id=str(resource_id),
        response_status=response_status,
        completion_result=completion,
    )
    return WorkingMutationResult(
        completed_revision=completed_revision,
        replayed=False,
        result_type=result_type,
        identities=dict(payload),
    )


def _fingerprint(
    *,
    effective_owner_id: UUID,
    project_id: UUID,
    working_composition_id: UUID | None,
    operation: str,
    expected_revision: int | None,
    target_identity: UUID | None,
    body: Mapping[str, object],
) -> str:
    canonical = json.dumps(
        {
            "body": dict(body),
            "effective_owner_id": str(effective_owner_id),
            "expected_revision": expected_revision,
            "operation": operation,
            "project_id": str(project_id),
            "target_identity": (str(target_identity) if target_identity is not None else None),
            "working_composition_id": (
                str(working_composition_id) if working_composition_id is not None else None
            ),
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise ApplicationValidationError("Idempotency-Key가 필요합니다.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApplicationValidationError("Idempotency-Key가 필요합니다.")
    return normalized


def _normalize_track_name(value: object) -> str:
    if not isinstance(value, str):
        raise ApplicationValidationError("Track name은 문자열이어야 합니다.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_TRACK_NAME_LENGTH:
        raise ApplicationValidationError(
            f"Track name은 1자 이상 {MAX_TRACK_NAME_LENGTH}자 이하여야 합니다."
        )
    return normalized


def _validate_uuid(value: object, field_name: str) -> None:
    if type(value) is not UUID:
        raise ApplicationValidationError(f"{field_name} 형식이 유효하지 않습니다.")


def _validate_expected_revision(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ApplicationValidationError("expected_revision은 0 이상의 정수여야 합니다.")


def _require_expected_revision(working: WorkingComposition, expected_revision: int) -> None:
    if working.revision != expected_revision:
        raise WorkingCompositionError(
            WorkingCompositionErrorCode.WORKING_COMPOSITION_REVISION_CONFLICT
        )


def _seconds_to_microseconds(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ApplicationValidationError(f"{field_name} 값이 유효하지 않습니다.")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ApplicationValidationError(f"{field_name} 값이 유효하지 않습니다.") from None
    if not decimal.is_finite():
        raise ApplicationValidationError(f"{field_name} 값이 유효하지 않습니다.")
    return int((decimal * MICROSECONDS_PER_SECOND).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _validate_clip_range(
    *,
    timeline_start: int,
    source_in: int,
    source_out: int,
    source_duration: int,
) -> None:
    if not (
        timeline_start >= 0
        and source_in >= 0
        and source_out > source_in
        and source_out <= source_duration
    ):
        raise WorkingCompositionError(WorkingCompositionErrorCode.INVALID_CLIP_RANGE)


def _normalize_clip_gain_db(value: object) -> Decimal:
    if isinstance(value, (bool, str)):
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE)
    try:
        gain = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE) from None
    if not gain.is_finite():
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE)
    try:
        quantized = gain.quantize(CLIP_GAIN_DB_QUANTUM)
    except InvalidOperation:
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE) from None
    if gain != quantized or not MIN_CLIP_GAIN_DB <= quantized <= MAX_CLIP_GAIN_DB:
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_GAIN_OUT_OF_RANGE)
    return quantized


def _normalize_pan(value: object) -> Decimal:
    if isinstance(value, (bool, str)):
        raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE)
    try:
        pan = value if isinstance(value, Decimal) else Decimal(str(value))
        quantized = pan.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise WorkingCompositionError(
            WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE
        ) from None
    if not pan.is_finite() or pan != quantized or not Decimal("-1") <= pan <= Decimal("1"):
        raise WorkingCompositionError(WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE)
    return quantized


def _track_mixer_state(track: CompositionTrack) -> dict[str, object]:
    return {
        "gain_db": f"{track.gain_db:.2f}",
        "pan": f"{track.pan:.2f}",
        "muted": track.muted,
        "solo": track.solo,
    }


def _normalize_mixer_gain(value: object, *, master: bool) -> Decimal:
    try:
        return _normalize_clip_gain_db(value)
    except WorkingCompositionError:
        code = (
            WorkingCompositionErrorCode.MASTER_GAIN_OUT_OF_RANGE
            if master
            else WorkingCompositionErrorCode.TRACK_MIXER_OUT_OF_RANGE
        )
        raise WorkingCompositionError(code) from None


def _history_bool(value: object) -> bool:
    if type(value) is not bool:
        raise WorkingCompositionError(
            WorkingCompositionErrorCode.WORKING_HISTORY_STRUCTURE_CONFLICT
        )
    return value


def _normalize_fade_duration(value: object) -> int:
    if isinstance(value, (bool, str)):
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE)
    try:
        duration = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE) from None
    if not duration.is_finite() or duration < 0:
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE)
    microseconds = duration * MICROSECONDS_PER_SECOND
    if microseconds != microseconds.to_integral_value():
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE)
    return int(microseconds)


def _validate_clip_fade(*, fade_in: int, fade_out: int, clip_duration: int) -> None:
    if fade_in < 0 or fade_out < 0 or fade_in + fade_out > clip_duration:
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_FADE_OUT_OF_RANGE)


def _validate_loop_geometry(
    *, source_in: int, source_out: int, timeline_duration: int, loop_enabled: bool, loop_phase: int
) -> None:
    window = source_out - source_in
    if timeline_duration <= 0 or loop_phase < 0 or loop_phase >= window:
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID)
    if not loop_enabled and (timeline_duration != window or loop_phase != 0):
        raise WorkingCompositionError(WorkingCompositionErrorCode.CLIP_LOOP_GEOMETRY_INVALID)


def _trim_start_changes(
    clip: CompositionClip, *, timeline_start: int, source_in: int
) -> dict[str, int]:
    advance = timeline_start - clip.timeline_start
    if clip.loop_enabled:
        return {
            "timeline_start": timeline_start,
            "timeline_duration": clip.timeline_duration - advance,
            "loop_phase": (clip.loop_phase + advance) % (clip.source_out - clip.source_in),
        }
    return {
        "timeline_start": timeline_start,
        "source_in": source_in,
        "timeline_duration": clip.source_out - source_in,
        "loop_phase": 0,
    }


def _trim_end_changes(clip: CompositionClip, *, source_out: int) -> dict[str, int]:
    if clip.loop_enabled:
        reduction = clip.source_out - source_out
        return {"timeline_duration": clip.timeline_duration - reduction}
    return {
        "source_out": source_out,
        "timeline_duration": source_out - clip.source_in,
        "loop_phase": 0,
    }


def _split_geometry_matches(
    original: CompositionClip,
    left: CompositionClip,
    right: CompositionClip,
) -> bool:
    original_end = original.timeline_start + original.source_out - original.source_in
    left_end = left.timeline_start + left.source_out - left.source_in
    right_end = right.timeline_start + right.source_out - right.source_in
    return (
        left.working_composition_id == original.working_composition_id
        and right.working_composition_id == original.working_composition_id
        and left.track_id == original.track_id
        and right.track_id == original.track_id
        and left.split_from_clip_id == original.clip_id
        and right.split_from_clip_id == original.clip_id
        and left.source_asset_version_id == original.source_asset_version_id
        and right.source_asset_version_id == original.source_asset_version_id
        and left.source_duration == original.source_duration
        and right.source_duration == original.source_duration
        and left.gain_db == original.gain_db
        and right.gain_db == original.gain_db
        and left.fade_in == original.fade_in
        and left.fade_out == 0
        and right.fade_in == 0
        and right.fade_out == original.fade_out
        and left.source_in == original.source_in
        and left.source_out == right.source_in
        and right.source_out == original.source_out
        and left.timeline_start == original.timeline_start
        and left_end == right.timeline_start
        and right_end == original_end
    )
