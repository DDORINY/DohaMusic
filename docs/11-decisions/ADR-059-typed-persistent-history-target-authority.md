# ADR-059 - Typed Persistent History Target Authority

> 상태: [승인]
> 작성일: 2026-09-06
> 최종 수정일: 2026-09-06
> 관련 기능: WorkingComposition persistent history, Mixer Undo/Redo
> 관련 ADR: [ADR-056](ADR-056-persistent-working-composition-history.md), [ADR-057](ADR-057-working-composition-multi-user-conflict-recovery-authority.md), [ADR-058](ADR-058-mixer-and-export-rendering-authority.md)

## 배경

기존 history entry는 non-null `clip_id`를 target으로 사용하고 Undo/Redo도 모든 command를 Clip으로 조회한다. 이 구조는 Track Mixer와 WorkingComposition Master Gain을 표현할 수 없다. Track 또는 WorkingComposition UUID를 `clip_id`에 넣는 hidden overload는 금지한다.

## 결정

Persistent History target의 canonical authority는 non-null `target_type`과 `target_id`다. 별도 target table은 만들지 않는다.

- `CLIP`: 같은 WorkingComposition의 Clip
- `TRACK`: 같은 WorkingComposition의 Track
- `WORKING_COMPOSITION`: journal을 소유한 WorkingComposition 자체

기존 `clip_id`는 제거하지 않고 nullable compatibility projection으로 유지한다. 기존 row는 `target_type='CLIP'`, `target_id=clip_id`로 backfill한다. 신규 Clip command는 transition 기간에 `clip_id=target_id`를 mirror하고 두 값의 불일치를 거부한다. TRACK과 WORKING_COMPOSITION command의 `clip_id`는 NULL이다.

## Command matrix

```text
CLIP_GAIN -> CLIP
CLIP_FADE -> CLIP
CLIP_LOOP -> CLIP
TRACK_MIXER -> TRACK
MASTER_GAIN -> WORKING_COMPOSITION
```

지원하지 않는 command/target 조합, unknown target type과 cross-composition target은 side effect 없이 fail-closed한다. `before_state`와 `after_state` JSON은 compatibility를 위해 유지하되 command별 serializer/validator가 exact typed shape를 강제한다. arbitrary state JSON은 허용하지 않는다.

## Undo/Redo dispatcher

Undo/Redo는 cursor가 선택한 entry target을 Backend에서 결정하고 Client는 target을 제출하지 않는다.

- CLIP은 기존 Gain/Fade/Loop state와 구조 검증을 그대로 복원한다.
- TRACK_MIXER는 `gain_db`, `pan`, `muted`, `solo` 전체 absolute state를 복원한다.
- MASTER_GAIN은 `master_gain_db`만 복원한다.

Clip, Track, Master command는 하나의 journal/cursor에서 strict LIFO로 공존한다. forward mutation 뒤 redo suffix invalidation, Commit/Checkout barrier, aggregate revision CAS와 response-loss idempotency는 기존 계약을 유지한다.

## Completion compatibility

Undo/Redo completion의 canonical target은 discriminated `target_type`과 `target_id`다. CLIP target만 기존 consumer를 위한 optional `clip_id` mirror를 제공한다. resource representation은 `composition_clip`, `composition_track`, `working_composition`을 사용한다.

과거 persisted Clip completion은 rewrite하지 않고 기존 deserializer가 계속 읽는다. 신규 serializer는 additive target representation을 사용하며 same-key replay는 최초 completion을 그대로 반환한다. History GET cursor projection에는 target payload를 추가하지 않는다.

## Migration

History entry에 `target_type`, `target_id`를 추가하고 `clip_id`를 nullable로 전환한다. 모든 기존 row를 deterministic CLIP target으로 backfill한 뒤 target columns를 non-null로 강제한다. SQLite-portable upgrade/downgrade/reupgrade를 검증한다. `clip_id` 제거는 모든 consumer 전환 후 별도 migration 대상이다.

## 영향

Mixer mutation은 transaction 안에서 canonical before/after capture, typed target append, revision CAS, Preview stale와 completion을 함께 수행한다. Frontend Undo/Redo는 기존 intent-only API를 유지하며 local inverse 또는 target dispatch를 소유하지 않는다.
