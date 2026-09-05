# AI-native DAW Product Track Definition of Done

> 문서 상태: [진행 중]
> 최종 수정일: 2026-09-03
> 관련 문서: [Master Roadmap](../../MASTER_ROADMAP.md), [제품 방향](../02-product/ai-native-daw-product-direction.md), [D1 Composition Read 계약](../06-api/composition-read-workspace.md), [Clip Domain ADR](../11-decisions/ADR-040-canonical-track-clip-working-composition-authority.md), [Frontend 전환 계획](../../planning/ai-native-daw-frontend-migration.md)

이 Track은 Phase 8 Responsive Studio MVP 완료와 분리한다. D0 문서 정합성이 끝나도 DAW Runtime이 구현된 것으로 보지 않는다. 각 단계의 구현·검증·문서·ADR·Git 증거가 모두 있어야 다음 상태로 승격한다.

## D0. 제품 목표 문서 정합성 — [완료]

- [x] CURRENT, TARGET, NOT IMPLEMENTED를 분리한 제품 방향 문서
- [x] 현재·목표 Architecture와 Reference·Creation·Editing·QA·Learning 흐름
- [x] Frontend Migration과 DohaLM Frontend Retirement Gate
- [x] Common Contract 재사용·금지·product-only 후보 정합성 표
- [x] 기존 Phase 8 MVP 완료와 장기 DAW TARGET 분리
- [x] broken link·Markdown·금칙 표현·`git diff --check` 검증
- [x] 한국어 커밋과 원격 작업 브랜치 Push
- [x] `develop` 대상 Draft PR 생성·검토
- [x] PR #94 squash merge와 병합 후 동일 tree 검증

## D1. Composition Read Workspace — [완료]

### 계약 Gate — [완료]

- [x] Workspace v1 Composition read authority와 Legacy migration input 경계
- [x] Project-level explicit selected/current Snapshot과 requested history read 분리
- [x] Snapshot-local Track projection identity와 future canonical Track Domain 분리
- [x] Section `not_available`·빈 목록 정책과 Clip 제외 범위
- [x] Project Composition aggregate endpoint·DTO·Artifact 안전·lineage 계약
- [x] bootstrap/empty/selection-required와 silent fallback·GET mutation 금지
- [x] CURRENT single-user owner scope와 TARGET 인증 Gate 분리
- [x] Common AI Contract schema 변경 없음

### 구현·검증 Gate — [진행 중]

- [x] Workspace Project와 `CompositionWorkspaceRead` aggregate Backend 구현
- [x] explicit selected/current Snapshot persistence와 same-Project 불변식
- [x] exact AssetVersion·safe Artifact·Track projection·Section availability·Mix·lineage 응답
- [x] Snapshot History HMAC Cursor와 aggregate 단건 read 분리
- [x] `WORKSPACE_BOOTSTRAP_REQUIRED`, empty, selection-required, error와 recovery UI
- [x] Legacy silent fallback·GET bootstrap/backfill·write side effect 0건 검증
- [x] `0017 → 0018 → 0017` isolated migration·historical round-trip와 source `0018` schema Gate
- [x] `NO_PREEXISTING_SELECTION_AUTHORITY`, Snapshot 수 무관 selection backfill 0건·latest fallback 0건
- [x] Bootstrap·zero-backfill 3회 멱등, 기존 valid selection 보존, fail-closed·rollback·restart
- [x] transition 이후 `empty`, `selection_required`, PATCH 후 `ready` Aggregate 계약
- [x] D1-Transition PR #104 검토와 `develop` squash merge
- [ ] 실제 인증 principal 기반 Owner·Workspace·Project privacy와 cross-owner leakage 0건
- [x] Frontend Workspace consume와 fixture Snapshot 선택·refetch·재진입 통합
- [ ] 실제 사용자 DB Snapshot E2E와 인증 Gate

## D2. Timeline Playback Foundation — [완료]

- [x] 읽기 전용 초 단위 ruler와 snapshot-local Track lane
- [x] 기존 Global Player 단일 audio authority 재사용
- [x] 단일 Mix·safe audio Artifact source 해석과 unavailable fail-closed
- [x] 실제 media duration·currentTime 기반 Playhead와 play·pause·ended
- [x] click seek·clamp·horizontal scroll·zoom coordinate mapping
- [x] local Track 선택, Space·좌우 keyboard, accessible label·focus·반응형 overflow
- [x] empty·selection-required에서 Timeline 미표시, ready에서만 렌더
### Waveform / Richer Playhead Foundation — [완료]

- [x] canonical safe Artifact를 재사용한 same-origin client decode와 128 MiB 입력 제한
- [x] 최대 2,048개 peak·각 channel의 각 peak bucket에서 sample array element 접근 최대 256회·단일 SVG path bounded rendering
- [x] ruler·Waveform·Playhead가 scroll·zoom을 포함한 하나의 시간 좌표계 사용
- [x] Waveform click seek, Playhead drag preview/commit, 정밀 hover·time feedback
- [x] source 변경·unmount abort, stale 결과 폐기, decode 실패 시 playback 격리
- [x] 단일 Global Player audio authority·keyboard·slider 접근성·Frontend 자동 테스트 유지

- [ ] Section marker·Clip·range selection·multi-track sync engine

## D3. Non-destructive DAW Editing — [진행 중]

일반 Project 관리는 `/projects`, DAW 진입은 `/projects?mode=daw`의 명시적 Project 선택과 `/projects/{project_id}` 편집으로 분리하며 `/studio`는 별도의 새 음악 생성 workflow다. Navigation은 이 역할을 안내할 뿐 Project·Snapshot·WorkingComposition을 추측하거나 자동 선택하지 않는다.

- [x] selected Clip Loop toggle과 canonical `timeline_duration` exact input
- [x] normal Loop mutation의 phase 입력 금지와 disable `D=W` absolute request
- [x] Backend persistent history journal/cursor 기반 phase 포함 Undo/Redo와 Frontend history intent 사용
- [x] Clip width·Fade invariant의 timeline duration authority와 Waveform/DSP 비영향 회귀

- [x] canonical Track·Clip·WorkingComposition authority와 persistence ADR
- [x] mutable working state와 immutable Snapshot commit·schema extension 경계
- [x] split identity·revision concurrency·Undo/Redo ownership·overlap·duration 정책
- [x] Backend split·trim·move·delete와 Track create·rename·reorder·delete
- [x] Frontend explicit Clip Copy와 copied-ID same-ID Undo/Redo
- [x] Clip별 static Gain authority·persistence·revision-safe Product API·Preview DSP·Commit freeze Backend foundation
- [x] Frontend selected Clip Gain slider·exact input·reset과 Backend persistent Undo/Redo
- [x] Clip별 linear Fade authority·persistence·revision-safe Product API·Preview DSP·Commit/Checkout freeze Backend foundation
- [x] Frontend selected Clip Fade exact input·Backend persistent Undo/Redo·Preview stale 통합
- [x] Clip `timeline_duration`·Loop phase authority, persistence·revision-safe Product API·Preview schema 4·deterministic renderer Backend foundation
- [x] Frontend loop 편집 UI와 Backend persistent history integration
- [x] 원본 불변 AssetVersion lineage·canonical Track/Clip CompositionSnapshot commit
- [x] WorkingComposition·Track·Clip·SnapshotTrack·SnapshotClip ORM과 additive Alembic 구현
- [x] exact microseconds·FK·lineage·overlap helper·revision·Repository rollback 회귀 검증
- [x] Track non-empty 삭제 거부와 trusted WAV·FLAC Artifact duration authority 기반
- [x] WorkingComposition Service·Product API·expected revision CAS·idempotency replay 구현
- [x] same-ID Track/Clip restore와 exact split geometry 기반 atomic unsplit/resplit Backend 구현
- [x] Frontend Track/Clip editing·Backend persistent Undo/Redo·revision conflict reconcile 구현
- [x] exact AssetVersion→exactly-one eligible Artifact safe media resolution Backend foundation 구현
- [x] Clip별 `[source_in, source_out)` Waveform decode·render와 Track lane 통합
- [x] revision-pinned Working Preview manifest, Project Preview Asset과 성공별 immutable AssetVersion/Artifact Backend foundation
- [x] WAV·FLAC·trusted MP3 trim·offset·gap·cross-Track overlap FFmpeg renderer와 bounded cleanup
- [x] Working Preview Frontend action·Job polling·stale 표시·Global Player integration
- [x] single-user persistent history journal/cursor·reload recovery
- [x] aggregate revision fail-closed와 canonical WorkingComposition/history refetch 기반 multi-user 동시 편집 recovery

## D4. Mixer와 Export — [계획]

- [ ] Volume·Pan·Mute·Solo와 Mix 설정
- [ ] Preview와 WAV·MP3·FLAC 독립 Export Asset
- [ ] 음질·Job·secure download 검증

## D5. AI Music Director — [계획]

- [ ] TimelineSelection을 기존 MusicIntent로 materialize
- [ ] 실행 전 승인과 Candidate A/B 비교·선택
- [ ] HTTP·SSE·Cancel·Retry·Readiness·오류·실제 Provider E2E

## D6. Reference Panel — [계획]

- [ ] 허용 source와 분석·retention·Training 권리 분리
- [ ] ReferenceAnalysis·FeatureRecord·DohaLM planning context
- [ ] 철회·삭제·원본 비노출 검증

## D7. Composition Evaluation / QA — [계획]

- [ ] `CompositionEvaluationRun` 제품 계약과 ADR 검토
- [ ] 9개 평가 영역과 human-readable QA Report
- [ ] Studio exact range deep-link, RevisionPlan과 Re-Evaluation
- [ ] 공통 EvaluationRun 의미 비충돌 검증

## D8. Continuous Learning Review Hub — [계획]

- [ ] 명시적 opt-in LearningCandidate 제안·검토
- [ ] RightsMetadata·TrainingEligibility·DatasetVersion 상태 연결
- [ ] 자동 Dataset 포함·Training 승인 금지와 불변 lineage 검증

## D9. 통합·운영 전환 — [계획]

- [ ] 기존 MVP parity·migration·rollback
- [ ] 인증·소유권·감사·rate limit·분산 실행·관측성
- [ ] 실제 사용자 DAW·QA·Export E2E와 접근성·성능 평가
- [ ] DohaLM Frontend Retirement Gate와 명시적 승인
