# WorkingComposition Atomic Mutation Service

> 문서 상태: [완료]
> 최종 수정일: 2026-09-03
> 관련 기능: AI-native DAW D3 Clip Editing Backend와 Frontend consumer
> 관련 문서: [ADR-040](../11-decisions/ADR-040-canonical-track-clip-working-composition-authority.md), [ADR-045](../11-decisions/ADR-045-clip-service-deletion-media-duration-authority.md), [ADR-047](../11-decisions/ADR-047-revision-safe-idempotency-completion-result.md), [ADR-050](../11-decisions/ADR-050-working-composition-inverse-mutation-authority.md), [ADR-053](../11-decisions/ADR-053-clip-gain-authority.md), [ADR-054](../11-decisions/ADR-054-clip-fade-authority.md), [Product API](../06-api/working-composition-api.md)

## 책임과 계층

`WorkingCompositionService`가 effective Owner·Workspace·Project scope, transaction, revision, mutation validation과 idempotency completion을 소유한다. Router는 Service만 호출하고 Repository·Session·ORM·filesystem·Resolver·Provider에 접근하지 않는다. Repository는 deterministic read와 persistence primitive만 제공하며 `commit()`·`rollback()`을 호출하지 않는다.

```text
Router → WorkingCompositionService → Repository / trusted metadata Service
                   └─ 하나의 session.begin()
                      claim → validate → mutate → revision CAS → complete
```

GET은 WorkingComposition을 자동 생성하지 않는다. active Track은 `(track_order, track_id)`, active Clip은 Track order 뒤 `(timeline_start, clip_id)` 순서로 반환하고 working duration은 active Clip의 최대 `timeline_end`에서 계산한다.

Project-scoped media source GET도 같은 Service의 trusted source metadata 경계를 재사용하지만 WorkingComposition 존재 여부나 현재 Clip membership에는 의존하지 않는다. exact AssetVersion의 Owner·Workspace·active ProjectAsset·active Asset과 exactly-one eligible audio Artifact를 현재 시점에 fail-closed 검증하고, opaque identity·media facts·trusted duration·same-origin Artifact content URL만 반환한다. 이 경로는 transaction·revision·idempotency completion·payload probe를 만들지 않는다.

## Initialize 계약

initialize는 GET-or-create가 아닌 explicit create다.

| 조건 | 결과 |
|---|---|
| Project에 WorkingComposition 없음 | 새 opaque ID, revision 0, completion result 저장 |
| 같은 key·같은 fingerprint | 최초 ID와 revision 0 replay, mutation 0 |
| 기존 WorkingComposition + 다른/새 key | `409 WORKING_COMPOSITION_ALREADY_EXISTS`, mutation·revision·성공 completion 0 |
| concurrent duplicate loser | DB unique race를 같은 Product conflict로 정규화, partial row 0 |

기존 WorkingComposition이나 현재 revision을 새 initialize 성공 응답으로 반환하지 않는다.

## Revision과 replay

모든 상태 mutation은 `expected_revision`을 받고 마지막에 `UPDATE ... WHERE revision=:expected_revision RETURNING revision` compare-and-swap을 수행한다. affected row가 없으면 transaction 전체가 rollback되고 `WORKING_COMPOSITION_REVISION_CONFLICT`다. 성공한 operation은 정확히 `+1`이다.

checkout·Track create/delete/restore·Clip create/copy/split/delete/restore/unsplit/resplit은 `Idempotency-Key`를 요구한다. fingerprint는 effective Owner, Project, WorkingComposition, operation, expected revision, target ID와 microseconds로 정규화한 body를 포함한다. completed replay는 현재 revision과 domain validation보다 먼저 stored typed result를 반환한다. move·trim·rename·reorder는 ADR-040의 absolute mutation 계약을 유지하며 이전 revision 재시도는 conflict 후 GET/reconcile한다.

## Track·Clip mutation

- Track create는 V1 `audio`와 연속 order를 사용한다.
- reorder는 임시 offset 뒤 최종 `0..N-1`로 배치해 active-order unique 충돌을 피한다.
- active Clip이 있는 Track delete는 `TRACK_NOT_EMPTY`; cascade하지 않는다.
- Track restore는 tombstone의 canonical ID를 유지하고 요청의 `target_track_order`에 삽입한다. active Track은 원자적으로 shift/reindex하며 범위는 `0..active_track_count`다.
- Clip create는 exact AssetVersion, active audio Asset, effective scope, active ProjectAsset와 exactly-one eligible Artifact의 persisted positive `duration_us`를 요구한다.
- public decimal seconds는 half-up integer microseconds로 변환한 뒤 range를 다시 검증한다.
- create·move·trim·split·checkout은 같은 Track의 `[start,end)` overlap을 거부하고 adjacency와 cross-Track overlap을 허용한다.
- split은 original을 tombstone하고 두 새 canonical ID에 `split_from_clip_id=original`을 기록한다.
- Clip copy는 active source row에서 exact AssetVersion과 frozen source geometry를 읽고, 명시한 active target Track·timeline position에 서버 발급 새 canonical ID를 생성한다. source body 입력·implicit placement·latest Version fallback은 없고 copied row의 `split_from_clip_id`는 항상 `null`이다. 현재 source eligibility와 기존 overlap authority를 다시 검증한다.
- Clip Gain은 exact `NUMERIC(5,2)`, 기본 `0.00 dB`, 양 끝 포함 `-24.00..+24.00 dB`의 absolute mutation이다. Copy와 Split child는 source Gain을 상속하고 delete/restore는 값을 보존하며 move/trim은 변경하지 않는다. original·left·right Gain이 다르면 unsplit/resplit은 `SPLIT_STRUCTURE_CONFLICT`로 전체 rollback해 편집을 조용히 잃지 않는다.
- Clip Fade는 decimal seconds를 exact integer microseconds로 정규화하는 absolute `fade_in`·`fade_out` mutation이다. 기본값은 0이고 합은 canonical `timeline_duration` 이하여야 한다. Copy·Move·delete/restore는 값을 보존한다. Split은 active Fade 내부를 거부하고 left에 Fade-in, right에 Fade-out만 투영한다. Trim이 invariant를 깨거나 unsplit/resplit child projection이 다르면 clamp 없이 전체 rollback한다.
- Clip Loop는 `timeline_duration`, `loop_enabled`, `loop_phase`를 canonical geometry로 사용한다. Disable은 caller가 `D = source_out - source_in`을 제출하는 absolute-state request이며 불일치는 `422 CLIP_LOOP_GEOMETRY_INVALID`로 side effect 없이 거부한다. Loop-enabled Split·Trim fragment의 `D < W`를 허용하고 phase는 ADR-055 equation으로만 생성한다.
- delete는 tombstone만 만들고 AssetVersion·Artifact·payload를 변경하지 않는다.
- Clip restore는 같은 canonical ID와 생성 당시 frozen geometry·`source_duration`을 유지하되, 현재 ProjectAsset·AssetVersion·Asset·eligible Artifact와 parent Track 활성 상태 및 overlap을 다시 fail-closed 검증한다.
- unsplit은 정확히 최초 split geometry를 유지하는 두 active child만 tombstone하고 original을 복원한다. resplit은 같은 조건의 두 tombstone child를 같은 canonical ID로 복원한다. 이동·trim·삭제·lineage/source 변경 등 구조 drift가 있으면 `SPLIT_STRUCTURE_CONFLICT`다.

checkout은 같은 Project의 immutable SnapshotTrack/SnapshotClip만 authority로 사용한다. canonical Track/Clip ID, exact AssetVersion과 frozen duration을 한 transaction에서 복원하고 legacy arrangement가 없으면 fail-closed한다. `SnapshotItem`은 사용하지 않는다.

Composition Commit은 active Clip 존재와 expected revision을 검증한 뒤 active Track·Clip만 새 SnapshotTrack·SnapshotClip로 deterministic하게 고정한다. exact AssetVersion과 frozen source/timeline geometry를 유지하고 current Artifact를 다시 해석하지 않는다. Snapshot·selection·working base·revision `+1`·`COMPOSITION_COMMIT` completion은 하나의 Service transaction이며 Repository는 flush만 수행한다. active Clip 0개는 `WORKING_COMPOSITION_EMPTY`로 Snapshot과 모든 side effect를 0건 보장한다. same-key replay는 저장된 Snapshot ID와 completed revision만 반환한다.

Commit은 audio·file·Provider I/O, Preview render, Master/Mix 복사나 새 AssetVersion 생성을 하지 않는다. Working Track·Clip row와 canonical ID도 교체하지 않으며 새 Snapshot에 playback source가 없는 상태를 허용한다.

## 검증과 범위 밖 항목

Clip Copy는 exact geometry·new identity·response-loss replay·split child·Snapshot freeze·concurrent CAS와 eligibility/insert/revision/completion 강제 rollback을 별도 회귀 테스트로 검증한다.

격리 SQLite fixture에서 revision race exactly-one success, initialize unique race, inverse replay fidelity, 동일 canonical ID 복원, atomic Track reindex, current source eligibility, exact split geometry와 Loop phase continuity, Clip Gain·Fade inheritance/divergence, overlap과 forced rollback을 검증했다. 현재 repository single head는 persistent WorkingComposition history journal과 cursor를 추가한 `20260905_0028`이다. 실제 사용자 DB·media·Provider·GPU에는 접근하지 않았다.

initialize는 빈 persistent history에서 시작하고 checkout과 성공한 Composition Commit은 history barrier다. Commit 자체는 Undo command가 아니며 과거 committed state 이동은 checkout을 사용한다. Backend는 immutable command journal과 persisted cursor를 저장하고 revision CAS·idempotency를 적용한 history projection과 Undo/Redo endpoint를 제공한다. Frontend는 GET history와 Backend Undo/Redo completion을 authority로 소비하고 conflict 시 WorkingComposition과 history를 함께 reconcile한다. exact AssetVersion-safe media source를 editor-session bounded decode cache와 Clip별 `[source_in, source_out)` projection으로 연결한 Track/Clip Waveform Frontend까지 완료했다.

명시적 Preview action은 WorkingComposition을 mutation하지 않고 현재 revision의 ordered Track·Clip과 exact Artifact를 전용 durable manifest에 고정한다. Project당 하나의 non-canonical `MIX` Preview Asset을 전용 binding으로 소유하며, 성공 render마다 새 immutable AssetVersion·WAV Artifact와 `working_preview` JobOutput을 claim-owned transaction으로 만든다. 같은 key는 최초 Job을 replay하고 새 action·retry 성공은 기존 결과를 덮어쓰지 않는다. 24시간 뒤 Artifact를 `expired`로 전환해 content를 닫되 AssetVersion·manifest provenance는 보존한다. Frontend는 explicit POST, 공식 Job polling, exact output cardinality, stale/rerender와 기존 Global Player handoff로 이 계약을 소비하며 completion 시 자동 재생하지 않는다. Commit 뒤 기존 Preview는 보존되지만 revision 차이로 stale이다. 상세 authority는 [ADR-052](../11-decisions/ADR-052-working-composition-preview-render-authority.md)를 따른다. Mixer는 후속 범위다.

Preview manifest schema 5는 Clip별 `timeline_duration_us`, `loop_enabled`, `loop_phase_us`, Gain, Fade와 Track별 `gain_db`, `pan`, `muted`, `solo`, Composition의 `master_gain_db`를 immutable하게 pin한다. renderer는 Clip DSP, Track gain/pan/selection, Track sum, master gain 순으로 처리하며 live WorkingComposition Mixer state를 다시 조회하지 않는다. Persisted schema 1~4는 각 버전의 명시적 compatibility contract로 읽고 schema 5 필수 state 누락은 fail closed한다. 이후 Working mutation은 기존 manifest·Artifact를 바꾸지 않으며 Preview·Commit을 자동 생성하지 않는다. Waveform은 source-shape projection을 유지해 Loop·Gain·Fade mutation으로 decode cache나 peak를 다시 만들지 않는다.
