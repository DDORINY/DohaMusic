"""WorkingComposition editing product API."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status

from backend.api.v1.dependencies import (
    get_effective_owner_id,
    get_request_id,
    get_working_composition_service,
    get_working_preview_service,
)
from backend.api.v1.routes.common import (
    map_working_composition_error,
    map_working_preview_error,
    reject_owner_input,
)
from backend.schemas.workspace import (
    CheckoutResult,
    ClipCopyRequest,
    ClipCreateRequest,
    ClipDetail,
    ClipFadeUpdateRequest,
    ClipGainUpdateRequest,
    ClipLoopRestoreRequest,
    ClipLoopUpdateRequest,
    ClipMoveRequest,
    ClipMutationResult,
    ClipSplitRequest,
    ClipToggleRequest,
    ClipTrimEndRequest,
    ClipTrimStartRequest,
    CompositionCommitResult,
    HistoryMutationResult,
    InitializeResult,
    MasterGainMutationResult,
    MasterGainUpdateRequest,
    ReorderTracksResult,
    SplitClipResult,
    SuccessResponse,
    TrackCreateRequest,
    TrackDetail,
    TrackMixerUpdateRequest,
    TrackMutationResult,
    TrackRenameRequest,
    TrackReorderRequest,
    TrackRestoreRequest,
    WorkingCompositionCheckoutRequest,
    WorkingCompositionCommitRequest,
    WorkingCompositionDetail,
    WorkingCompositionInitializeRequest,
    WorkingHistoryDetail,
    WorkingHistoryMutationRequest,
    WorkingMutationRequest,
    WorkingPreviewCreateRequest,
    WorkingPreviewCreateResult,
)
from backend.services.workspace import (
    WorkingCompositionAggregate,
    WorkingCompositionService,
    WorkingMutationResult,
    WorkingPreviewService,
)

router = APIRouter(
    prefix="/projects/{project_id}/working-composition",
    tags=["WorkingComposition"],
    dependencies=[Depends(reject_owner_input)],
)
WorkingCompositionServiceDependency = Annotated[
    WorkingCompositionService, Depends(get_working_composition_service)
]
WorkingPreviewServiceDependency = Annotated[
    WorkingPreviewService, Depends(get_working_preview_service)
]
EffectiveOwnerDependency = Annotated[UUID, Depends(get_effective_owner_id)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="WorkingComposition mutation을 구분하는 opaque key",
    ),
]


@router.get(
    "/history",
    response_model=SuccessResponse[WorkingHistoryDetail],
    operation_id="get_working_composition_history",
)
def get_working_composition_history(
    project_id: UUID,
    working_composition_id: UUID,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[WorkingHistoryDetail]:
    try:
        state = service.get_history_state(
            project_id,
            working_composition_id=working_composition_id,
            effective_owner_id=effective_owner_id,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, WorkingHistoryDetail(**state))


def _history_mutation(
    project_id: UUID,
    payload: WorkingHistoryMutationRequest,
    request: Request,
    service: WorkingCompositionService,
    owner_id: UUID,
    key: str,
    *,
    redo: bool,
):
    try:
        operation = service.redo_history if redo else service.undo_history
        result = operation(
            project_id,
            working_composition_id=payload.working_composition_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=owner_id,
            idempotency_key=key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _history_result(result))


@router.post(
    "/history/undo",
    response_model=SuccessResponse[HistoryMutationResult],
    operation_id="undo_working_composition_history",
)
def undo_working_composition_history(
    project_id: UUID,
    payload: WorkingHistoryMutationRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[HistoryMutationResult]:
    return _history_mutation(
        project_id, payload, request, service, effective_owner_id, idempotency_key, redo=False
    )


@router.post(
    "/history/redo",
    response_model=SuccessResponse[HistoryMutationResult],
    operation_id="redo_working_composition_history",
)
def redo_working_composition_history(
    project_id: UUID,
    payload: WorkingHistoryMutationRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[HistoryMutationResult]:
    return _history_mutation(
        project_id, payload, request, service, effective_owner_id, idempotency_key, redo=True
    )


@router.patch(
    "/tracks/{track_id}/mixer",
    response_model=SuccessResponse[TrackMutationResult],
    operation_id="update_working_composition_track_mixer",
)
def update_track_mixer(
    project_id: UUID,
    track_id: UUID,
    payload: TrackMixerUpdateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[TrackMutationResult]:
    try:
        result = service.set_track_mixer(
            project_id,
            working_composition_id=payload.working_composition_id,
            track_id=track_id,
            gain_db=payload.gain_db,
            pan=payload.pan,
            muted=payload.muted,
            solo=payload.solo,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _track_result(result))


@router.patch(
    "/master-gain",
    response_model=SuccessResponse[MasterGainMutationResult],
    operation_id="update_working_composition_master_gain",
)
def update_master_gain(
    project_id: UUID,
    payload: MasterGainUpdateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[MasterGainMutationResult]:
    try:
        result = service.set_master_gain(
            project_id,
            working_composition_id=payload.working_composition_id,
            master_gain_db=payload.master_gain_db,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        MasterGainMutationResult(
            working_composition_id=result.identities["working_composition_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


@router.post(
    "/preview",
    response_model=SuccessResponse[WorkingPreviewCreateResult],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_working_composition_preview",
)
def create_working_composition_preview(
    project_id: UUID,
    payload: WorkingPreviewCreateRequest,
    request: Request,
    service: WorkingPreviewServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[WorkingPreviewCreateResult]:
    try:
        result = service.create_for_owner(
            project_id=project_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_preview_error(exc) from exc
    return _success(
        request,
        WorkingPreviewCreateResult(
            job_id=result.job_id,
            preview_render_id=result.preview_render_id,
            working_composition_id=result.working_composition_id,
            rendered_revision=result.rendered_revision,
            status=result.status.value,
            replayed=result.replayed,
        ),
    )


@router.get(
    "",
    response_model=SuccessResponse[WorkingCompositionDetail],
    operation_id="get_working_composition",
)
def get_working_composition(
    project_id: UUID,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[WorkingCompositionDetail]:
    try:
        aggregate = service.get_working_composition(
            project_id, effective_owner_id=effective_owner_id
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _aggregate_detail(aggregate))


@router.post(
    "/initialize",
    response_model=SuccessResponse[InitializeResult],
    status_code=status.HTTP_201_CREATED,
    operation_id="initialize_working_composition",
)
def initialize_working_composition(
    project_id: UUID,
    _payload: WorkingCompositionInitializeRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[InitializeResult]:
    try:
        result = service.initialize(
            project_id,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        InitializeResult(
            working_composition_id=result.identities["working_composition_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


@router.post(
    "/commit",
    response_model=SuccessResponse[CompositionCommitResult],
    status_code=status.HTTP_201_CREATED,
    operation_id="commit_working_composition",
)
def commit_working_composition(
    project_id: UUID,
    payload: WorkingCompositionCommitRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[CompositionCommitResult]:
    try:
        result = service.commit(
            project_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        CompositionCommitResult(
            working_composition_id=result.identities["working_composition_id"],
            composition_snapshot_id=result.identities["composition_snapshot_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


@router.post(
    "/checkout",
    response_model=SuccessResponse[CheckoutResult],
    operation_id="checkout_working_composition",
)
def checkout_working_composition(
    project_id: UUID,
    payload: WorkingCompositionCheckoutRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[CheckoutResult]:
    try:
        result = service.checkout(
            project_id,
            working_composition_id=payload.working_composition_id,
            composition_snapshot_id=payload.composition_snapshot_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        CheckoutResult(
            working_composition_id=result.identities["working_composition_id"],
            base_composition_snapshot_id=result.identities["base_composition_snapshot_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


@router.post(
    "/tracks",
    response_model=SuccessResponse[TrackMutationResult],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_working_composition_track",
)
def create_track(
    project_id: UUID,
    payload: TrackCreateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[TrackMutationResult]:
    try:
        result = service.create_track(
            project_id,
            working_composition_id=payload.working_composition_id,
            name=payload.name,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _track_result(result))


@router.patch(
    "/tracks/reorder",
    response_model=SuccessResponse[ReorderTracksResult],
    operation_id="reorder_working_composition_tracks",
)
def reorder_tracks(
    project_id: UUID,
    payload: TrackReorderRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[ReorderTracksResult]:
    try:
        result = service.reorder_tracks(
            project_id,
            working_composition_id=payload.working_composition_id,
            ordered_track_ids=payload.ordered_track_ids,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        ReorderTracksResult(
            working_composition_id=payload.working_composition_id,
            completed_revision=result.completed_revision,
        ),
    )


@router.patch(
    "/tracks/{track_id}",
    response_model=SuccessResponse[TrackMutationResult],
    operation_id="rename_working_composition_track",
)
def rename_track(
    project_id: UUID,
    track_id: UUID,
    payload: TrackRenameRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[TrackMutationResult]:
    try:
        result = service.rename_track(
            project_id,
            working_composition_id=payload.working_composition_id,
            track_id=track_id,
            name=payload.name,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        TrackMutationResult(
            track_id=track_id,
            completed_revision=result.completed_revision,
            replayed=False,
        ),
    )


@router.delete(
    "/tracks/{track_id}",
    response_model=SuccessResponse[TrackMutationResult],
    operation_id="delete_working_composition_track",
)
def delete_track(
    project_id: UUID,
    track_id: UUID,
    payload: WorkingMutationRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[TrackMutationResult]:
    try:
        result = service.delete_track(
            project_id,
            working_composition_id=payload.working_composition_id,
            track_id=track_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _track_result(result))


@router.post(
    "/tracks/{track_id}/restore",
    response_model=SuccessResponse[TrackMutationResult],
    operation_id="restore_working_composition_track",
)
def restore_track(
    project_id: UUID,
    track_id: UUID,
    payload: TrackRestoreRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[TrackMutationResult]:
    try:
        result = service.restore_track(
            project_id,
            working_composition_id=payload.working_composition_id,
            track_id=track_id,
            target_track_order=payload.target_track_order,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _track_result(result))


@router.post(
    "/clips",
    response_model=SuccessResponse[ClipMutationResult],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_working_composition_clip",
)
def create_clip(
    project_id: UUID,
    payload: ClipCreateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.create_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            track_id=payload.track_id,
            source_asset_version_id=payload.source_asset_version_id,
            timeline_start=payload.timeline_start,
            source_in=payload.source_in,
            source_out=payload.source_out,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.post(
    "/clips/{clip_id}/copy",
    response_model=SuccessResponse[ClipMutationResult],
    status_code=status.HTTP_201_CREATED,
    operation_id="copy_working_composition_clip",
)
def copy_clip(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipCopyRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.copy_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            target_track_id=payload.target_track_id,
            target_timeline_start=payload.target_timeline_start,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.patch(
    "/clips/{clip_id}/move",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="move_working_composition_clip",
)
def move_clip(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipMoveRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[ClipMutationResult]:
    return _absolute_clip_response(
        request,
        clip_id,
        lambda: service.move_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            timeline_start=payload.timeline_start,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
        ),
    )


@router.patch(
    "/clips/{clip_id}/gain",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="update_working_composition_clip_gain",
)
def update_clip_gain(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipGainUpdateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.set_clip_gain(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            gain_db=payload.gain_db,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.patch(
    "/clips/{clip_id}/fade",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="update_working_composition_clip_fade",
)
def update_clip_fade(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipFadeUpdateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.set_clip_fade(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            fade_in=payload.fade_in,
            fade_out=payload.fade_out,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.patch(
    "/clips/{clip_id}/loop",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="update_working_composition_clip_loop",
)
def update_clip_loop(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipLoopUpdateRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.set_clip_loop(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            loop_enabled=payload.loop_enabled,
            timeline_duration=payload.timeline_duration,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.post(
    "/clips/{clip_id}/loop/restore",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="restore_working_composition_clip_loop_state",
)
def restore_clip_loop_state(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipLoopRestoreRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.restore_clip_loop_state(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            loop_enabled=payload.loop_enabled,
            timeline_duration=payload.timeline_duration,
            loop_phase=payload.loop_phase,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.patch(
    "/clips/{clip_id}/trim-start",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="trim_working_composition_clip_start",
)
def trim_clip_start(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipTrimStartRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[ClipMutationResult]:
    return _absolute_clip_response(
        request,
        clip_id,
        lambda: service.trim_clip_start(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            timeline_start=payload.timeline_start,
            source_in=payload.source_in,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
        ),
    )


@router.patch(
    "/clips/{clip_id}/trim-end",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="trim_working_composition_clip_end",
)
def trim_clip_end(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipTrimEndRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
) -> SuccessResponse[ClipMutationResult]:
    return _absolute_clip_response(
        request,
        clip_id,
        lambda: service.trim_clip_end(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            source_out=payload.source_out,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
        ),
    )


@router.post(
    "/clips/{clip_id}/split",
    response_model=SuccessResponse[SplitClipResult],
    operation_id="split_working_composition_clip",
)
def split_clip(
    project_id: UUID,
    clip_id: UUID,
    payload: ClipSplitRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[SplitClipResult]:
    try:
        result = service.split_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            split_at=payload.split_at,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        SplitClipResult(
            original_clip_id=result.identities["original_clip_id"],
            left_clip_id=result.identities["left_clip_id"],
            right_clip_id=result.identities["right_clip_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


@router.delete(
    "/clips/{clip_id}",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="delete_working_composition_clip",
)
def delete_clip(
    project_id: UUID,
    clip_id: UUID,
    payload: WorkingMutationRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.delete_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.post(
    "/clips/{clip_id}/restore",
    response_model=SuccessResponse[ClipMutationResult],
    operation_id="restore_working_composition_clip",
)
def restore_clip(
    project_id: UUID,
    clip_id: UUID,
    payload: WorkingMutationRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = service.restore_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            clip_id=clip_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(request, _clip_result(result))


@router.post(
    "/clips/{original_clip_id}/unsplit",
    response_model=SuccessResponse[SplitClipResult],
    operation_id="unsplit_working_composition_clip",
)
def unsplit_clip(
    project_id: UUID,
    original_clip_id: UUID,
    payload: ClipToggleRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[SplitClipResult]:
    return _split_toggle_response(
        request,
        lambda: service.unsplit_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            original_clip_id=original_clip_id,
            left_clip_id=payload.left_clip_id,
            right_clip_id=payload.right_clip_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        ),
    )


@router.post(
    "/clips/{original_clip_id}/resplit",
    response_model=SuccessResponse[SplitClipResult],
    operation_id="resplit_working_composition_clip",
)
def resplit_clip(
    project_id: UUID,
    original_clip_id: UUID,
    payload: ClipToggleRequest,
    request: Request,
    service: WorkingCompositionServiceDependency,
    effective_owner_id: EffectiveOwnerDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> SuccessResponse[SplitClipResult]:
    return _split_toggle_response(
        request,
        lambda: service.resplit_clip(
            project_id,
            working_composition_id=payload.working_composition_id,
            original_clip_id=original_clip_id,
            left_clip_id=payload.left_clip_id,
            right_clip_id=payload.right_clip_id,
            expected_revision=payload.expected_revision,
            effective_owner_id=effective_owner_id,
            idempotency_key=idempotency_key,
        ),
    )


def _success(request: Request, data):
    return SuccessResponse(data=data, request_id=get_request_id(request))


def _track_result(result: WorkingMutationResult) -> TrackMutationResult:
    return TrackMutationResult(
        track_id=result.identities["track_id"],
        completed_revision=result.completed_revision,
        replayed=result.replayed,
    )


def _clip_result(result: WorkingMutationResult) -> ClipMutationResult:
    return ClipMutationResult(
        clip_id=result.identities["clip_id"],
        completed_revision=result.completed_revision,
        replayed=result.replayed,
    )


def _history_result(result: WorkingMutationResult) -> HistoryMutationResult:
    target_type = result.identities.get("target_type", "CLIP")
    target_id = result.identities.get("target_id", result.identities.get("clip_id"))
    return HistoryMutationResult(
        target_type=target_type,
        target_id=target_id,
        clip_id=result.identities.get("clip_id"),
        completed_revision=result.completed_revision,
        replayed=result.replayed,
    )


def _absolute_clip_response(
    request: Request,
    clip_id: UUID,
    operation,
) -> SuccessResponse[ClipMutationResult]:
    try:
        result = operation()
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        ClipMutationResult(
            clip_id=clip_id,
            completed_revision=result.completed_revision,
            replayed=False,
        ),
    )


def _split_toggle_response(request: Request, operation) -> SuccessResponse[SplitClipResult]:
    try:
        result = operation()
    except Exception as exc:
        raise map_working_composition_error(exc) from exc
    return _success(
        request,
        SplitClipResult(
            original_clip_id=result.identities["original_clip_id"],
            left_clip_id=result.identities["left_clip_id"],
            right_clip_id=result.identities["right_clip_id"],
            completed_revision=result.completed_revision,
            replayed=result.replayed,
        ),
    )


def _aggregate_detail(
    aggregate: WorkingCompositionAggregate,
) -> WorkingCompositionDetail:
    working = aggregate.working_composition
    return WorkingCompositionDetail(
        working_composition_id=working.working_composition_id,
        project_id=working.project_id,
        base_composition_snapshot_id=working.base_composition_snapshot_id,
        revision=working.revision,
        mix_settings=dict(working.mix_settings),
        master_gain_db=working.master_gain_db,
        tracks=[
            TrackDetail(
                track_id=track.track_id,
                track_type=track.track_type,
                name=track.name,
                track_order=track.track_order,
                gain_db=track.gain_db,
                pan=track.pan,
                muted=track.muted,
                solo=track.solo,
            )
            for track in aggregate.tracks
        ],
        clips=[
            ClipDetail(
                clip_id=clip.clip_id,
                track_id=clip.track_id,
                source_asset_version_id=clip.source_asset_version_id,
                timeline_start=_seconds(clip.timeline_start),
                source_in=_seconds(clip.source_in),
                source_out=_seconds(clip.source_out),
                source_duration=_seconds(clip.source_duration),
                timeline_duration=_seconds(clip.timeline_duration),
                loop_enabled=clip.loop_enabled,
                loop_phase=_seconds(clip.loop_phase),
                gain_db=clip.gain_db,
                fade_in=_seconds(clip.fade_in),
                fade_out=_seconds(clip.fade_out),
                split_from_clip_id=clip.split_from_clip_id,
            )
            for clip in aggregate.clips
        ],
        timeline_duration=_seconds(aggregate.timeline_duration_us),
    )


def _seconds(microseconds: int) -> Decimal:
    return Decimal(microseconds) / Decimal(1_000_000)
