# ADR-057 WorkingComposition Multi-user Conflict Recovery Authority

> 상태: 승인
> 작성일: 2026-09-06
> 최종 수정일: 2026-09-06
> 관련 기능: D3 Multi-user Concurrent Editing Recovery / Foundation
> 관련 문서: [ADR-047](ADR-047-revision-safe-idempotency-completion-result.md), [ADR-056](ADR-056-persistent-working-composition-history.md), [WorkingComposition API](../06-api/working-composition-api.md), [Frontend Architecture](../03-architecture/frontend-architecture.md)

## 배경

둘 이상의 client가 같은 WorkingComposition revision을 캐시한 채 편집하면 먼저 성공한 mutation만 aggregate revision을 전진시킬 수 있다. 뒤늦은 stale intent를 자동 replay하거나 field 단위로 merge하면 사용자가 확인하지 않은 상태를 만들고 persistent history cursor를 손상할 수 있다.

## 결정

WorkingComposition과 persistent history의 canonical owner는 Backend다. 모든 forward mutation, Undo, Redo, Commit, Checkout과 revision-pinned Preview는 aggregate `expected_revision`을 사용한다. 같은 Clip의 같은 field, 다른 field, 서로 다른 Clip을 편집한 경우도 aggregate revision이 다르면 `WORKING_COMPOSITION_REVISION_CONFLICT`로 fail-closed한다.

Frontend recovery는 다음 순서의 canonical projection refresh다.

1. 실패한 request를 종료하고 pending local mutation을 해제한다.
2. WorkingComposition GET을 수행한다.
3. 해당 canonical WorkingComposition ID의 history GET을 수행한다.
4. 최신 generation의 두 응답만 query state에 반영한다.
5. 선택 Clip ID가 canonical aggregate에 남아 있으면 선택을 유지하고, 삭제됐으면 선택을 해제한다.
6. numeric draft는 canonical control key로 reset하고 사용자가 최신 state 위에서 명시적으로 다시 편집한다.

stale mutation의 자동 retry, silent last-write-wins, hidden merge, per-field merge, operation transformation, command rebase는 사용하지 않는다. response-loss retry만 동일 Idempotency-Key와 동일 fingerprint로 기존 completion을 replay한다. conflict 뒤 사용자 재시도는 최신 revision과 새 logical operation/key를 사용한다.

## History와 구조 변경

Undo/Redo도 forward mutation과 같은 aggregate revision CAS를 사용한다. concurrent Undo/Redo, forward-vs-Undo/Redo에서 정확히 하나만 성공하며 loser는 cursor, canonical Clip, history entry와 completion을 변경하지 않는다. Split/Delete 뒤 old Clip identity를 향한 stale edit, stale Restore, Commit과 Checkout도 같은 정책으로 거부한다.

## Preview

Preview create는 제출된 revision이 현재 aggregate와 일치할 때만 immutable manifest를 freeze한다. conflict recovery는 workspace/history를 다시 읽고 기존 Preview를 current로 추측하지 않는다. 별도 recovery endpoint나 latest Preview 추측 API는 추가하지 않는다.

## 선택 이유

현재 제품 단계에서 fail-closed와 canonical refetch는 기존 Backend CAS와 persistent history authority를 그대로 재사용하면서 데이터 손실과 숨은 merge를 막는 가장 작은 안전 계약이다.

## 제외 범위

presence, collaborative cursor/selection broadcast, WebSocket, CRDT, OT, automatic intent replay, per-Clip revision은 별도 architecture decision 없이는 도입하지 않는다.

## 데이터와 호환성

새 table, column, migration, error code, recovery endpoint는 없다. Alembic head는 `20260905_0028`을 유지한다. 실제 사용자 DB와 media에는 접근하지 않는다.