# WorkingComposition Product API

> 문서 상태: [완료]
> 최종 수정일: 2026-09-03
> 관련 기능: AI-native DAW D3 Clip Editing Backend API와 Frontend consumer
> 관련 문서: [Service Architecture](../03-architecture/working-composition-service.md), [ADR-050](../11-decisions/ADR-050-working-composition-inverse-mutation-authority.md), [ADR-054](../11-decisions/ADR-054-clip-fade-authority.md), [Workspace REST API](workspace-rest-api-contract.md), [Composition Read](composition-read-workspace.md)

## Endpoint

기본 namespace는 `/api/v1/projects/{project_id}/working-composition`이다.

| Method | suffix | 성공 | Idempotency-Key | 목적 |
|---|---|---:|---|---|
| `GET` | `` | 200 | 없음 | ordered active working aggregate 조회 |
| `POST` | `/initialize` | 201 | 필수 | 빈 WorkingComposition 최초 생성 |
| `POST` | `/checkout` | 200 | 필수 | immutable Snapshot arrangement 복원 |
| `POST` | `/commit` | 201 | 필수 | canonical working arrangement를 새 immutable Snapshot으로 확정 |
| `POST` | `/preview` | 202 | 필수 | expected revision의 immutable Preview Job/manifest 생성 |
| `POST` | `/tracks` | 201 | 필수 | audio Track 생성 |
| `PATCH` | `/tracks/reorder` | 200 | 없음 | 전체 active Track 절대 순서 적용 |
| `PATCH` | `/tracks/{track_id}` | 200 | 없음 | Track 이름 절대값 적용 |
| `DELETE` | `/tracks/{track_id}` | 200 | 필수 | 빈 Track tombstone |
| `POST` | `/tracks/{track_id}/restore` | 200 | 필수 | canonical Track을 지정 order로 복원 |
| `POST` | `/clips` | 201 | 필수 | exact AssetVersion Clip 생성 |
| `POST` | `/clips/{clip_id}/copy` | 201 | 필수 | source geometry를 explicit Track·timeline 목적지에 새 canonical ID로 복제 |
| `PATCH` | `/clips/{clip_id}/gain` | 200 | 필수 | Clip static `gain_db` 절대값 적용 |
| `PATCH` | `/clips/{clip_id}/fade` | 200 | 필수 | Clip-relative `fade_in`·`fade_out` 절대값 적용 |
| `PATCH` | `/clips/{clip_id}/loop` | 200 | 필수 | Clip `loop_enabled`·`timeline_duration` 절대 상태 적용 |
| `POST` | `/clips/{clip_id}/loop/restore` | 200 | 필수 | Undo/Redo용 canonical Loop state exact 복원 |
| `PATCH` | `/clips/{clip_id}/move` | 200 | 없음 | Timeline 시작 절대값 적용 |
| `PATCH` | `/clips/{clip_id}/trim-start` | 200 | 없음 | source/timeline 시작 절대값 적용 |
| `PATCH` | `/clips/{clip_id}/trim-end` | 200 | 없음 | source 끝 절대값 적용 |
| `POST` | `/clips/{clip_id}/split` | 200 | 필수 | original tombstone과 두 child 생성 |
| `DELETE` | `/clips/{clip_id}` | 200 | 필수 | Clip tombstone |
| `POST` | `/clips/{clip_id}/restore` | 200 | 필수 | current eligibility 검증 후 canonical Clip 복원 |
| `POST` | `/clips/{original_clip_id}/unsplit` | 200 | 필수 | exact split child를 tombstone하고 original 복원 |
| `POST` | `/clips/{original_clip_id}/resplit` | 200 | 필수 | 같은 canonical split child를 복원 |

일반 edit mutation body는 `working_composition_id`와 `expected_revision`을 요구한다. Project당 하나인 working aggregate를 확정하는 `/commit`과 별도 render action인 `/preview` body는 `expected_revision`만 받는다. Clip 시간 입력은 decimal seconds이고, source duration·Artifact ID·owner·path·locator는 받지 않는다. replay 응답의 `completed_revision`과 identity는 stored completion result가 authority다.

Clip Copy body는 추가로 `target_track_id`, `target_timeline_start`만 받는다. `source_asset_version_id`, `source_in`, `source_out`, `source_duration`은 Backend가 active source Clip에서 읽으며 extra field는 strict validation으로 거부한다. same-key replay는 최초 copied `clip_id`와 completed revision을 반환하고 새 Clip을 만들지 않는다.

Clip Gain body는 `working_composition_id`, `expected_revision`, numeric `gain_db`를 받는다. `gain_db`는 finite exact decimal, `0.01 dB` 해상도와 양 끝 포함 `-24.00..+24.00 dB`여야 하며 string, `-Infinity`와 mute sentinel은 허용하지 않는다. 성공은 `clip_id`, 최초 `completed_revision`, `replayed`를 반환하며 Snapshot·Preview·Artifact를 자동 생성하지 않는다.

Clip Fade body는 `working_composition_id`, `expected_revision`, numeric `fade_in`, `fade_out`을 decimal seconds로 받는다. 각 값은 finite·non-negative이고 최대 소수점 6자리이며 합은 canonical `timeline_duration` 이하여야 한다. string, boolean, NaN, Infinity와 묵시적 clamp는 허용하지 않는다. 성공은 `clip_id`, 최초 `completed_revision`, `replayed`를 반환하며 Snapshot·Preview·Artifact를 자동 생성하지 않는다. aggregate Clip detail은 canonical seconds `fade_in`, `fade_out`을 반환한다.

Clip Loop body는 `working_composition_id`, `expected_revision`, boolean `loop_enabled`, numeric `timeline_duration`을 받으며 `loop_phase` 입력은 금지한다. Off에서 Enable하면 phase 0, On 상태의 duration update는 현재 canonical phase 보존이다. Enable은 positive duration을 허용하므로 `D < W`, `D = W`, `D > W`가 모두 유효하다. Disable은 absolute-state request로 caller가 `D = W = source_out - source_in`을 명시해야 하며 불일치는 `422 CLIP_LOOP_GEOMETRY_INVALID`다. Backend는 duration을 무시하거나 canonicalize하지 않고 fingerprint에 두 Loop field를 포함한다. 유효한 disable 결과는 loop false, `D = W`, phase 0이다.

Loop history restore body는 `working_composition_id`, `expected_revision`, `loop_enabled`, `timeline_duration`, `loop_phase`의 전체 canonical state를 받는다. 이는 일반 UI value mutation이 아니라 Frontend-owned Undo/Redo command 전용 authority다. Disabled restore는 `D=W, P=0`, enabled restore는 `D>0, 0<=P<W`만 허용하며 phase를 포함한 fingerprint, revision CAS, typed completion과 same-key replay를 적용한다. Backend command stack과 DB schema는 추가하지 않는다.

Frontend selected Clip inspector는 canonical Fade와 Loop를 표시한다. Loop duration은 enabled일 때만 편집하며 typing은 mutation 0, blur/Enter는 valid mutation 1, Escape는 canonical draft 복원이다. Disable은 exact source window `W`를 제출하고 normal request에는 phase를 넣지 않는다. Undo/Redo만 phase 포함 전용 restore를 사용한다. Backend 422가 최종 authority이며 Frontend는 clamp, phase 계산, Fade/Loop DSP, Preview 자동 생성 또는 Waveform source 변형을 수행하지 않는다.

## Composition Commit

`POST /commit`은 Backend canonical active Track·Clip, exact AssetVersion, frozen source geometry와 WorkingComposition mix settings를 deterministic order로 새 CompositionSnapshot·SnapshotTrack·SnapshotClip에 고정한다. 같은 transaction에서 Project explicit selection과 WorkingComposition base를 새 Snapshot으로 바꾸고 revision을 정확히 1 증가시킨다. 응답은 `working_composition_id`, `composition_snapshot_id`, `completed_revision`, `replayed`다.

active Clip이 0개면 Track 존재 여부와 무관하게 `409 WORKING_COMPOSITION_EMPTY`다. tombstone Clip은 세지 않으며 Snapshot·selection·base·revision·성공 completion을 변경하지 않는다. same key·same fingerprint는 최초 Snapshot ID와 completed revision을 replay하며 새 Snapshot과 aggregate 재계산은 0건이다.

Commit은 render·Preview·Export·Mixer가 아니다. Artifact·AssetVersion을 생성하거나 이전 Master/Mix와 Preview output을 새 Snapshot으로 복사하지 않는다. 따라서 선택된 새 Snapshot에 canonical playback source가 없어도 Commit은 성공하며 Composition consumer는 `NO_CANONICAL_PLAYBACK_SOURCE` 상태를 허용한다. 기존 Preview는 보존하되 revision 증가로 stale이 되고 자동 rerender·player source switch는 하지 않는다.

## Clip media source read

`GET /api/v1/projects/{project_id}/asset-versions/{asset_version_id}/media-source`는 WorkingComposition mutation namespace 밖의 Project-scoped read endpoint다. 요청한 exact AssetVersion만 해석하며 latest/newest/selected fallback과 첫 Artifact 선택을 금지한다.

성공 응답은 `asset_version_id`, `artifact_id`, `media_type`, `size_bytes`, `artifact_checksum`, trusted `duration_seconds`, same-origin `/api/v1/artifacts/{artifact_id}/content`만 포함한다. effective Owner·Workspace·active ProjectAsset·active audio Asset과 현재 eligible한 exactly-one audio Artifact를 검증한다. storage path/root/key, locator, credential, signed URL과 payload byte는 공개하거나 읽지 않는다. 이 GET은 WorkingComposition 존재를 요구하지 않고 revision·idempotency result·DB row를 만들거나 변경하지 않는다.

Artifact role/media allowlist는 Clip create와 같은 `audio|stem`, `audio/wav|audio/flac|audio/mpeg`다. 모든 형식은 persisted positive trusted duration을 요구한다. 현재 trusted ingestion이 MP3 duration을 probe하지 않는 경로에서는 `audio/mpeg`가 이 endpoint를 통해 우회 성공하지 않고 `SOURCE_DURATION_UNAVAILABLE`로 닫힌다.

Frontend는 Clip의 exact `source_asset_version_id`마다 이 endpoint를 공식 API client로 호출하고, 응답의 `content_url`만 기존 `/backend` same-origin proxy로 변환해 D2 Waveform loader에 전달한다. editor-session bounded memory cache가 같은 AssetVersion의 resolve·fetch·decode를 공유하며 Clip은 frozen `source_duration` timebase의 `[source_in, source_out)` projection만 표시한다. resolver·fetch·decode·128 MiB 제한 실패는 Waveform unavailable로 격리하고 Clip mutation은 계속 허용한다. current Snapshot, latest Artifact·AssetVersion, path·locator fallback은 사용하지 않는다.

## Initialize duplicate

- same key + same fingerprint: 최초 `working_composition_id`, `completed_revision`, `replayed=true`
- 다른/new key + existing WorkingComposition: `409 WORKING_COMPOSITION_ALREADY_EXISTS`
- duplicate conflict는 기존 WorkingComposition·Track·Clip·revision을 변경하지 않고 성공 completion result를 만들지 않는다.

## Frontend history boundary

initialize 성공은 빈 Frontend Undo/Redo history의 시작점이다. checkout 성공은 working arrangement 전체를 교체하는 history barrier이며 checkout 자체는 inverse operation이 아니다. Commit 성공과 same-key replay 성공도 새 base의 history barrier로 Undo·Redo stack을 비우며 Commit 자체는 Undo/Redo command가 아니다. 실패한 Commit은 history를 비우지 않는다. 단, revision·split structure conflict는 Commit barrier가 아니라 기존 conflict GET/reconcile authority에 따라 history를 초기화한다. 과거 committed version 이동은 explicit checkout을 사용한다. Backend API는 command stack을 저장하지 않는다.

Frontend editing consumer는 기존 21개 operation을 중앙 API client로 호출한다. Backend의 22번째 Fade operation은 다음 Frontend gate 전까지 production UI에서 호출하지 않는다. Clip Gain은 selected Clip의 canonical `gain_db`를 표시하고 slider drag 중 숫자만 preview한 뒤 pointer/key commit에서 numeric absolute value를 한 번 전송한다. Gain memory command는 same Clip ID와 absolute before/after 값을 저장하며 Undo/Redo마다 새 key, response-loss retry에는 같은 key를 사용한다. Copy는 명시적으로 보이는 target Track·Timeline 입력 또는 사용자가 누른 Playhead 적용 action 뒤에만 활성화되며 OS clipboard shortcut이나 숨은 placement fallback을 사용하지 않는다. Backend 응답의 `completed_revision`을 local increment 없이 반영하고 GET으로 canonical Track·Clip 및 Composition selection을 reconcile한다. create/delete/copy는 same-ID restore, split은 stored original·left·right identity의 unsplit/resplit을 사용한다.

## Working Preview

`POST /preview` body는 `expected_revision`만 받는다. 성공은 `202`와 `job_id`, `preview_render_id`, `working_composition_id`, `rendered_revision`, `status=queued`, `replayed`를 반환한다. 같은 key·Project·WorkingComposition·revision은 같은 Job을 replay하고 다른 fingerprint는 `409 IDEMPOTENCY_KEY_REUSED`다. 새 key는 같은 revision에서도 새 explicit Preview action이다.

Job 생성은 현재 source eligibility를 재검증해 >16개도 손실 없이 exact Artifact ID와 integer microseconds geometry를 전용 manifest에 고정한다. worker는 현재 WorkingComposition을 다시 읽지 않는다. 성공 output은 existing Job 조회의 `output_role=working_preview` exactly-one Artifact이며 `/api/v1/artifacts/{artifact_id}/content`로만 재생한다. 응답과 Job settings에는 path·URL·locator를 포함하지 않는다. Preview 전후 WorkingComposition revision·Track·Clip, Snapshot과 Project selection은 변하지 않는다. Frontend는 POST 응답의 Job ID를 adaptive polling하고 terminal에서 중단하며, 같은 logical response-loss retry에 같은 key를 사용한다. 성공 결과는 사용자 재생 action에서만 기존 Global Player로 전달하고 auto-play하지 않는다. `rendered_revision`이 current revision과 다르면 stale로 표시하고 새 explicit action으로 rerender한다. 공식 latest Preview endpoint가 없으므로 refresh 뒤 idle이며 목록 API에서 latest를 추측하지 않는다.

## Error

| HTTP | code | 의미 |
|---:|---|---|
| 404 | `PROJECT_NOT_FOUND` | scope-hidden Project 없음·비활성·다른 Owner |
| 404 | `WORKING_COMPOSITION_NOT_FOUND` | 명시 ID/Project의 active working 없음 |
| 404 | `TRACK_NOT_FOUND`, `CLIP_NOT_FOUND` | 대상이 없거나 다른 working scope |
| 404 | `COMPOSITION_SNAPSHOT_NOT_FOUND` | Snapshot 없음 또는 다른 Project |
| 409 | `WORKING_COMPOSITION_ALREADY_EXISTS` | 다른 key의 duplicate initialize |
| 409 | `WORKING_COMPOSITION_REVISION_CONFLICT` | expected revision CAS 또는 Preview revision pin 실패 |
| 409 | `WORKING_COMPOSITION_EMPTY` | active Clip 0개인 Commit 거부 |
| 409 | `WORKING_PREVIEW_MANIFEST_CONFLICT`, `WORKING_PREVIEW_JOB_STATE_CONFLICT` | Preview binding/manifest 또는 claim-owned completion 상태 충돌 |
| 409 | `TRACK_NOT_EMPTY`, `TRACK_ALREADY_ACTIVE`, `TRACK_RESTORE_ORDER_INVALID`, `CLIP_ALREADY_ACTIVE`, `CLIP_OVERLAP` | Product invariant 충돌 |
| 409 | `SPLIT_STRUCTURE_CONFLICT` | split lineage·source·exact geometry 또는 상태가 최초 split 구조와 다름 |
| 409 | `IDEMPOTENCY_KEY_REUSED`, `IDEMPOTENCY_IN_PROGRESS` | key fingerprint 충돌 또는 처리 중 |
| 409 | `SOURCE_ASSET_UNAVAILABLE`, `SOURCE_ARTIFACT_AMBIGUOUS`, `SOURCE_DURATION_UNAVAILABLE` | source eligibility fail-closed |
| 409 | `SNAPSHOT_ARRANGEMENT_NOT_AVAILABLE` | immutable arrangement 없음 |
| 422 | `INVALID_CLIP_RANGE`, `CLIP_GAIN_OUT_OF_RANGE`, `CLIP_FADE_OUT_OF_RANGE`, `INVALID_INPUT` | 정규화 이후 Clip geometry·Gain·Fade 입력 범위 오류 |
| 422 | `WORKING_PREVIEW_EMPTY`, `WORKING_PREVIEW_LIMIT_EXCEEDED`, `WORKING_PREVIEW_SOURCE_UNAVAILABLE`, `WORKING_PREVIEW_OUTPUT_INVALID` | Preview 입력·source·output fail-closed |

오류는 path, storage root, locator, signed URL, credential, DB path, raw exception과 SQL constraint 이름을 반환하지 않는다.

## Surface 실측

WorkingComposition Router는 APIRoute 24개, OpenAPI Path 23개, Operation 24개이며 operation ID 중복은 0개다. 전체 application은 Route 107개, APIRoute 103개, OpenAPI Path 82개, Operation 103개이며 global operation ID 중복은 0개다. Legacy Pipeline의 GET/HEAD route는 공개 path와 method를 유지하면서 method별 명시 operation ID를 사용한다.

이 22개는 Product API이므로 Workspace Resource Endpoint 30/64 분모에는 포함하지 않는다.


## Multi-user conflict recovery

WorkingComposition mutation, Undo/Redo, Commit, Checkout과 Preview create는 aggregate `expected_revision`을 authority로 사용한다. stale request는 기존 `WORKING_COMPOSITION_REVISION_CONFLICT`로 fail-closed하며 canonical mutation, revision 증가, history cursor 이동과 completion 생성은 0건이다. Frontend는 별도 recovery endpoint 없이 WorkingComposition GET과 history GET을 순서대로 다시 읽는다. stale intent 자동 retry/merge는 하지 않으며 response-loss의 same-key completion replay와 구분한다. 자세한 결정은 [ADR-057](../11-decisions/ADR-057-working-composition-multi-user-conflict-recovery-authority.md)을 따른다.
