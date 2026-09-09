Warning: truncated output (original token count: 20460)
Total output lines: 715

# 변경 이력

> 문서 목적: 사용자와 개발자에게 의미 있는 저장소 변경을 기록한다.
> 현재 상태: **운영 중**
> 최종 수정일: 2026-09-05

DohaMusic 프로젝트의 주요 변경 사항을 기록한다. 일반 작업은 `[Unreleased]`에 기록하고 프로젝트 버전 정책은 구현 단계에서 결정한다.

## [Unreleased]

### CI - Frontend Playwright Gate

- `develop` 대상 PR과 `develop` push에서 Chromium Desktop, Tablet, Mobile configured-parallel
  Playwright 전체 회귀와 실제 Backend/FFmpeg 기반 Export Browser E2E를 실행하는
  `frontend-playwright` GitHub Actions job을 추가했다.
- Playwright retry와 worker override 없이 기존 설정을 사용하고, full-stack Export test runtime은
  GitHub runner temp 경로를 사용하는 cross-platform 격리 환경으로 정합화했다.

### 추가 - Export Production Worker Runner

- API가 생성한 queued Export Job을 Export-only claim/lease CAS로 발견해 canonical WAV
  `ExportWorkerService`로 전달하는 production runner를 application lifespan에 연결했다.
- 만료된 Export claim의 restart recovery, concurrent runner claim 단일화, cancellation 제외,
  iteration failure isolation 및 graceful shutdown 계약을 ADR-065로 기록했다.

### 수정 — API Surface 계약 완결

- Legacy Pipeline content/download의 GET·HEAD operation ID를 method별로 명시해 OpenAPI operation ID 중복 2종을 제거했다. 공개 path, HTTP method와 request/response semantics는 변경하지 않았다.
- 전체 Runtime API count, method/path·operation ID 유일성, `$ref`와 path parameter 무결성, critical Product API 및 canonical surface fingerprint를 fail-closed regression Gate로 고정했다.

### 추가 — Multi-user Concurrent Editing Recovery

- ADR-057에서 WorkingComposition aggregate revision CAS를 multi-user authority로 확정하고 same/different field·different Clip·Undo/Redo·structural·Commit/Checkout/Preview stale request를 fail-closed한다.
- Frontend conflict recovery를 generation-guarded canonical WorkingComposition/history refetch로 강화했다. stale intent 자동 retry/merge 없이 pending draft를 canonical 값으로 reset하고, 존재하는 selected Clip ID만 유지한다.
- Backend production·API·error taxonomy·persistence·migration은 변경하지 않았고 Alembic head `20260905_0028`을 유지한다.

- Backend가 Clip Gain/Fade/Loop의 canonical 전후 상태를 원자적으로 기록하는 persistent WorkingComposition history journal, cursor, undo/redo API와 migration 0028을 추가했다.
- Frontend Undo/Redo를 Backend history projection과 mutation endpoint로 전환해 reload 후 cursor를 복구하고, Gain/Fade/Loop strict LIFO·redo invalidation·revision conflict reconciliation·Preview stale·structural/Commit/Checkout barrier를 일관되게 적용했다.

### 추가 — Clip Loop Frontend Integration

- selected Clip inspector에 canonical Loop toggle과 `timeline_duration` exact input을 추가하고, normal mutation과 phase 포함 history restore를 분리했다.
- Loop 변경을 Gain/Fade와 같은 memory history, revision reconcile, Preview stale 및 Commit/Checkout history boundary에 통합했다. Waveform와 WebAudio DSP는 변경하지 않았다.

### 문서

- ADR-055에서 Clip source window와 timeline duration을 분리하고 loop phase를 canonical microseconds로 저장하는 Split·Trim·Fade·Preview·Snapshot·migration architecture를 확정했다.

### 수정 — Loop History Phase Restore Authority

- Loop가 켜진 Clip의 duration 변경은 Split·Trim이 만든 canonical nonzero phase를 보존하도록 수정했다. Off→On은 phase 0, On→Off는 `D=W, P=0` 계약을 유지한다.
- Frontend-owned Undo/Redo가 disable 이전의 nonzero phase를 exact 복원할 수 있도록 revision-safe 전용 Loop history restore operation과 phase 포함 idempotency fingerprint를 추가했다. 일반 Loop mutation에는 phase 입력을 노출하지 않았고 DB schema·migration·Frontend production code는 변경하지 않았다.

### 수정 — Project·DAW Navigation 분리

- Desktop·Mobile AppShell에서 일반 `프로젝트` 관리(`/projects`)와 `DAW 편집` 진입(`/projects?mode=daw`)을 별도 navigation entry로 분리하고 query·nested Project에 따른 단일 `aria-current` 규칙을 적용했다.
- DAW entry와 Studio CTA는 Project를 추측하지 않고 명시적 선택 화면을 표시하며, 같은 Project list를 재사용해 일반 mode의 `프로젝트 열기`와 DAW mode의 `DAW 열기`를 exact `/projects/{project_id}`로 연결한다. authoritative `project_id`가 있는 Result direct CTA는 `DAW에서 편집`으로 명확히 했다.
- Backend production·API semantics·migration·실제 사용자 DB/media와 WorkingComposition·Gain·Fade·Preview·Commit authority는 변경하지 않았다. D3는 `[진행 중]`이며 당시 다음 gate는 Loop Authority / Foundation이었다.

### 수정 — Studio·DAW Navigation과 Onboarding

- Next dev를 `127.0.0.1`로 열었을 때 dev asset origin 차단으로 hydration되지 않아 Onboarding `닫기`가 동작하지 않던 문제를 허용된 loopback origin과 명시적 Zustand persist hydration gate로 수정했다. 닫기·Escape·첫 음악 만들기는 완료 상태를 보존하고 dialog focus를 안전하게 복원한다.
- `/studio`를 새 음악 생성 workflow로 명확히 하고 Desktop·Mobile `프로젝트 · DAW`, Studio secondary CTA, Project card의 `DAW에서 편집`, Project 상세의 사용자-facing DAW heading을 연결했다. Pipeline 응답에 authoritative `project_id`가 있는 Result만 exact Project CTA를 표시하며 implicit Project fallback은 추가하지 않았다.
- Backend production·API semantics·migration·실제 사용자 DB/media와 WorkingComposition·Gain·Fade·Preview·Commit authority는 변경하지 않았다. D3는 `[진행 중]`이며 당시 다음 gate는 Loop Authority / Foundation이었다.

### 추가 — Clip Fade Frontend UI

- selected Clip inspector에 `fade_in`·`fade_out` canonical seconds를 최대 소수 6자리 exact numeric input으로 추가했다. 두 값의 합이 Clip duration을 넘으면 묵시적 clamp 없이 local validation을 표시하고 mutation하지 않는다.
- 중앙 WorkingComposition API client와 `completed_revision` authority, same-key response-loss retry를 연결하고 absolute before/after Fade pair를 기존 Gain과 같은 strict LIFO memory Undo/Redo stack에서 처리한다.
- Preview stale·explicit Preview, Commit/Checkout history barrier, Clip·Project race 격리, safe error와 desktop/tablet/mobile 접근성을 Vitest와 Playwright로 검증했다. Frontend WebAudio·Waveform source, Backend production code·migration·실제 사용자 DB/media는 변경하지 않았다.

### 추가 — Clip Loop Authority Backend Foundation

- ADR-055에 따라 `timeline_duration`, `loop_enabled`, `loop_phase`를 Working Clip·Snapshot Clip·Working Preview Clip의 canonical geometry로 구현했다. Loop-enabled fragment는 `timeline_duration < source window`를 허용하며 Split·Trim은 phase continuity equation을 적용한다.
- absolute Loop mutation은 `expected_revision`, `Idempotency-Key`, `loop_enabled`, `timeline_duration`을 사용한다. Disable caller는 `timeline_duration = source_out - source_in`을 제출해야 하며 다른 값은 묵시적 정규화 없이 `422 CLIP_LOOP_GEOMETRY_INVALID`로 거부한다.
- additive Alembic `20260905_0027`, deterministic backfill, Preview manifest schema 4와 schema 1~3 fallback, source window→loop expansion→Gain→full-timeline Fade renderer를 추가했다. 실제 사용자 DB·media와 Frontend production code는 변경하지 않았으며 다음 gate는 Loop Frontend Integration이다.
- SQLite downgrade는 Gain·Fade CHECK를 먼저 제거한 뒤 batch table recreation으로 해당 column을 제거해 SQLite version·platform에 따른 dangling constraint 실패를 방지한다.

### 추가 — Clip Fade Backend Foundation

- ADR-054에서 D3 Clip-relative `fade_in`·`fade_out`을 integer microseconds, 기본 0, 최대 6자리 decimal seconds와 고정 linear-amplitude curve로 확정했다. 두 Fade 합은 Clip duration 이하여야 하며 자동 clamp는 하지 않는다.
- revision-safe `PATCH /api/v1/projects/{project_id}/working-composition/clips/{clip_id}/fade`와 `CLIP_FADE_UPDATE` completion을 추가했다. Copy·Move·delete/restore는 Fade를 보존하고 Split은 active Fade 구간을 거부한 뒤 left의 Fade-in과 right의 Fade-out만 보존하며, canonical projection이 다른 unsplit/resplit은 fail-closed rollback한다.
- CompositionClip·SnapshotClip·WorkingPreviewRenderClip에 Fade를 보존하고 Preview manifest schema 3과 FFmpeg static Gain 뒤 linear Fade DSP를 추가했다. Commit·Checkout은 exact Fade를 freeze/restore하고 기존 Preview manifest는 Working mutation 뒤에도 불변이다.
- additive Alembic `20260903_0026`과 upgrade/downgrade, default·range·precision, replay·CAS, inverse identity, Preview audio envelope·pinning 및 회귀 테스트를 추가했다. 실제 사용자 DB·media·Common Contract·Provider·Dataset·Training·GPU와 Frontend production code는 변경하지 않았다.

### 추가 — Clip Gain Frontend UI

- selected Clip inspector에 `-24.00..+24.00 dB`, `0.01 dB` slider·exact numeric input과 0 dB reset을 추가했다. drag 중에는 숫자만 local preview하고 pointer/key commit에서 중앙 API client로 absolute Gain mutation 한 번을 보낸다.
- `gain_db` canonical 응답, Backend `completed_revision`, same-key response-loss retry와 absolute before/after memory Undo/Redo를 연결했다. revision conflict는 GET reconcile과 history clear를 사용하고 backend range·split structure 오류는 안전한 메시지로 표시한다.
- Copy·Split·delete/restore·Commit·checkout Gain identity, Preview stale, Waveform fetch/decode/cache 불변, pending Clip selection·Project session 격리와 desktop/tablet/mobile 접근성을 Vitest와 Playwright로 검증했다. Backend production·migration·실제 사용자 DB/media·audio DSP·새 ADR은 변경하지 않았다.

### 추가 — Clip Gain Backend Foundation

- ADR-053에서 D3 Clip별 static `gain_db`를 기본 `0.00 dB`, `0.01 dB` 해상도, inclusive `-24.00..+24.00 dB`로 확정했다. mute sentinel은 사용하지 않고 Track/Master Gain·Pan·Mute·Solo는 D4로 분리했다.
- revision-safe `PATCH /api/v1/projects/{project_id}/working-composition/clips/{clip_id}/gain`과 `CLIP_GAIN_UPDATE` completion을 추가했다. Copy·Split은 Gain을 상속하고 delete/restore·move/trim은 보존하며 split lineage Gain divergence의 unsplit/resplit은 fail-closed rollback한다.
- CompositionClip·SnapshotClip·WorkingPreviewRenderClip에 Gain을 보존하고 Preview manifest schema 2와 FFmpeg static dB DSP를 추가했다. Commit은 Gain을 freeze하고 Preview는 source trim 뒤 Gain, timeline placement, Track mix 순으로 적용하며 Waveform cache는 변경하지 않는다.
- additive Alembic `20260830_0025`와 upgrade/downgrade, range, replay, CAS, identity, Preview amplitude·pinning 및 회귀 테스트를 추가했다. 실제 사용자 DB·media·Common Contract·Provider·Dataset·Training·GPU와 Frontend production code는 변경하지 않았다.

### 추가 — Clip Copy

- `POST /api/v1/projects/{project_id}/working-composition/clips/{clip_id}/copy`를 추가했다. active source Clip의 exact AssetVersion과 frozen source range/duration을 유지하고 명시적 target Track·timeline position에 서버 발급 새 canonical Clip ID를 생성한다. source row와 split lineage는 변경하지 않으며 copied `split_from_clip_id`는 `null`이다.
- current Asset·AssetVersion·ProjectAsset·exactly-one Artifact eligibility, same-WorkingComposition active Track, same-Track overlap·adjacency와 expected revision CAS를 fail-closed 재검증한다. `CLIP_COPY` completion은 최초 copied ID와 completed revision을 replay하며 key conflict·response loss·concurrent race·네 transaction stage rollback을 검증한다.
- Frontend editor에 visible target Track·Timeline destination과 explicit Playhead 적용 action을 추가했다. Copy 성공은 새 ID를 선택하고 memory history에 push하며 Undo는 copied ID delete, Redo는 같은 ID restore를 사용한다. OS clipboard shortcut, implicit placement, auto Preview와 auto Commit은 추가하지 않았다.
- split child Copy와 copied Clip Snapshot commit identity, exact geometry, Waveform exact AssetVersion cache 재사용을 회귀 검증했다. 기존 schema를 재사용해 migration은 0개이고 Alembic head는 `20260828_0024`이며 실제 사용자 DB·media에는 접근하지 않았다.

### 추가 — Composition Commit

- `POST /api/v1/projects/{project_id}/working-composition/commit`을 추가해 Backend canonical active Track·Clip, exact AssetVersion, frozen geometry와 mix settings를 새 immutable CompositionSnapshot·SnapshotTrack·SnapshotClip에 고정한다. Project selection·WorkingComposition base·revision `+1`·`COMPOSITION_COMMIT` completion은 한 Service transaction이다.
- active Clip 0개는 `WORKING_COMPOSITION_EMPTY`로 거부하고 Snapshot·selection·base·revision·성공 completion을 0건 보장한다. same-key response-loss retry는 최초 Snapshot ID와 completed revision을 replay하며 새 Snapshot을 만들지 않는다.
- Commit은 render가 아니므로 Preview·Master/Mix Artifact와 AssetVersion을 복사하거나 생성하지 않는다. 새 Snapshot의 canonical playback source가 없어도 성공하고 기존 Working Preview는 revision 차이로 stale이 되며 Global Player를 자동 전환하지 않는다.
- Frontend editor에 “현재 편집 상태를 새 버전으로 저장” action을 추가했다. empty/pending 상태를 명시적으로 비활성화하고 성공 뒤 Composition selection과 working aggregate를 reconcile하며 Undo/Redo stack을 비운다. 실패는 history를 보존하고 과거 committed version 이동은 기존 Snapshot checkout을 사용한다.
- 기존 schema와 `COMPOSITION_COMMIT` typed completion을 재사용해 migration은 추가하지 않았고 Alembic head는 `20260828_0024`다. 실제 사용자 DB·media·Provider·GPU·background daemon은 변경하지 않았다.

### 추가 — Working Preview Frontend Integration

- WorkingComposition editor에 명시적 Preview action을 추가하고 canonical Backend revision과 opaque `Idempotency-Key`로 `POST /working-composition/preview`를 호출한다. network response loss 재시도는 같은 key를 유지하고 새 사용자 action은 새 key를 사용한다.
- POST가 반환한 Job ID를 기존 Workspace Job read API로 adaptive polling하며 queued·running·ready·failed·cancelled를 구분하고 terminal·unmount·Project 변경에서 polling을 중단한다. `working_preview` exactly-one Artifact output만 선택하고 first/latest/generic audio fallback은 사용하지 않는다.
- Preview Artifact는 same-origin content route를 기존 Global Player에 사용자 재생 action으로만 전달한다. 새 audio element와 auto-play는 없으며 playback expiry/unavailable을 안전한 메시지와 rerender affordance로 표시한다.
- `rendered_revision != current revision`이면 prior Preview를 보존한 채 stale로 표시한다. queued/running 중 edit, rerender, revision conflict GET reconcile, last successful Preview와 refresh 후 idle/no-latest-fallback 경계를 반영했다.
- Chromium·tablet·mobile stateful Playwright와 API/state/poll/output/component/player 회귀를 추가했다. Backend production code·migration·Alembic `20260828_0024`·실제 사용자 DB/media·background daemon·Composition commit·Mixer는 변경하지 않았다.

### 추가 — Working Preview Render Backend Foundation

- ADR-052에서 explicit async `working_preview` Job, revision-pinned arbitrary Clip manifest, exact Artifact input과 non-canonical Preview Asset 소유권을 확정했다. Common AssetType·Provider·CompositionSnapshot·current selection·Export lineage는 변경하지 않는다.
- Project당 전용 Preview Asset 하나를 기존 `MIX` 물리 유형과 별도 `working_preview_assets` binding으로 소유하고, 성공 render마다 같은 Asset 아래 새 immutable AssetVersion·WAV Artifact와 `working_preview` JobOutput을 claim-owned transaction으로 생성한다. `artifacts.asset_version_id NOT NULL`을 유지하며 실패·cancel·timeout에는 empty version을 남기지 않는다.
- `POST /api/v1/projects/{project_id}/working-composition/preview`를 추가했다. same key·fingerprint는 최초 Job을 replay하고 새 action은 같은 revision에서도 새 Job을 허용한다. manifest는 16개를 넘는 Clip도 ordered Track·Clip, exact source AssetVersion/Artifact와 integer microseconds geometry를 손실 없이 보존한다.
- FFmpeg renderer는 WAV·FLAC·trusted MP3, trim·offset·silence·cross-Track overlap과 deterministic PCM16 stereo 48 kHz WAV를 지원한다. Track 64, Clip 512, 30분, input 128 MiB, output 384 MiB, timeout 300초를 적용하고 caller path/URL 없이 `shell=False`와 runtime random temp cleanup을 사용한다.
- Preview payload는 24시간 뒤 `expired`로 전환해 기존 Artifact content gate로 차단하고 AssetVersion·manifest provenance는 유지한다. additive Alembic `20260828_0024`는 Preview table 4개만 추가하며 backfill·기존 Artifact rewrite·실제 사용자 DB/media 적용은 없다. Frontend Preview action·Job polling·stale/Global Player integration은 다음 작업이다.

### 추가 — Track/Clip Waveform Frontend

- WorkingComposition Track lane의 각 Clip에 exact `source_asset_version_id → media-source → same-origin Artifact content` 경로를 연결하고 frozen `[source_in, source_out)` source window만 SVG Waveform으로 표시한다. D2 WebAudio loader의 128 MiB 입력, 최대 2,048 peak, bucket당 최대 256 sample 접근과 단일 bounded path를 재사용한다.
- editor session 범위 최대 64개 memory cache로 같은 AssetVersion의 resolver·fetch·decode를 공유한다. Project·WorkingComposition 변경과 unmount에서 pending 작업을 abort하고 stale completion을 폐기하며 persistent cache·sidecar Artifact·dependency를 추가하지 않았다.
- move는 동일 projection을 유지하고 trim·split·unsplit·resplit·delete/restore·Undo/Redo와 pending drag는 canonical Clip geometry에서 파생된다. zoom·horizontal scroll·Global Player Playhead는 같은 timeline 좌표계를 사용하며 Waveform은 pointer와 접근성 semantics를 가로채지 않는다.
- repository-owned segmented WAV를 사용하는 Chromium·tablet·mobile Playwright에서 source-window 차이, same-source request dedup, history identity, zoom/scroll alignment와 resolver·decode failure 중 편집 지속을 검증했다. Backend production code·schema·migration·실제 사용자 DB·media는 변경하지 않았다.

### 추가 — AssetVersion-safe Clip Media Resolution Foundation

- Project 범위의 exact `AssetVersion`을 현재 eligible한 exactly-one audio `Artifact`로 해석하는 read-only `GET /api/v1/projects/{project_id}/asset-versions/{asset_version_id}/media-source`를 추가했다.
- 기존 Clip mutation과 같은 effective Owner·Workspace·active ProjectAsset·active Asset·Artifact kind/media/retention·trusted duration authority를 재사용한다. zero/multiple candidate, revoked 관계, cross-owner·cross-project, unsupported media와 duration 부재는 latest/first fallback 없이 fail-closed한다.
- 응답은 opaque AssetVersion·Artifact identity, media type, size, checksum, duration과 same-origin Artifact content URL만 공개한다. path·storage key/root·locator·credential·signed URL·raw payload는 해석 응답에 포함하지 않으며 GET은 DB·revision·WorkingComposition을 변경하지 않는다.
- 기존 Artifact와 ProjectAsset 인덱스로 exact-version·scope query plan을 충족해 schema·Alembic은 변경하지 않았다. 이번 변경은 Track/Clip Waveform의 Backend source foundation만 완료하며 Frontend Waveform·binary endpoint·payload probe·working render는 계속 미구현이다.

### 구현 — Verified Durable Payload Staging Runtime Foundation

- `VerifiedPayloadStagingPort`와 `LocalFilesystemStagingAdapter`를 구현했다. locator-derived deterministic final key, random partial, bounded streaming SHA-256·size, WAV·FLAC·strict UTF-8 JSON 검증, file `fsync`, exclusive hard-link publish, concurrent reuse와 restart orphan adoption을 제공한다.
- verified open은 no-follow regular handle에서 전체 actual facts를 재검증하고, verified delete는 identity/facts mismatch를 성공으로 숨기지 않으면서 missing을 idempotent하게 처리한다.
- `PayloadStagingService`가 I/O 전후 claim·cancellation·rights authority를 재검증하고 짧은 transaction의 `source_bound → verified_staged` CAS를 수행한다. CAS loser의 equivalent object는 재사용하며 채택되지 않은 object는 안전하게 정리한다.
- downloader·Artifact ingestion·Completion·Worker wiring·API·schema·Alembic은 추가하지 않았다.

### 문서 — Verified Durable Staging authority 확정

- `VERIFIED_DURABLE_STAGING_LOCAL_ADAPTER_SUFFICIENT`를 확정했다. 기존 `DOHA_ARTIFACT_STAGING_ROOT`, actual-byte 검증, file `fsync`와 exclusive hard-link publish primitive를 재사용하고 locator-derived deterministic key로 restart orphan을 adopt한다.
- byte publish 후 최신 claim·cancel·rights·revocation·revision을 재검증해 `verified_staged` CAS를 수행한다. partial과 orphan cleanup, same-locator replay, missing/tampered object fail-closed, lifecycle 기반 retention과 `cleanup_pending → delete → cleaned` ordering을 고정했다.
- 별도 staging metadata table·Alembic·object storage 선행 추상화는 필요하지 않다고 판정했다. 이번 변경은 문서-only이며 local adapter, downloader, Artifact ingestion, Completion과 Worker wiring은 아직 미구현이다.

### 추가 — Frontend Clip Editing과 Memory Undo/Redo

- Project Composition 화면에 no-write WorkingComposition GET, 명시적 initialize와 현재 Snapshot checkout을 연결하고 Backend 응답의 `completed_revision` 및 후속 GET aggregate만 canonical state로 사용한다.
- exact Snapshot AssetVersion을 source로 고르는 Clip create와 Track create·rename·drag reorder·delete, Clip select·drag move·trim-start·trim-end·playhead split·delete UI를 추가했다. pointer move는 local preview만 수행하고 pointer release에서 mutation을 한 번 전송한다.
- Track/Clip create·delete는 same-ID restore로, split은 original·left·right identity 기반 unsplit/resplit으로 Undo/Redo한다. rename·reorder·move·trim은 absolute before/after 값을 사용하는 strict LIFO memory command stack으로 구현했다.
- initialize·checkout·Project 변경·revision/structure conflict reconcile은 history boundary이며 refresh 뒤 history를 복원하지 않는다. pending mutation은 직렬화하고 Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z, Ctrl+Y와 accessible 버튼을 제공한다.
- idempotent mutation별 새 opaque key를 만들고 네트워크 응답 유실 재시도에는 같은 key를 유지한다. structured Product error를 사용자 메시지로 매핑하며 실패한 mutation·Undo는 command stack을 이동시키지 않는다.
- Backend production code·migration·실제 사용자 DB·Artifact payload·media·Provider는 변경하지 않았다. Track/Clip 고도 Waveform, working preview/render, Composition commit, Mixer는 계속 미구현이다.

### 추가 — Durable Payload Locator Persistence Foundation

- `payloadref:v1:<32 lowercase UUID hex>` identity의 전용 `PayloadLocator` domain, persistence port, SQLAlchemy/SQLite adapter와 Service-owned transaction을 구현했다. `ProviderJobBinding 1:N`과 direct Workspace scope는 composite FK로 일치시키고 ordinal 및 canonical source tuple을 각각 unique로 보존한다.
- exact issue replay는 기존 locator를 반환하고 immutable mismatch는 `RESULT_REPLAY_CONFLICT`로 fail-closed한다. expected/actual facts, source/policy expiry, safe relative staging key, terminal revocation과 `source_bound → verified_staged → ingested → cleanup_pending → cleaned` revision CAS를 구현했다.
- additive Alembic `20260825_0023`과 fresh/existing upgrade·downgrade·re-upgrade, restart, idempotency, concurrent mutation, lifecycle, revocation 및 path/URL/credential security regression을 추가했다. `InMemoryTrustedPayloadRegistry`는 test/dev 호환으로 유지하고 Product API는 추가하지 않았다.
- durable byte staging, downloader orchestration, Artifact ingestion/Completion adapter, Worker dispatcher/reclaim과 실제 Provider Runtime은 계속 미구현이다.

### 추가 — DohaVocal 0.2.0 Payload Acquisition Consumer

- DohaVocal PR #6에서 병합된 `0.2.0` payload-backed Result와 `GetPayloadContent` capability를 기존 `0.1.0` metadata-only 경로와 분리한 strict DTO로 추가했다.
- payload source·role·SHA-256·size·media·availability와 Workspace contract version을 read-only trust gate에서 검증하고, ordered canonical replay identity가 달라지면 fail-closed한다.
- 고정 DohaVocal origin의 payload endpoint를 redirect 없이 bounded streaming으로 읽어 실제 size·media type·SHA-256을 검증하는 transient acquisition port를 추가했다. 당시 locator persistence도 미구현이었으나 현재 위 Persistence Foundation으로 구현됐고 byte staging, Artifact ingestion, Completion 및 Worker wiring은 계속 미구현이다.
- 결정 근거와 후속 `DURABLE_LOCATOR_REQUIRED` 재분석 dependency를 ADR-048에 기록했고, 재분석 결과는 ADR-049가 확정한다.

### 추가 — WorkingComposition Inverse Mutation

- ADR-050에서 Track restore의 explicit target order, Clip restore의 current eligibility fail-closed 검증, exact initial split geometry 기반 unsplit/resplit과 initialize·checkout Frontend history boundary를 확정했다.
- Track과 Clip을 delete 전 canonical ID로 복원하고, Track order shift/reindex 및 Clip overlap·parent·source 검증을 같은 revision transaction에서 수행한다.
- unsplit/resplit은 original·left·right row를 새로 만들지 않고 같은 canonical ID로 원자적으로 전환한다. child move·trim·delete 또는 lineage·source·geometry drift는 `SPLIT_STRUCTURE_CONFLICT`로 거부한다.
- revision-safe completion result V1에 `TRACK_RESTORE`, `CLIP_RESTORE`, `CLIP_UNSPLIT`, `CLIP_RESPLIT`을 추가하고 same-key replay가 최초 identity와 completed revision을 반환하도록 검증했다.
- WorkingComposition Product API를 17개 operation·16개 path로 확장했다. 기존 tombstone·lineage schema를 재사용해 Alembic head는 `20260825_0022`로 유지하며 실제 사용자 DB·media·Provider·Frontend는 변경하지 않았다.

### 추가 — WorkingComposition Atomic Mutation Service와 Product API

- WorkingComposition no-write GET, explicit initialize·Snapshot checkout, Track create·rename·reorder·delete와 Clip create·move·trim-start·trim-end·split·delete Service를 구현했다. 모든 상태 변경은 expected revision compare-and-swap으로 성공당 정확히 `+1`이며 Repository는 transaction을 소유하지 않는다.
- initialize는 GET-or-create가 아니다. 같은 key·fingerprint만 최초 ID/revision을 replay하고, 다른/new key의 duplicate와 DB unique race loser는 `409 WORKING_COMPOSITION_ALREADY_EXISTS`로 정규화하며 state·revision·성공 completion result를 남기지 않는다.
- exact AssetVersion·effective Owner/Workspace/Project·active ProjectAsset, exactly-one eligible Artifact와 trusted duration을 Clip create에 적용했다. integer microseconds half-up, same-Track 반개구간 overlap 거부, adjacency·cross-Track 중첩 허용, Track non-empty 삭제 거부와 tombstone lineage를 유지한다.
- `/api/v1/projects/{project_id}/working-composition` 아래 최초 13개 Product operation을 추가했다. 새 Router는 Service만 호출하고 path·locator·credential·raw DB 오류를 공개하지 않으며 새 operation ID 중복은 0개다.
- initialize·checkout·Track/Clip create/delete·split은 stored `IdempotencyCompletionResult`에서 최초 identity와 completed revision을 replay한다. split·checkout·reorder·create/delete 강제 실패와 concurrent initialize/revision race의 partial mutation 0을 격리 SQLite로 검증했다.
- Alembic head는 `20260825_0022`로 유지하고 신규 migration은 추가하지 않았다. 실제 사용자 DB·Artifact payload·media·Provider·GPU에는 접근하지 않았다. Composition commit, Frontend·Undo/Redo·Waveform·working preview/render·Mixer는 후속이다.

### 추가 — Revision-safe Idempotency Completion Result Foundation

- 완료 revision과 operation별 Product identity를 보존하는 immutable `IdempotencyCompletionResult`와 여덟 result type allowlist를 추가했다. payload는 canonical UUID key만 허용하고 UTF-8 JSON 8,192 bytes로 제한한다.
- 기존 `complete()`와 `resource_type/resource_id/response_status` replay를 유지하면서 `claim_with_result()`와 `complete_with_result()`를 추가했다. 새 결과가 없거나 일부만 있는 legacy·손상 row, unknown version/type은 fail-closed하며 Repository는 commit/rollback하지 않는다.
- Alembic `20260825_0022`로 nullable `completed_revision`, `result_type`, `result_version`, `result_payload`를 additive하게 추가했다. 기존 `COMPLETED` row는 추측 backfill하지 않았고 실제 사용자 DB에는 적용하지 않았다.
- ADR-047에 성공 mutation만 같은 transaction에서 결과를 저장하는 replay·legacy·보안 권위를 기록했다. 후속 WorkingComposition mutation Service와 Product API가 이 결과 계약을 소비한다.

### 문서 — Durable Execution Handoff authority 확정

- 최신 `develop`의 Workspace Job·ProviderJobBinding·Provider replay·Result trust gate·Completion authority를 다시 분류하고 17개 crash/restart case를 판정했다.
- ADR-046에서 locator 전 Provider execution·Result validation은 `NO_NEW_DURABLE_HANDOFF_STORAGE_REQUIRED`로 확정했다. 새 entity/field, schema와 Alembic은 필요하지 않으며 다음 dependency는 `DURABLE_LOCATOR_REQUIRED`다.
- CURRENT reclaim Runtime은 계속 미구현이다. Python·DB·API·Frontend·Provider wiring·network·Artifact·Dataset·model/GPU는 변경하지 않았다.

### 유지보수 — Ruff 정적검사 기준선

- Python root를 `backend`와 `ai_worker`, test root를 `backend/tests`로 명시하고 로컬과 CI의 compileall·Ruff lint·Ruff format 명령을 통일했다.
- Ruff 0.16.4와 명시적 rule set을 저장소 자체 설정으로 고정해 상위 디렉터리 설정 상속을 제거하고, first-party Python 전체의 lint·format 기준선을 정리했다.
- project-root bootstrap 뒤 import가 필요한 AI worker script 세 개만 근거가 있는 `E402` 예외로 제한했다. 기능·API·DB·Dataset·Training·authentication 계약은 변경하지 않았다.

### 추가 — Clip Service Authority Foundation

- ADR-045에서 active Clip이 없는 Track만 tombstone하고, 하나라도 있으면 `TRACK_NOT_EMPTY`로 거부하며 cascade 삭제하지 않는 V1 의미를 확정했다.
- trusted ingestion이 WAV frame 또는 FLAC STREAMINFO에서 계산한 양의 integer microseconds를 `Artifact.duration_us`에 저장하도록 구현했다. MP3는 정확한 현재 의존성이 없어 길이를 추정하지 않고 Clip source에서 fail-closed한다.
- Alembic `20260824_0021`로 nullable `artifacts.duration_us`와 양수 CHECK를 additive하게 추가했다. 기존 Artifact row의 backfill·Payload rewrite와 실제 사용자 DB 적용은 0건이다.
- exact AssetVersion의 active audio Artifact 후보가 정확히 하나이고 trusted duration이 있을 때만 Clip source metadata를 반환하는 내부 Service와 active Clip count Repository primitive를 추가했다. WorkingComposition mutation·API·Frontend·Provider·GPU 호출은 추가하지 않았다.

### 문서 — Workspace Worker Re-entry Lifecycle Authority

- replay-safe Provider-backed Job의 정책을 `LEASE_EXPIRY_RECLAIMABLE`로 정하고 graceful yield, process crash, explicit shutdown, Provider/payload wait를 구분했다.
- 공개 다섯 상태와 기존 claim·lease·heartbeat·attempt·stage 필드만으로 `running -> running` atomic ownership transfer, old token 무효화, cancel·Completion race와 binding-first resume를 ADR-044에 확정했다.
- 현재 runtime의 `WORKER_LEASE_EXPIRED` failure는 후속 구현 전까지 유지한다. Python·schema·Alembic·API·Frontend·Provider wiring은 변경하지 않았고 durable execution handoff 분석만 재개 가능 상태로 전환했다.

### 문서 — DohaVocal Worker Reconciliation Contract

- Provider `succeeded`와 Workspace `succeeded`를 분리하고 metadata-only 결과의 `running` 유지, 내부 `stage`, lease·bounded polling·cancel·retry ownership을 authoritative contract와 ADR-043으로 확정했다.
- same-key Provider Create replay, 1:N binding append, mandatory Result trust gate, DohaMusic-owned payload reconciliation, candidate→Workspace role mapping, Completion eligibility·replay와 12개 crash/restart case를 문서화했다.
- 당시 장시간 same-Job resume와 production payload 복구를 각각 `DURABLE_EXECUTION_HANDOFF_REQUIRED`, `DURABLE_LOCATOR_REQUIRED`로 구분했다. 전자의 lifecycle 선행 결정은 후속 ADR-044가 확정했으며 실제 reclaim 구현은 남아 있다. Python·DB schema·Alembic·API·Frontend·Provider wiring·network·Artifact·Dataset·model/GPU는 변경하지 않았다.

### 추가 — Clip Domain Persistence Foundation

- Project당 하나의 `WorkingComposition`, V1 audio `CompositionTrack`, exact `AssetVersion` 기반 `CompositionClip`과 불변 `CompositionSnapshotTrack`·`CompositionSnapshotClip` SQLAlchemy 모델을 추가했다.
- Alembic `20260824_0020`에서 integer microseconds 시간 CHECK, same-Project·same-Working·same-Snapshot 복합 FK, `RESTRICT`, tombstone·split lineage와 canonical query Index를 additive하게 구현했다. 기존 Snapshot/SnapshotItem row backfill·rewrite는 0건이다.
- Repository의 bounded mix settings, deterministic Track/Clip·Snapshot read, 반개구간 same-Track overlap 검사와 expected revision `+1` 기반을 추가했다. Repository는 `commit()`·`rollback()`을 호출하지 않으며 실패 rollback 0건을 격리 SQLite 회귀로 검증했다.
- source media duration probe와 Owner·Workspace·ProjectAsset·audio Artifact eligibility, mutation Service/idempotency orchestration, API·Frontend·render·Provider 호출은 구현하지 않았고 실제 사용자 DB `20260810_0017`에는 접근하거나 migration을 적용하지 않았다.

### 추가 — Local Operator Authentication Foundation

- Windows 공식 API 근거로 V1 concrete proof mechanism을 `WINDOWS_WEBAUTHN_PLATFORM_CREDENTIAL`로 선택하고 process token, Credential Manager generic credential, DPAPI와 다른 후보의 부적합 경계를 ADR-042에 기록했다.
- Provider-independent credential reference·principal·verified context 계약, private witness/provenance 검증과 fail-closed bootstrap을 추가했다. Concrete Win32 adapter는 미구현이며 configured·operational 상태는 false다.
- Deterministic Fake는 test-only로 제한했고 raw secret, 실제 credential, reviewer mapping·authority·approval, DohaAudio Runtime과 Dataset·Training에는 side effect를 만들지 않았다.

### 추가 — Trusted Payload Locator / Resolver Contract

- `payloadref:v1:<opaque-id>` 형식의 DohaMusic-owned `TrustedPayloadReference`, 내부 issuer/resolver protocol과 deterministic in-memory Foundation을 추가했다.
- 기존 trusted staging root 안의 regular file만 canonicalize하고 symlink·reparse·traversal을 차단하며 실제 bytes에서 SHA-256·size·media type을 계산한다. resolve 때 expiry, file identity와 metadata를 재검증하고 path 없는 안전한 오류를 반환한다.
- metadata-only Provider Result는 계속 `payload_present=false`, reference 없음, ingestion non-eligible이다. Public API·Alembic·Worker wiring·Provider network/downloader·Completion adapter·실제 Artifact ingestion은 추가하지 않았다.

### 문서 — Clip Domain / Persistence Authority

- ADR-040에서 Project당 하나의 mutable `WorkingComposition`, Composition lineage 범위의 canonical `track_id`·`clip_id`, exact `source_asset_version_id`와 Service·복합 FK 기반 same-Project 경계를 결정했다.
- move·trim·delete·split의 비파괴 의미, split 시 원본 tombstone과 left/right 새 identity, same-Track overlap 금지, active Clip end 기반 working duration을 확정했다.
- 즉시 server persistence와 Snapshot commit을 분리하고 revision optimistic concurrency, 기존 Idempotency 기록 재사용, Frontend memory Undo/Redo와 Service-owned atomic transaction·rollback을 정했다.
- 현행 `SnapshotItem`은 Timeline을 표현할 수 없어 별도 불변 Snapshot Track/Clip schema가 필요함을 `COMPOSITION_SNAPSHOT_SCHEMA_EXTENSION_REQUIRED`로 기록했다. Backend·Frontend·테스트·Alembic·API·DB·Common Contract·Dataset·Training·GPU는 변경하지 않았다.

### 문서 — V1 Reviewer Authentication Product Authority

- ADR-038의 initial evidence-only CASE B와 fail-closed 판단을 보존하고, 이후 product owner가 V1 local-only·single owner/operator·DohaMusic local governance UI와 OS-bound local operator proof model을 명시적으로 승인했음을 기록했다.
- Upstream `LOCAL_AUTHENTICATED_OPERATOR`와 downstream `DOHAMUSIC_DELEGATED_ASSERTION`을 분리하고 external auth network 불필요·offline capable·V1 MFA 미필수, local persistent private store 요구를 확정했다.
- `AUTH_REQUIREMENTS_RESOLVED=true`, `AUTH_PROVIDER_SELECTION_READY=true`와 selected authentication provider model을 기록했지만 실제 OS adapter·assertion·secret·mapping·ReviewerAuthority·approval 및 DohaAudio Runtime은 변경하지 않았다.

### 추가 — Waveform / Richer Playhead Foundation

- 선택된 Snapshot의 단일 `mix` Item·단일 safe audio Artifact를 동일한 playback 권위로 재사용해 읽기 전용 Master / Mix Waveform overview를 추가했다. Browser가 same-origin Artifact content를 decode하며 Backend API·DB·Alembic은 변경하지 않았다.
- source 크기를 128 MiB 이하로 제한하고 최대 2,048개 peak와 각 channel의 각 peak bucket에서 sample array element를 최대 256회만 접근해 단일 SVG path로 렌더한다. source 변경·unmount 시 진행 요청을 취소하고 stale 결과를 폐기하며 AudioBuffer는 peak 추출 뒤 보존하지 않는다.
- ruler·Waveform click seek, Playhead drag preview/commit, hover 정밀 시간과 keyboard seek를 동일한 scroll·zoom 좌표계에 연결했다. Waveform 실패는 기존 재생과 분리하며 별도 audio element, `requestAnimationFrame` loop, Track/Clip 편집, Section·range selection·Mixer를 추가하지 않았다.

### 추가 — Timeline Playback Foundation

- 선택된 CompositionSnapshot의 snapshot-local Track projection을 사용하는 읽기 전용 초 단위 Timeline shell, ruler, lane, Playhead, local Track 선택과 horizontal scroll·zoom 기반을 추가했다.
- AppShell의 기존 Global Player를 단일 audio authority로 확장해 play/pause·currentTime·duration·seek·ended·loading·error를 Timeline과 동기화했다. duration과 Playhead는 실제 media metadata·event만 사용한다.
- 단일 `mix` Item과 단일 safe audio Artifact만 canonical source로 해석하고, 없거나 모호하면 `NO_CANONICAL_PLAYBACK_SOURCE`로 재생을 비활성화한다. first/latest fallback과 multi-track audio 조합은 추가하지 않았다.
- click-to-seek의 viewport offset·scroll·zoom·clamp, keyboard transport, 접근성 label, responsive overflow와 기존 Player·Composition 회귀 테스트를 추가했다. Backend API·DB·Alembic·실제 사용자 DB·Provider·Common Contract·Dataset·Training·GPU는 변경하지 않았다.

### 추가 — Provider Result Artifact Ingestion Contract

- DohaVocal `VocalProviderResultCandidate`와 Workspace Artifact authority 사이에 read-only trust gate를 추가해 durable binding, owner-scoped Job, provider/job identity, output role, dynamic Manifest, settings, source/parent lineage, Processing Chain owner와 checksum scope를 검증한다.
- 검증된 Provider Artifact·output AssetVersion identity는 opaque metadata로만 보존하고 `(binding, output role, provider artifact)` idempotency key를 제공한다. path·URI·credential 형식과 lineage spoof는 side effect 없이 fail-closed한다.
- `payload_present=false`와 `metadata_descriptor` checksum을 binary·structured Artifact로 승격하지 않으며 기존 `ProviderOutput.temporary_path`와 Completion UoW의 payload-backed 불변식을 유지한다. `vocal_analysis` Fake descriptor도 실제 JSON Payload가 아니므로 non-eligible이다.
- DB·Alembic·Public API·Worker wiring·Provider network·실제 payload ingestion·Artifact/AssetVersion/JobOutput/ModelUsage 생성·Workspace Job terminal 전이는 추가하지 않았다.

### 추가 — D1 Workspace Composition Frontend

- 기존 Project 상세 Route에서 `CompositionWorkspaceRead` aggregate의 `empty`, `selection_required`, `ready` 상태를 Backend authority 그대로 표시한다.
- Snapshot 후보를 자동 선택하지 않고 사용자의 명시적 적용에만 selection PATCH를 호출한 뒤 aggregate를 refetch하며, 제출 중 control 비활성화와 재진입 복원을 구현했다.
- ready 화면은 Snapshot-local Track projection, exact AssetVersion, 경로를 노출하지 않는 Artifact metadata·공개 content/download URL, Section `not_available`, Mix snapshot과 lineage를 표시한다.
- Frontend fixture 통합·API contract·loading·오류·기존 Studio 회귀 테스트를 추가했다. Backend 공개 API·DB·Alembic·실제 사용자 DB·Provider·Artifact payload·Common Contract·Dataset·Training·GPU는 변경하지 않았다.

### 문서 — Reviewer Authentication과 배포 권위

- CURRENT DohaMusic을 `LOCAL_ONLY`·no-product-login으로 확인하고, product·service identity는 DohaMusic, opaque reviewer mapping·ReviewerAuthority는 DohaAudio가 소유하는 경계를 ADR-037로 기록했다.
- Frontend·일반 사용자의 DohaAudio 직접 호출을 배제하고 delegated DohaMusic identity를 목표 trust direction으로 정하되, production topology·semantic reviewer population·interaction·실제 authentication provider는 product-owner 결정 전까지 미확정으로 유지했다.
- OAuth/OIDC/GitHub/local auth, assertion, secret, private identity DB, mapping·authority·approval을 구현하지 않았고 DohaAudio와 Rights·Dataset·Model·Training gate를 변경하지 않았다.

### 추가 — D1 Composition Transition

- Workspace Bootstrap exact revision Gate를 `0018`의 필수 Table, selection PK·unique, same-Project 복합 FK와 Snapshot identity Index까지 확장했다.
- 기존 persistence에 project-level selected Snapshot 권한이 없음을 확인하고 `NO_PREEXISTING_SELECTION_AUTHORITY`로 고정해 Snapshot 수와 무관하게 selection backfill·latest fallback·dual write를 0건으로 유지한다.
- Bootstrap transaction 안에서 활성 Project의 `empty`, `selection_required`, 기존 valid selection을 단일 batch inventory로 분류하고 구조화된 결과를 반환한다. invalid·cross-Project selection은 자동 교정 없이 중단한다.
- 실제 사용자 DB 접근·migration·backfill, Frontend, Provider, Artifact payload, Common Contract, Dataset·Training·GPU는 변경하지 않았다.

### 추가 — Provider Job Persistence Contract

- `provider_job_bindings`와 Alembic `20260821_0019`를 추가해 Workspace Job별 Provider 실행 identity와 retry 1:N history를 durable DB authority로 보존한다.
- `(provider_id, provider_job_id)` uniqueness, Workspace Job `RESTRICT` FK, same-Provider retry self-FK와 self-retry CHECK로 duplicate·cross-provider·unknown parent를 fail-closed한다.
- Owner-scoped create/history/latest Service와 restart recovery, cross-Workspace retry, 경계 기반 logical ID validation, transaction rollback을 검증한다. composite identity 중복과 다른 DB 무결성 실패를 구분하며 Provider status·Public API·Worker wiring·Provider 호출·Artifact ingestion은 추가하지 않았다.

### 추가 — Workspace Job Vocal Capability Contract

- Workspace Job 공식 type을 `vocal_generation`, `voice_conversion`, `vocal_correction`, `vocal_analysis` 네 DohaVocal capability로 확장하고 capability별 required/optional input role과 pre-ingestion candidate output role을 고정했다.
- strict discriminated `job_input`을 추가해 Generation reference, Conversion source·voice reference·entity type, Correction type, Analysis type을 검증하고 `training_dataset_id=null`을 강제한다. 검증된 입력은 기존 JSON settings snapshot의 서버 예약 키에 불변·결정적으로 보존한다.
- Project·effective Owner·선택 Composition Snapshot·동적 Manifest·idempotency fingerprint와 기존 다른 Provider의 `voice_conversion` 호환성을 유지했다. 선택 parent Version은 source의 동일 Asset 파생 계보로, Processing Chain은 effective Owner 소유로 검증한다. 공개 Job 생성 request contract는 확장되지만 Endpoint·DB·Alembic·Worker·Provider 실행·Artifact ingestion은 변경하지 않았다.

### 추가 — DohaVocal HTTP Transport Foundation

- 기존 `VocalProviderTransport` port에 재사용 가능한 동기 `HttpVocalProviderTransport`를 추가하고 DohaVocal `0.1.0`의 9개 HTTP operation을 연결했다.
- config-only base URL, HTTP(S)·no-userinfo 검증, connect/read/write/pool timeout, 동적 path encoding, JSON Content-Type·body fail-closed와 transport 자동 retry 비활성 경계를 구현했다.
- `httpx.MockTransport`로 wire request·5-state·retry·lineage·Manifest·오류·timeout·계약 drift·secret 비노출을 실제 network 없이 검증했다. Worker wiring·Artifact ingestion·DB·Alembic·공개 API·인증·실제 audio/AI는 변경하지 않았다.

### 추가 — D1 Composition Aggregate Backend Read

- Project와 선택된 불변 `CompositionSnapshot`의 동일 소속을 DB 복합 FK와 Service에서 함께 강제하는 `project_composition_selections` 1:1 상태와 additive Alembic `20260820_0018`을 추가했다.
- `GET /api/v1/projects/{project_id}/composition`과 `PATCH /api/v1/projects/{project_id}/composition-selection`을 구현해 `empty`, `selection_required`, `ready`, 명시적 history override를 제공한다.
- aggregate는 exact AssetVersion, 경로를 노출하지 않는 Artifact 참조, Snapshot-local Track projection, Section `not_available`, Mix JSON과 저장된 lineage만 batch 조회하며 GET은 Legacy fallback·bootstrap·backfill·selection 변경을 수행하지 않는다.
- 실제 사용자 DB migration, Workspace bootstrap·backfill, Frontend, Provider, Artifact payload, Dataset·Training·GPU와 Common AI Contract는 변경하지 않았다.

### 수정 — DohaVocal Manifest fixture 정합화

- DohaVocal stable wire fixture의 Fake Model Manifest ID를 실제 Runtime authority인 `dohavocal.fake-model@0.1.0`으로 정합화하고, canonical Provider ID `dohavocal`과의 분리를 회귀 테스트로 고정했다.

### 문서 — D1 Composition Read 선행 계약

- Workspace v1을 Composition read authority로 정하고 Legacy silent fallback과 GET 자동 bootstrap·backfill·selection 변경을 금지했다.
- Project-level explicit selected/current Snapshot, requested history read, SnapshotItem 기반 snapshot-local Track projection과 Section `not_available` 정책을 ADR-035로 결정했다.
- `GET /api/v1/projects/{project_id}/composition`의 `CompositionWorkspaceRead` 응답, exact AssetVersion·safe Artifact·Mix·lineage·empty/auth/error 계약과 D1 DoD를 확정했다.
- Backend·Frontend·DB·Alembic·실제 데이터·Provider·Common Contract schema·Dataset·Training·GPU는 변경하지 않았다.

### 문서 — Database 현재·목표 구조 정합화

- Database Overview를 CURRENT Runtime, CURRENT Workspace/Domain, TARGET과 TRANSITION의 Canonical entry point로 정리했다.
- 실제 SQLAlchemy와 Alembic 기준으로 Runtime 14개, Workspace 도메인 21개, 별도 Storage Catalog 1개와 전체 Application metadata 36개의 계산 범위와 책임을 구분했다.
- CURRENT Runtime ERD·Core Table Definition의 범위를 명시하고 Pipeline·Voice Conversion 상세 문서를 유지했다.
- TARGET 문서를 `PARTIALLY IMPLEMENTED`, Migration 전략을 `TRANSITION`으로 표시해 구현된 additive schema와 미구현 backfill·dual write·Runtime 전환을 분리했다.
- 코드·DB schema·Alembic·Provider·Runtime·Dataset·Training·GPU·Common Contract는 변경하지 않았다.

### 문서 — Documentation Cleanup

- 제품 개요와 목표·비목표의 고유 내용을 AI-native DAW Product Authority로 통합하고 기존 안내 문서를 명시적으로 대체 처리했다.
- Frontend route·design·responsive·component 기준을 실제 code tree 중심 문서로 통합하고 오래된 설계 초안 5개를 `docs/archive/frontend/`로 보존 이동했다.
- Legacy Phase 계획 6개를 `planning/archive/`로 이동하고 History·Project 중복 설명을 API 문서로 통합했다.
- 기존 문서의 추가 최상위 제목을 하위 heading으로 정리해 Markdown 문서당 H1 하나 기준을 맞췄다.
- Authority Map과 Cleanup Plan을 최종 경로·분류·처리 결과에 맞췄으며 코드·DB·Training·Dataset·GPU 변경은 없다.

### 추가 — DohaVocal Consumer Contract Foundation

- DohaVocal `0.1.0`의 4개 Vocal capability와 9개 Provider operation을 strict DTO로 해석하는 `VocalProviderClient`·transport port·권한 context mapping을 추가했다.
- effective Owner·Project·입력 ID·Model Manifest·settings snapshot·Provider idempotency를 mapping하고 5-state, 새 Job retry, root/parent/processing chain, metadata-only 결과와 Manifest 검토 상태를 손실 없이 보존한다.
- stable JSON fixture와 fake transport contract test를 추가했다. 실제 network·audio·AI·GPU·Dataset·DB·Alembic·공개 API·Workspace Worker 조립은 변경하지 않았다.
- Provider application·transport·timeout·invalid response·contract version 오류를 구분하고 raw body·경로·token·stack trace를 노출하지 않도록 fail-closed 한다.

### 문서 — Canonical Authority와 Navigation 정리

- README를 CURRENT·TARGET·Architecture·개발 Track·질문 중심 Navigation·Safety 중심의 Repository entry point로 단순화했다.
- 추적 중인 Markdown 문서를 CANONICAL·SUPPORTING·HISTORICAL·SUPERSEDED·STALE로 분류하는 `DOCUMENT_AUTHORITY_MAP`과 후속 통합·deprecate·archive 위험을 기록한 `DOCUMENT_CLEANUP_PLAN`을 추가했다.
- Product·System Architecture·Master Roadmap·실행 Roadmap·Operations·DoD의 책임을 구분하고 PR #94 병합 증거에 따라 AI-native DAW D0 문서 기준을 완료로 갱신했다.
- 오래된 MVP·Phase 계획과 구현 전 Frontend 설계안에 현재 Authority가 아님을 표시했다. 파일 이동·삭제와 제품 코드·DB·Provider·Common Contract·Dataset·Training 변경은 수행하지 않았다.

### 문서 — AI-native DAW 제품 목표 정합성

- DohaMusic 장기 목표를 AI-native DAW, Project/Composition Runtime, Provider Orchestrator, Composition Evaluation/QA와 Continuous Learning Hub로 정의하고 현재 Responsive Studio MVP와 TARGET·NOT IMPLEMENTED를 분리했다.
- Reference, Creation, DAW Editing, Composition QA와 Continuous Learning 흐름 및 단계별 Frontend 전환 계획, DohaLM Frontend retirement Gate를 추가했다.
- 공통 `MusicIntent`, `RevisionPlan`, `SimilarityReport`, Learning·Rights·Dataset·Training 객체를 재사용하고 신규 `EditIntent`를 만들지 않는 원칙을 기록했다. `CompositionEvaluationRun`과 `TimelineSelection`은 schema 미확정 DohaMusic product-only 후보로 한정했다.
- 코드·DB·Alembic·Runtime·Provider·Training·Dataset·Common Contract schema는 변경하지 않았다.

### 추가 — Common AI Contract Python 소비자 기반

- `DohaStudio/.github`의 `dohastudio-common-ai-contracts` `0.1.0`을 병합 commit `dd75fc88c16e9ae9a04acfafb72756a905f6365b`에 불변 고정하고, package·policy·resource identity를 fail-closed로 확인하는 opt-in loader를 추가했다.
- canonical `rights_metadata` payload를 package public API로 검증하고 `ValidationIssue`를 그대로 반환하는 얇은 adapter를 추가했다. schema 복제, 누락 field 추정, Voice consent 승격, Runtime·DB·Provider 연결은 수행하지 않았다.
- 설치 후 schema 조회와 검증의 offline 동작, import 무부작용, package 부재·불일치의 안전한 실패를 테스트로 고정했다.

### 추가 — Workspace Job Resource API

- effective Workspace의 Job 목록·생성·aggregate 상세·취소·재시도 공식 Endpoint 5개를 `/api/v1/jobs`에 추가했다. Router는 `JobService`만 호출하고 HMAC Cursor, Owner scope, 7개 Job type·Snapshot·input role Matrix와 생성·재시도 멱등성 계약을 재사용한다.
- 공개 DTO는 정렬된 JobInput·JobOutput·ModelUsage와 안전한 오류만 반환하며 `claim_token`, Worker·lease 정보, 소유자 내부 UUID와 storage path를 노출하지 않는다. Job API는 5/5, Resource API는 30/64다.
- 실제 Provider transport, background daemon·scheduler, Frontend, Alembic, Entity, 실제 사용자 DB와 `DohaArtifacts`는 변경하지 않았다. 이 Draft PR 병합 전에는 Backend Foundation Complete와 Generative AI Track OPEN을 선언하지 않는다.

### 추가 — Workspace Job Completion Unit of Work

- Workspace Job의 atomic claim, bounded Worker ID, opaque claim token, lease·heartbeat, race-safe 만료 recovery와 단일 `run_once()` Provider dispatch 기반을 추가했다. 공개 Worker 오류 코드는 64자 이하의 대문자·숫자·underscore machine code만 허용하고 나머지는 안전한 내부 코드로 대체한다.
- fake dispatcher가 success·failure·timeout·명시적 cancel·malformed 결과와 장시간 heartbeat를 재현하며 성공 결과는 Completion UoW에 claim token과 함께 위임한다. 동일 Job의 transport retry는 canonical idempotency key를 재사용하고 completion replay에서 Workspace lineage를 중복 생성하지 않는다.

- Provider 결과 DTO와 output role Matrix를 검증하고 trusted ingestion·불변 publish·필요한 AssetVersion·Artifact·Catalog·JobOutput·ModelUsage·최종 무결성·`succeeded` 전이를 하나의 Service 소유 DB transaction으로 확정하는 Workspace Job Completion Unit of Work를 추가했다.
- 동일 completion replay는 기존 결과를 반환하고 다른 결과는 충돌로 거부한다. cancel marker 우선, 다중 출력 rollback, publish 후 DB 실패의 identity 기반 보상과 staging cleanup을 임시 SQLite·Artifact root에서 검증했다.

### 변경 — Artifact ingestion transaction 경계

- Artifact trusted ingestion을 기존 독립 transaction과 Completion UoW가 함께 사용하는 prepare/register/verify/compensate primitive로 분리했다. Repository의 직접 `commit()`·`rollback()`과 신규 Alembic revision은 추가하지 않았다.

### 추가

- Workspace Job 공식 7개 type과 Snapshot·input role Matrix, effective Owner·ProjectAsset·exact Artifact/AssetVersion lineage를 강제하는 owner-scoped 생성 Service를 추가했다.
- Job·JobInput과 기존 `idempotency_records`를 한 transaction에서 생성하고 동일 create/retry 요청 replay, 충돌 거부와 실패 rollback을 검증했다.
- progress 단조 증가, bounded stage·안전한 오류, terminal 불변, queued 즉시 취소·running cancel marker, frozen retry와 ordered aggregate read Service를 추가했다.

### 변경

- Job Foundation은 Service state/cancel/retry와 Completion Unit of Work 기반까지 완료했지만 Worker claim/lease runtime, Provider 실제 호출과 Job API 5개는 계속 미구현이다. Resource API 25/64, metadata 36개 Table과 Alembic `20260810_0017`은 변경하지 않았다.

### 추가 — Workspace Job Cursor와 keyset Repository 기반

- HMAC Cursor version 1에 `job` Resource를 추가하고 `(created_at DESC, job_id DESC)` position과 effective Owner·Workspace·선택 `project_id`·`status`·`job_type` fingerprint를 적용했다.
- 기존 offset 조회는 호환성을 위해 유지하면서 Owner scope를 `workspaces.owner_id`에서 강제하는 `list_jobs_after()`와 `limit + 1` 기반 `JobPage`를 추가했다. 다른 Owner·Workspace·filter의 Cursor 재사용은 `INVALID_CURSOR`로 거부한다.
- 10,000건 임시 SQLite fixture에서 Workspace·Project·status·type의 첫·다음 page가 revision `20260810_0017`의 keyset Index를 사용하고 `jobs` full scan과 `TEMP B-TREE`가 없음을 검증했다.
- Job API는 0/5, 전체 Resource API는 25/64를 유지한다. Service state machine·Worker·Provider 호출·Alembic·실제 사용자 DB·Frontend는 변경하지 않았다.
- 구현과 검증 결과는 [Workspace Job Cursor 기반 검증](reports/validation/VALIDATION-WORKSPACE-JOB-CURSOR-FOUNDATION.md)에 기록했다.

### 수정 — Bootstrap CLI revision 기준 0017 동기화

- Bootstrap CLI가 정확히 허용하는 revision을 실제 사용자 DB와 Alembic source head에 맞춰 `20260809_0016`에서 `20260810_0017`로 변경했다.
- minimum revision이나 일반 Alembic DAG 판정은 도입하지 않았으며 `0016` 이하, 알 수 없는·형식 오류 revision과 revision row 0개 또는 복수를 계속 fail-closed로 거부한다.
- 임시 SQLite로 revision Gate만 검증하며 실제 사용자 DB 접근·Bootstrap 실행·Alembic·Entity·API를 변경하지 않는다. Metadata 36개 Table, Resource API 25/64, Job API 0/5와 Runtime Table 14개의 source of truth 상태를 유지한다.
- 구현과 검증 결과는 [Bootstrap CLI revision 0017 검증](reports/validation/VALIDATION-BOOTSTRAP-REVISION-0017.md)에 기록한다.

### 추가 — Workspace Job schema와 keyset 기반

- `jobs`에 Workspace scope, 내부 취소 요청, claim token·Worker·lease·heartbeat·attempt를 추가하고 `job_inputs`·`job_outputs`에 nullable staging role Column을 추가했다.
- source revision `20260810_0017`은 기존 Job의 Workspace를 Project에서 채우며 해석할 수 없는 row가 있으면 중단한다. 실제 사용자 DB `20260809_0016`에는 적용하지 않았다.
- Workspace·Project·status·type 목록 keyset Index 4개와 claim queue·lease recovery Index 2개를 추가하고 10,000 Job 임시 SQLite fixture에서 대상 Query의 full scan과 `TEMP B-TREE` 제거를 검증했다.
- 기존 Runtime Table 14개, 공개 5-state, Metadata 36개 Table과 Resource API 25/64를 유지한다. Job Cursor·Repository keyset·Service state machine·Worker·Provider 호출·API 5개, 실제 DB·Bootstrap·backfill·dual write·Frontend는 변경하지 않았다.
- 구현과 검증 결과는 [Workspace Job schema·Index Migration 검증](reports/validation/VALIDATION-WORKSPACE-JOB-SCHEMA-MIGRATION.md)에 기록했다.

### 문서 — Workspace Job Foundation 공식 계약

- Legacy Generation·Stem·Voice·Pipeline Job과 Workspace `jobs` Aggregate를 분리하고 현재 완료 상태가 Workspace Job API 완료를 의미하지 않음을 명시했다.
- 현재 제품 근거가 있는 7개 Job type과 Snapshot·input/output role Matrix, exact Artifact 선택, 공개 5-state·내부 cancel marker, 새 Job retry와 Owner·Workspace scope를 확정했다.
- Provider header idempotency, Worker claim·lease·heartbeat·crash failure, completion Unit of Work와 부분 출력 quarantine·보상 경계를 [Workspace Job Foundation](docs/03-architecture/workspace-job-foundation.md)과 [ADR-033](docs/11-decisions/ADR-033-workspace-job-execution-boundary.md)에 기록했다.
- 다음 additive Migration의 Workspace scope·role·cancellation·claim/lease Column과 검증할 keyset Index를 문서화했다. Python·Entity·Alembic·실제 DB·Artifact·Provider·Worker·API는 변경하지 않았으며 Job API 0/5, Resource API 25/64, source와 실제 DB `20260809_0016`, metadata 36개 Table을 유지한다.

### 수정 — Bootstrap CLI revision 기준 동기화

- Bootstrap CLI가 허용하는 대상 revision을 이전 `20260807_0013`에서 현재 Alembic source head와 정확히 일치하는 `20260809_0016`으로 변경했다.
- 최소 revision 비교나 일반 Alembic DAG 판정은 도입하지 않고, 과거·미래·알 수 없는·형식 오류 revision과 revision row 0개 또는 복수를 fail-closed로 거부한다.
- 임시 SQLite에서 현재 revision Gate 통과와 거부 경계를 검증했으며 실제 사용자 DB 접근·Bootstrap·Migration은 수행하지 않았다. Metadata 36개 Table, Resource API 25/64와 Runtime Table 14개의 source of truth 상태는 변경하지 않았다.
- 구현과 검증 결과는 [Bootstrap CLI revision 0016 검증](reports/validation/VALIDATION-BOOTSTRAP-REVISION-0016.md)에 기록했다.

### 추가 — CompositionSnapshot Resource API

- `GET /api/v1/snapshots`, `POST /api/v1/snapshots`, `GET /api/v1/snapshots/{composition_snapshot_id}` 공식 Resource API 3개를 추가했다.
- Router는 App Factory가 주입한 `CompositionService`만 호출하며 effective Owner·ProjectAsset scope, exact AssetVersion, 자동 version, 3회 concurrency retry와 Snapshot+Item+Idempotency 단일 transaction을 재사용한다.
- 목록은 Project 필수 filter와 `snapshot_version DESC, composition_snapshot_id DESC` HMAC Cursor를 사용하고, 생성은 필수 `Idempotency-Key`의 동일 요청을 최초 `201` aggregate로 재생한다.
- 상세는 불변 Snapshot과 `(item_role, sort_order, snapshot_item_id)` 순서의 Item을 반환하고 `owner_id`·`created_by`·Artifact 선택 필드는 노출하지 않는다. PATCH·DELETE와 독립 SnapshotItem Route는 추가하지 않았다.
- Resource API는 25/64, CompositionSnapshot API는 3/3이다. Metadata 36개 Table과 Alembic `20260809_0016`을 유지하며 실제 사용자 DB·실제 `DohaArtifacts`에는 접근하지 않았다.
- 구현과 임시 SQLite 검증 결과는 [CompositionSnapshot API 검증](reports/validation/VALIDATION-COMPOSITION-SNAPSHOT-API.md)에 기록했다.

### 추가 — CompositionSnapshot 계약과 Cursor 기반

- effective Owner·활성 Project·같은 Workspace 또는 Owner 소유 global Asset·활성 ProjectAsset 관계를 검증하는 CompositionSnapshot Application 기반을 추가했다.
- `lyrics|music|vocal|stem|mix` 역할과 정확한 AssetVersion, Project별 자동 `snapshot_version`, 최대 3회 concurrency retry, Snapshot+Item+Idempotency 단일 transaction을 구현했다.
- `composition_snapshot` version 1 HMAC Cursor와 `(snapshot_version DESC, composition_snapshot_id DESC)` keyset 조회를 추가했다. 6,000개 임시 SQLite Query Plan에서 기존 Unique Index 사용, TEMP B-TREE 0건과 중복·누락 0건을 확인해 Alembic·Index는 변경하지 않았다.
- Provider·Model Manifest를 제한된 문자열 mapping으로, Mix 설정을 깊이·항목 수가 제한된 JSON object로 검증하고 기존 `idempotency_records`를 Owner·Project scope로 재사용한다.
- 공식 Router와 CompositionSnapshot Endpoint 3개는 아직 미구현이며 Resource API 22/64, 실제 사용자 DB `20260809_0016`, metadata 36개 Table과 Runtime source of truth 14개를 유지한다.
- 계약과 검증은 [CompositionSnapshot 기반](docs/06-api/composition-snapshot-foundation.md)과 [검증 보고서](reports/validation/VALIDATION-COMPOSITION-SNAPSHOT-FOUNDATION.md)에 기록했다.

### 추가 — Artifact Resource API와 single-byte Range

- `GET /api/v1/artifacts/{artifact_id}`, `/content`, `/download`를 추가해 Artifact Metadata와 검증된 Payload를 inline 또는 attachment로 제공한다.
- Artifact Router는 effective Owner·retention·delivery allowlist·size·전체 SHA-256 검증을 `ArtifactApplicationService`에 위임하고 Repository·Resolver·filesystem에 직접 접근하지 않는다.
- `bytes=start-end`, `bytes=start-`, `bytes=-suffix` 단일 Range를 지원하고 multiple·invalid·unsatisfiable 요청은 `416 INVALID_RANGE`와 `Content-Range: bytes */<size>`로 거부한다.
- 공개 DTO와 오류에는 Catalog·storage key·URI·로컬 Path·checksum 기대값을 노출하지 않으며 공개 Artifact POST·PATCH·DELETE·Collection은 추가하지 않았다.
- Resource API 진행도는 22/64, Artifact API는 3/3이다. Alembic·Entity·실제 사용자 DB·Frontend·Provider·Runtime은 변경하지 않았다.
- 구현과 격리 검증 결과는 [Artifact Resource API 검증](reports/validation/VALIDATION-ARTIFACT-RESOURCE-API.md)에 기록했다.

### 추가 — Artifact 접근 제어와 reconciliation 기반

- `ArtifactApplicationService`를 추가해 `Artifact → AssetVersion → Asset.owner_id` 계보에서 effective Owner를 검증하고 cross-owner·누락·삭제된 상위 Asset은 `ARTIFACT_NOT_FOUND`로 숨긴다.
- `active|quarantined|expired|pending_delete|deleted` retention allowlist와 Metadata·Content Gate를 구현했다. Content는 `active`만 허용하고 같은 descriptor에서 실제 size와 전체 SHA-256을 검증한 뒤에만 내부 stream을 반환한다.
- `ArtifactReconciliationService`는 local Catalog를 UUID keyset batch로 읽고 승인 namespace만 symlink·reparse fail-closed scan하여 missing·unreferenced·size/checksum drift·invalid locator·pending 후보를 path 없이 보고한다.
- Reconciliation은 항상 dry-run이며 issue 결과를 상한 내에 보존한다. Artifact·Catalog·retention·Payload를 수정하거나 삭제하는 destructive repair는 구현하지 않았다.
- 실제 사용자 DB·실제 `DohaArtifacts`·Alembic·Entity·API·Frontend는 변경하지 않았고 Resource API 19/64와 Artifact API 0/3을 유지한다.
- 구현과 격리 검증 결과는 [Artifact 접근·reconciliation 검증](reports/validation/VALIDATION-ARTIFACT-ACCESS-RECONCILIATION.md)에 기록했다.

### 추가 — Artifact Trusted Ingestion

- 공개 REST가 아닌 내부 `ArtifactIngestionService`와 `LocalArtifactPublisher`를 추가해 승인 staging root의 Payload만 immutable Artifact로 등록한다.
- 실제 bytes를 streaming copy하며 SHA-256과 크기를 계산하고 WAV·FLAC·MP3, UTF-8 text와 bounded JSON을 kind별 fail-closed validator로 확인한다. caller checksum·MIME는 비교 hint로만 사용한다.
- Artifact ID 기반 canonical key를 생성하고 final filesystem의 exclusive 임시 inode를 `fsync`한 뒤 hard-link publish하여 기존 Payload를 덮어쓰지 않는다. 같은 checksum은 lineage가 다르면 별도 Artifact로 허용한다.
- Artifact와 `ArtifactStorageLocation`은 Service 소유 단일 transaction에 등록하고 Resolver round-trip으로 identity·size를 재검증한다. DB 실패 시 이번 실행이 만든 Payload만 보상 삭제하며 실패는 path 없는 orphan candidate로 보고한다.
- `DOHA_ARTIFACT_STAGING_ROOT`를 기본 미설정 fail-closed 변수로 추가했다. 실제 사용자 DB·실제 `DohaArtifacts`·Alembic·API는 변경하지 않았고 Artifact API는 0/3, Resource API는 19/64를 유지한다.
- 구현과 격리 검증 결과는 [Artifact Trusted Ingestion 검증](reports/validation/VALIDATION-ARTIFACT-TRUSTED-INGESTION.md)에 기록했다.

### 추가 — Artifact Storage Resolver

- `ArtifactStorageRepository`와 내부 `ArtifactStorageResolver`를 추가해 Artifact ID의 Catalog locator를 설정으로 주입한 `lm|audio|vocal|music` root 내부 regular file로만 해석한다.
- `DOHA_ARTIFACT_ROOT`는 기본 미설정이며 root 누락·비-directory·symlink/reparse 위험을 fail-closed한다. storage backend는 `local`, locator version은 `1`만 지원한다.
- canonical POSIX 상대 key, `music` namespace allowlist, 정확한 `Path.relative_to()` containment, symlink·junction·reparse 거부와 열린 descriptor의 identity·size·mtime 재검증을 구현했다.
- 실제 사용자 DB와 실제 `DohaArtifacts`에는 접근하지 않았고 Catalog row·Alembic·Route·Resource API는 변경하지 않았다. trusted ingestion, 전체 SHA-256·MIME 검증, orphan reconciliation, Range와 Artifact API 3개는 후속 작업이다.
- 구현과 격리 검증 결과는 [Artifact Storage Resolver 검증](reports/validation/VALIDATION-ARTIFACT-STORAGE-RESOLVER.md)에 기록했다.

### 변경 — Artifact Storage Catalog 실제 사용자 DB 적용

- 승인된 read-only Inventory, 정식 backup·restore, upgrade·downgrade rehearsal과 최종 writer·checksum·FK Gate를 거쳐 실제 사용자 SQLite DB를 `20260808_0015`에서 `20260809_0016`으로 승격했다.
- Application Table은 35개에서 Catalog를 포함한 36개로 늘었고 Runtime Table 14개와 기존 35개 Table schema·79개 row·canonical digest는 그대로 보존됐다. `artifact_storage_locations`는 0 row다.
- `quick_check`와 `integrity_check`는 `ok`, `foreign_key_check`는 0건, Runtime `foreign_keys`는 1이며 정식 backup과 manifest는 변경되지 않았다.
- Resolver·trusted ingestion·physical checksum 검증·Range·Artifact API 3개, backfill·dual write·Runtime 전환은 수행하지 않았다. Resource API는 19/64이고 Runtime Table 14개가 계속 source of truth다.
- 실제 적용 결과는 [Artifact Storage Catalog 0016 실제 적용 검증](reports/validation/VALIDATION-ARTIFACT-STORAGE-CATALOG-0016-APPLICATION.md)에 기록했다.

### 추가 — Artifact Storage Catalog 기반

- `ArtifactStorageLocation`과 내부 `artifact_storage_locations` Table을 추가해 Artifact ID와 backend·domain·canonical root-relative storage key를 1:1로 연결했다.
- Artifact Entity에는 경로 column을 추가하지 않고 FK `RESTRICT`, Artifact당 locator Unique와 `(storage_backend, storage_domain, storage_key)` Unique를 적용했다.
- 빈 backend·key, 승인되지 않은 `lm|audio|vocal|music` 외 domain과 1 미만 locator version을 DB Check Constraint로 거부한다. traversal·symlink·정규화 검증은 후속 Resolver 책임으로 유지한다.
- additive source revision `20260809_0016`은 Catalog Table 하나만 생성·제거하며 기존 35개 Table과 row count·digest·무결성을 임시 SQLite upgrade·downgrade에서 보존했다.
- 실제 사용자 DB는 접근하거나 Migration하지 않아 `20260808_0015`를 유지한다. Resolver·ingestion·실제 checksum 검증·Artifact API 3개와 Resource API 진행도 19/64도 변경하지 않았다.
- 검증 결과는 [Artifact Storage Catalog 검증](reports/validation/VALIDATION-ARTIFACT-STORAGE-CATALOG.md)에 기록했다.

### 문서 — Artifact Storage Resolver와 무결성 계약

- Artifact를 정확한 AssetVersion에 귀속된 불변 Payload 기록으로 고정하고 공개 Artifact API를 Metadata·content·download 3개로 유지했다.
- 경로 없는 Artifact와 물리 Payload 사이의 authoritative Catalog로 내부 `artifact_storage_locations` Table을 선택하고 `artifact://<artifact_id>`와 canonical domain-relative storage key 계약을 확정했다.
- trusted ingestion이 실제 bytes에서 SHA-256·크기·kind별 media type을 검증한 뒤 exclusive publish하고 Artifact·Catalog를 같은 DB transaction에 등록하도록 정의했다.
- owner 파생, retention 5개 상태, single byte range, 안전한 파일명, traversal·symlink·Windows reparse point·TOCTOU, 손상·누락 오류와 GC 경계를 문서화했다.
- 구현 전 별도 Catalog additive Migration이 필요하며 Artifact Router·Resolver·Entity·Alembic·실제 DB·파일·Runtime은 변경하지 않았다. Resource API는 19/64, Artifact API는 0/3을 유지한다.
- 검증 결과는 [Artifact Storage 계약 검증](reports/validation/VALIDATION-ARTIFACT-STORAGE-CONTRACT.md)에 기록했다.

### 추가 — AssetVersion Resource REST API

- `GET·POST /api/v1/assets/{asset_id}/versions`와 `GET /api/v1/assets/{asset_id}/versions/{asset_version_id}`를 추가해 최신 Version 우선 목록, 새 불변 Version 생성과 단건 Lineage 조회를 제공한다.
- 생성 요청은 `version_number`와 소유자·감사 필드를 받지 않는다. Service가 기존 최대 번호 다음 값을 할당하고 기존 Version을 덮어쓰거나 Selection·Artifact·Composition을 자동 변경하지 않는다.
- AssetVersion PATCH·DELETE Endpoint를 제공하지 않으며, 상위 Version은 같은 Asset에 속해야 한다. Asset·Version 부재와 번호 충돌은 `ASSET_VERSION_NOT_FOUND`·`ASSET_VERSION_CONFLICT`를 포함한 안전한 오류로 변환한다.
- Resource API 진행도는 19/64다. Alembic·Cursor·Index·실제 사용자 DB·Frontend·Runtime은 변경하지 않았고 Runtime Table 14개는 계속 source of truth다.
- 구현·회귀 검증 결과는 [AssetVersion Resource API 검증](reports/validation/VALIDATION-ASSETVERSION-API.md)에 기록했다.

### 추가 — Asset Resource REST API

- `GET·POST /api/v1/assets`와 `GET·PATCH·DELETE /api/v1/assets/{asset_id}`를 추가해 Owner scope 목록, 논리 Asset 생성, 상세 조회, 제한된 Metadata 수정과 Soft Delete를 제공한다.
- 목록은 선택적 `workspace_id`·`asset_type`, `(created_at DESC, asset_id DESC)` HMAC Cursor와 실제 사용자 DB에 적용된 revision `20260808_0015` Index를 사용하며 외부 offset과 `owner_id`를 노출하지 않는다.
- POST는 Asset만 생성하고 AssetVersion·Artifact·ProjectAsset을 자동 생성하지 않는다. DELETE도 AssetVersion·Artifact·ProjectAsset을 삭제하지 않는다.
- 공개 DTO와 입력에서 `owner_id`·`created_by` 및 내부 식별·삭제 필드를 제외하고 `ASSET_NOT_FOUND`, `ASSET_CONFLICT`, `WORKSPACE_BOOTSTRAP_REQUIRED`와 공통 Cursor·입력 오류 계약을 적용한다.
- Resource API 진행도는 16/64다. Runtime Table 14개는 계속 source of truth이며 실제 Bootstrap·backfill·dual write·Frontend는 수행하지 않았다.
- 구현·회귀 검증 결과는 [Asset Resource API 검증](reports/validation/VALIDATION-ASSET-API.md)에 기록했다.

### 추가 — Asset Cursor Pagination과 keyset Index 기반

- 향후 Asset 공개 목록을 신뢰된 현재 Owner의 활성 Asset으로 제한하고 `workspace_id=<uuid>`와 `asset_type`만 선택적 filter로 허용한다. `owner_id`, `include_deleted`, lifecycle filter와 자유 검색은 공개하지 않는다.
- 기존 version 1 created-at Cursor에 `resource=asset`을 추가하고 effective Owner·Workspace·Asset type·Soft Delete·정렬을 filter fingerprint로 결합했다. 기존 Workspace·Project·ProjectAsset token 의미는 변경하지 않았다.
- `AssetRepository.list_assets_after()`와 `AssetService.list_asset_page()`에 `(created_at DESC, asset_id DESC)` 및 `limit + 1` 기반 keyset page를 추가했다. 기존 offset 호환 메서드와 Asset Router는 변경하지 않았다.
- 6,000개 임시 SQLite fixture의 공식 첫·다음 page 8개 Query에서 full·partial 후보를 비교했다. partial 후보는 기존 `ix_assets_deleted_at`와 임시 정렬을 유지해 제외하고 Owner 및 Owner+Workspace full 복합 Index 두 개를 선택했다.
- Asset Index 전용 source revision `20260808_0015`를 추가해 임시 DB upgrade·downgrade, Table 35개, row digest, metadata·reflection과 무결성 보존을 검증했다. 실제 사용자 DB는 접근하지 않아 revision `20260807_0014`를 유지한다.
- Asset Resource API는 0/5, 전체 Resource API는 11/64를 유지한다. 검증 결과는 [Asset Cursor·Index 검증](reports/validation/VALIDATION-ASSET-CURSOR-INDEXES.md)에 기록했다.

### 추가 — ProjectAsset Resource REST API

- `GET·POST /api/v1/projects/{project_id}/assets`와 `DELETE /api/v1/projects/{project_id}/assets/{asset_id}`를 추가해 ProjectAsset 목록·기존 Asset 연결·관계 해제를 제공한다.
- 목록은 Project에 결합된 HMAC Cursor와 `display_order ASC, project_asset_id ASC` keyset 조회를 사용하고 외부 응답은 `asset_id`, `role`, `display_order`만 노출한다.
- 같은 `(project_id, asset_id)` 활성 관계는 `409 PROJECT_ASSET_CONFLICT`로 거부하고 Soft Delete 관계는 같은 식별 row를 복원한다. DELETE는 ProjectAsset만 Soft Delete하며 Asset·AssetVersion과 다른 Project 연결은 보존한다.
- 공개 입력의 내부 식별자·소유권·감사 필드를 거부하고 Project·Asset·ProjectAsset Not Found를 Resource별 안전한 오류 코드로 구분한다.
- Resource API 진행도는 11/64이며 다음 범위는 Asset Cursor·Index와 Resource API 5개다. 실제 Bootstrap·backfill·dual write·Frontend·Alembic revision과 실제 사용자 DB는 변경하지 않았다.
- 구현·회귀 검증 결과는 [ProjectAsset Resource API 검증](reports/validation/VALIDATION-PROJECT-ASSET-API.md)에 기록했다.

### 변경 — ProjectAsset keyset Index 실제 사용자 DB 적용

- 승인된 Inventory·backup·restore·migration rehearsal Gate를 거쳐 실제 사용자 SQLite DB를 `20260807_0013`에서 `20260807_0014`로 승격했다.
- `ix_project_assets_active_keyset`을 적용하고 첫 page·다음 page 조회에서 신규 Index 사용, TEMP B-TREE 없음, full scan 없음과 전체 application data digest 보존을 확인했다.
- Runtime Table 14개는 계속 source of truth이며 ProjectAsset Router·Resource Endpoint, Bootstrap·backfill·dual write·Frontend는 구현하거나 실행하지 않았다.
- 적용 결과는 [ProjectAsset keyset Index 실제 DB 적용 검증](reports/validation/VALIDATION-PROJECT-ASSET-KEYSET-INDEX-APPLICATION.md)에 기록했다.

### 추가 — ProjectAsset Cursor Pagination 기반

- ProjectAsset 목록에 `display_order ASC, project_asset_id ASC` 정렬과 Project ID에 결합된 HMAC Cursor payload를 추가하되 기존 Workspace·Project version 1 token 의미는 유지했다.
- `WorkspaceRepository.list_project_assets_after()`와 `WorkspaceService.list_project_asset_page()`에 offset 없는 `limit + 1` keyset 조회, `has_more`와 `next_cursor` 계약을 추가했다.
- 활성 ProjectAsset용 partial Index `ix_project_assets_active_keyset`과 Alembic source revision `20260807_0014`를 추가하고 full·partial 후보의 임시 SQLite Query Plan을 비교해 partial 후보를 채택했다.
- DohaMusic의 ProjectAsset identity는 REST 식별 경로와 기존 Unique Constraint·restore 정책에 맞춰 `(project_id, asset_id)`로 유지하며 `role`은 변경 가능한 관계 Metadata로 취급한다.
- ProjectAsset Router·Resource Endpoint, 실제 사용자 DB 접근과 0014 적용, Asset API·backfill·dual write·Frontend는 수행하지 않았다. Resource API 진행도는 8/64를 유지한다.

### 추가 — 첫 Workspace Resource REST API

- 기존 API Foundation과 `WorkspaceService`를 연결해 Workspace 3개, MusicProject 5개 등 `/api/v1` Resource Endpoint 8개를 추가했다.
- Workspace가 없는 단일 사용자 환경은 `409 WORKSPACE_BOOTSTRAP_REQUIRED`로 중단하고, `owner_id`와 `created_by`를 공개 입력에서 받지 않으며 Project 생성자는 Workspace 소유자에서 파생한다.
- 응답에서도 내부 소유권 식별자를 제외하고, 모든 Endpoint의 고유 operation ID·한국어 summary·tag와 FastAPI version 독립 Route 검증을 고정했다.
- 명시적 Bootstrap CLI가 현재 Alembic head `20260807_0013`만 허용하도록 이전 Migration preflight revision과 기대값을 분리했다.
- 목록은 기존 HMAC Cursor와 keyset 조회를 그대로 사용하고 Project 삭제는 연결 Asset 계보를 보존하는 Soft Delete와 빈 `204 No Content` 응답으로 처리한다.
- ProjectAsset·Asset·AssetVersion·Artifact·Composition·Job API, 인증·권한·Idempotency·Frontend·backfill·dual write는 구현하지 않았다.

### 추가 — Workspace keyset 복합 Index

- Workspace 전체 활성 목록, owner별 활성 목록과 MusicProject의 Workspace별 활성 목록을 위한 세 개의 복합 Index와 Alembic `20260807_0013` revision을 추가했다.
- 수백 건의 임시 SQLite fixture에서 여섯 가지 첫·다음 page 쿼리를 `EXPLAIN QUERY PLAN`으로 비교해 신규 Index 사용과 `USE TEMP B-TREE FOR ORDER BY` 제거를 검증했다.
- 기존 단일 Index를 유지하면서 Entity metadata와 Migration의 Index 이름·Column 순서를 일치시켰고, upgrade·downgrade에서 Table·row·기존 Index와 무결성이 보존되는지 검증했다.
- 실제 사용자 DB Migration·조회·backup, Resource Endpoint, Cursor payload, Repository·Service 계약, Frontend와 backfill·dual write는 수행하지 않았다.

### 추가 — HMAC Cursor Pagination 기반

- `DOHAMUSIC_CURSOR_SIGNING_KEY` 전용 비밀 설정과 canonical JSON·base64url·HMAC-SHA256 기반 opaque cursor codec을 추가했다.
- Cursor에는 version, Resource, 정렬, 마지막 `created_at`·UUID, filter fingerprint와 page limit만 포함하고 filter 원문·사용자 데이터·경로·credential은 포함하지 않는다.
- Workspace와 MusicProject에 `(created_at DESC, UUID DESC)` keyset 조회와 `limit + 1` 기반 `CursorPage` Service 결과를 추가해 `has_more=true`일 때만 서명된 `next_cursor`를 발급한다.
- Service와 서명된 payload의 `v`·`limit`을 정확한 정수로 제한하고 공개 placeholder key와 2 KiB를 초과하는 Cursor 입력을 안전하게 거부한다.
- 임시 SQLite에서 같은 생성 시각의 복수 row, 여러 page, Soft Delete 제외, Workspace filter 고정과 중복·누락 부재를 검증했다.
- Workspace·Project Resource Endpoint, 외부 offset, 실제 사용자 DB 접근, Alembic·Runtime·Frontend·Idempotency 변경은 수행하지 않았다.

### 추가 — Workspace API 공통 기반과 명시적 Bootstrap

- FastAPI `0.141.1`부터 중첩 Router가 최상위 `app.routes`에 펼쳐지지 않는 환경 차이를 반영해, Route 기준선 테스트가 내부 저장 형태가 아닌 정규화된 등록 Route와 OpenAPI 계약을 검증하도록 수정했다.
- 기존 `/api` payload를 변경하지 않고 빈 `/api/v1` Router 기반, 성공·Collection·오류 Pydantic v2 Schema와 v1 전용 오류 응답 분기를 추가했다.
- 검증된 `X-Request-ID`를 재사용하고 그 외 요청에는 opaque UUID를 생성해 `request.state`, 응답 payload와 header에서 연결하도록 했다.
- 명시적 SQLite URL과 필수 `owner_id`, revision `20260806_0012`, Workspace Table을 확인한 뒤에만 단일 사용자 기본 Workspace를 생성하는 `--apply` Bootstrap CLI를 추가했다. dry-run은 DB를 열거나 변경하지 않는다.
- Bootstrap 재실행은 같은 owner의 활성 Workspace를 반환하며 여러 활성 Workspace, 잘못된 revision과 누락 Table은 중단한다. 실제 사용자 DB에는 실행하지 않았다.
- 64개 Resource Endpoint, 일반 Idempotency-Key 저장·재생, Artifact Resolver, Job dispatch, Frontend, backfill·dual write는 구현하지 않았다.

### 추가 — Workspace Application Service와 transaction 경계

- Workspace·Asset·Composition·Job·Collaboration 5개 Application Service를 additive namespace에 추가하고 Service 메서드가 동기 SQLAlchemy transaction을 소유하도록 했다.
- 같은 Session을 공유하는 여러 Workspace Repository 변경은 성공 시 한 번 commit되고 예외 시 전체 rollback되며 Repository의 commit·rollback 금지 정책을 유지한다.
- Resource Not Found·Conflict·Validation·Invalid State application 오류를 FastAPI와 분리하고 Job 공통 상태 전이와 불변 Version·Snapshot 계약을 Service에서 검증한다.
- Soft Delete된 `ProjectAsset`, `Tag`, `Favorite`의 Unique row는 새 row를 만들지 않고 같은 식별 row를 복구한다. 감사 식별자와 계보는 보존한다.
- 범용 Unit of Work, REST API·Pydantic Schema·Frontend·Provider 호출·backfill·dual write·Runtime 전환은 구현하지 않았다.

### 추가 — Workspace Repository 계층

- 기존 Runtime Repository를 변경하지 않고 `backend.repositories.workspace` 아래에 Workspace·Asset·Composition·Job·Collaboration 5개 Aggregate Repository를 additive로 추가했다.
- 신규 Repository는 주입받은 동기 `Session`에서 `add`·`flush`·조회만 수행하며 `commit`·`rollback`은 향후 Service 또는 Unit of Work의 transaction 경계로 남겼다.
- SQLAlchemy 2.0 조회, 안정적인 정렬, 제한된 `limit`·`offset`, Soft Delete 기본 필터와 Entity·Constraint 계약을 임시 SQLite Repository 테스트로 검증했다.
- Workspace Entity와 실제 사용자 DB additive migration은 완료됐지만 신규 Table은 비어 있다. backfill·dual write·Service·REST API·Legacy 제거는 수행하지 않았고 기존 Runtime Table 14개가 계속 source of truth다.

### 수정 — SQLite Migration 안전 제어

- 앱 lifespan의 Alembic `upgrade head`를 `DOHAMUSIC_AUTO_MIGRATE=true`에서만 실행하도록 변경하고 기본값을 `false`로 고정했다.
- Runtime과 Alembic online SQLite 연결이 공통 helper로 연결마다 `PRAGMA foreign_keys=ON`을 적용하고 Alembic이 활성 상태를 확인하도록 했다.
- 임시·테스트 DB 경로만 명시적으로 자동 Migration을 사용하도록 분리하고 실제 사용자 DB Inventory·backup·Migration은 수행하지 않았다.
- 실제 사용자 DB Inventory, 검증된 backup과 최종 적용 승인은 계속 BLOCKER로 유지한다.

### 추가 — Workspace DB Migration 사전 점검

- 실제 사용자 DB 경로를 필수 인자로 받고 SQLite read-only URI로 Inventory·무결성·schema drift를 검사하는 preflight 도구를 추가했다.
- 별도 확인 인수가 있어야 SQLite backup API로 timestamp backup을 생성하고 checksum·revision·Table·integrity를 검증하도록 했다.
- 실제 적용 Runbook, Preflight 체크리스트, Backup·Rollback 정책과 fixture 검증 보고서를 추가했다.
- 임시 fixture에서 원본 checksum 불변, backup, upgrade 복사본 35개 Table과 backup 복원본 14개 Table을 검증했다.
- 실제 사용자 DB 접근·Migration, backfill·dual write·Legacy 제거와 Runtime FK 설정 변경은 수행하지 않았다.

### 추가 — Workspace Entity additive Migration

- 기존 Alembic head `20260801_0011` 다음에 목표 Workspace Table 21개만 생성하는 `20260806_0012` revision을 추가했다.
- 기존 Runtime Table 14개와 Column·Constraint·데이터를 변경하지 않고 신규 FK 39개, Index 109개, Check Constraint 8개와 Unique Constraint 17개를 Entity metadata와 일치시켰다.
- 임시 SQLite DB에서 upgrade 후 전체 35개 application Table과 downgrade 후 Legacy 14개 Table 보존을 검증했다.
- 실제 사용자 DB Migration, backfill, dual write, Legacy 제거와 Repository·Service·REST API 구현은 수행하지 않았다.

### 추가 — Workspace SQLAlchemy Entity 초기 구현

- 기존 14개 Runtime Entity를 교체하지 않고 `backend.models.workspace` namespace에 목표 21개 SQLAlchemy 2.0 Entity를 additive로 추가했다.
- 기존 `DeclarativeBase`를 재사용하고 UUID 생성 함수, 생성·수정 시각과 Soft Delete Mixin, `AssetType`·목표 `JobStatus` 문자열 Enum을 구현했다.
- 문서의 PK·FK·Unique·Index·Nullable, `ProjectAsset` N:M, AssetVersion·Snapshot 계보, JobInput·JobOutput, RecordingEnrollment·Approval 관계를 metadata에 반영했다.
- 모든 신규 Entity를 `backend.models`에서 명시적으로 등록하고 mapper 대칭·FK 해석·35개 전체 metadata Table의 in-memory SQLite `create_all`을 검증했다.
- Alembic Migration·실제 DB 변경·Repository·Service·REST API·Worker·Runtime·Frontend는 변경하지 않았다.

### 수정 — Frontend dependency 기준선 정합성

- `brace-expansion`을 `5.0.9`, `minimatch`를 `10.2.6`, `postcss`를 `8.5.25`로 제한해 기존 override와 lockfile의 취약 버전을 안전한 패치 버전으로 교체했다.
- 기존 기준선에서 이미 선택하던 `sharp 0.35.0`을 직접 production dependency로 명시하고 `@img/sharp-wasm32 0.35.0`을 선택 dependency로 선언해 npm 11의 orphan 설치와 `extraneous` 문제를 제거했다.
- Next.js·React·TypeScript 버전과 Frontend 기능은 변경하지 않았으며 `npm ci`, `npm ls`, audit 0건, lint, typecheck, 97개 Vitest와 production build를 통과했다.
- 원인과 취약점별 처리 근거는 [Frontend dependency 기준선 검증 보고서](reports/validation/VALIDATION-FRONTEND-DEPENDENCY-BASELINE.md)에 기록했다.

### 검증 — 코드 기준선 안정화 점검

- `main` 대비 `develop`의 512개 파일을 Backend·Frontend·AI Worker·Alembic·테스트·문서·설정·스크립트로 분류하고 전체 제품 기준선 승격 영향을 기록했다.
- FastAPI·기존 14개 Runtime Entity·Alembic 단일 head·SQLite metadata와 Backend 195개, Frontend 97개 테스트 및 Frontend lint·typecheck·build를 검증했다.
- Git 추적 파일의 비밀정보·절대 운영 경로·Dataset·모델·Checkpoint·미디어·대용량 파일 포함 여부와 PR #55의 Workspace Entity 격리를 확인했다.
- `git diff --check`를 막던 6개 문서의 trailing whitespace와 EOF 공백만 제거했으며 내용과 의미는 변경하지 않았다.
- Frontend dependency tree의 `npm ls` 오류와 npm audit의 high 2건·moderate 2건을 main 승격 BLOCKER로 기록하고 자동 dependency 변경은 수행하지 않았다.
- 상세 결과와 후속 Gate는 [코드 기준선 안정화 검토 보고서](reports/validation/VALIDATION-WORKSPACE-CODE-BASELINE.md)에 기록했다.

### 문서 — 최종 아키텍처 기준선 검토

- DohaStudio Common S…460 tokens truncated…der·테스트는 변경하지 않았으며 모든 목표 API는 `[계획]`으로 유지한다.

### 문서 — Asset 중심 데이터베이스 재설계

- 확정된 DohaStudio Common Specification에서 `Asset.project_id`를 제거하고 `Asset.workspace_id`·`ProjectAsset` N:M 계약으로 정렬한 결과를 반영했다.
- DohaStudio Common Specification을 기준으로 Workspace·MusicProject·Asset·AssetVersion·Artifact·Composition Snapshot·Job 중심의 21개 Entity와 21개 목표 Table을 설계했다.
- Pipeline이 결과를 소유하지 않고 AssetVersion이 불변 결과를 소유하도록 ERD, PK·FK·Unique·Index, Selection·Approval·삭제·Snapshot 정책을 정의했다.
- 현행 14개 Table에서 목표 구조로 전환하는 additive backfill, Dual Write, Shadow Read, Read 전환과 Legacy 제거 순서 및 검증 Gate를 문서화했다.
- SQL·ORM·Migration·API·DB 파일·Artifact 파일과 Runtime은 변경하지 않았으며 목표 구조는 `[제안]`으로 유지한다.

### 문서 — DohaMusic Workspace `music` Artifact 도메인

- Composition Snapshot의 권위 있는 관계 데이터는 DB가 소유하고 `snapshots` 폴더는 재현·직렬화·백업용 Artifact라는 경계를 명확히 했다.
- Provider Runtime의 `lm`·`audio`·`vocal` Artifact와 DohaMusic Workspace의 Mix·Export·Preview·Composition Snapshot·실행 기록을 분리했다.
- `D:/DohaArtifacts/music/{mixes,exports,previews,snapshots,runs}` 목표 구조와 Mix·Export의 DohaMusic 책임을 문서화했다.
- Snapshot이 최신 Asset이 아니라 특정 AssetVersion을 참조하도록 계획하고 현재 폴더·환경 변수·코드·DB는 변경하지 않음을 명시했다.

### 문서 — AI Provider 저장소 책임과 단계적 Runtime 분리

- DohaMusic을 제품 서비스와 Workspace·Job Orchestrator·결과 관리·Mixer·최종 Export 책임으로 유지하고 DohaLM, DohaAudio, DohaVocal의 Dataset·학습·평가·Runtime 책임 경계를 정의했다. DohaAudio·DohaVocal 저장소는 존재하며 Runtime 기능은 `[계획]`으로 구분했다.
- 기존 `PipelineExecutor`를 장기 제품 책임이 아닌 Legacy·Compatibility Workflow로 명시하고 확정된 DohaStudio 공통 Provider 계약 참조를 추가했다.
- 신규 Music Generator는 DohaAudio, 신규 Singing Voice·Voice Conversion은 DohaVocal에서 구현하고 기존 ACE-Step·Demucs·Seed-VC subprocess는 검증된 전환 전까지 호환 계층으로 유지하도록 정했다.
- Git 밖의 공통 Dataset·Artifact root 정책, Model Manifest 최소 계약과 Provider Job·Artifact ID·URI·API versioning·GPU admission control을 ADR-028과 단계적 Roadmap·DoD에 기록했다.
- 이번 변경은 문서만 수정하며 저장소 생성, Runtime 코드 이동, HTTP API, Artifact URI와 공통 Model Registry 구현을 포함하지 않는다.

### 문서 — DohaLM 가사 생성·분석 연동 계획

- DohaLM과 DohaMusic의 Provider·Reference Application 경계, REST/Streaming 우선 연동과 미완료 Python SDK·전용 Lyrics API의 검증 게이트를 문서화했다.
- AI 초안·수정 제안·사용자 편집·최종 승인 버전과 모델 사용·라이선스 계보를 분리하고 승인본만 음악 Pipeline에 전달하도록 계획했다.
- `AIHUB-71748` 계열을 `research_only`로 분류하고 상업 작업에는 `commercial_approved` 모델만 허용하는 fail-closed 정책을 추가했다.

### 수정 — Voice Enrollment WAV 정규화 오류 분류

- 60초를 넘는 PCM16 WAV가 정규화 출력 상한에서 `VOICE_SAMPLE_NORMALIZATION_FAILED` 500으로 오분류되던 문제를 `VOICE_SAMPLE_DURATION_TOO_LONG` 422로 수정했다.
- PCM24·float32·ADPCM·WAVE_FORMAT_EXTENSIBLE WAV를 `VOICE_SAMPLE_UNSUPPORTED_CODEC` 422로 분리하고 PCM 16-bit WAV 변환 안내를 Backend·OpenAPI·Frontend에 반영했다.
- PCM16 mono/stereo/16kHz resample, 미지원 codec, 손상·빈 WAV, 부분 출력 cleanup, 동일·신규 idempotency key 재시도와 실패 DB·Storage cleanup 회귀 테스트를 추가했다.

### 수정 — Voice Enrollment WAV 업로드

- Frontend의 Next.js rewrite proxy가 기본 10MB를 넘는 multipart body를 절단해 Backend 계약상 유효한 WAV 업로드를 500으로 반환하던 문제를 수정했다.
- Backend의 25MiB 파일 제한에 multipart metadata 여유를 더해 proxy request body limit를 26MiB로 설정하고 설정 회귀 테스트를 추가했다.
- Backend·공개 API·DB·migration·Storage·scheduler 계약과 F6 `[진행 중]` 상태는 변경하지 않았다.

### 변경 — Guided Voice Enrollment UI

- 정상 안내를 INFO 카드로 분리하고 실제 오류에만 경고 UI를 사용하도록 Voice Enrollment Wizard의 메시지 위계를 정리했다.
- 녹음 상태·입력 수준·안내 문장, Sample 품질·재생·대표 선택·삭제 카드, 우측 Summary와 완료 화면을 Doha Studio 시각 체계에 맞게 개선했다.
- 로딩·빈 상태·단계 표시와 키보드·스크린 리더 정보를 보강하고 Desktop Chrome, 820px Tablet, Pixel 7 Playwright 회귀 범위를 구성했다.
- Backend·API·DB·migration과 F6 `[진행 중]` 상태는 변경하지 않았다.

### 테스트 — Guided Voice Enrollment Validation

- 제품·API·DB·migration을 변경하지 않고 신규 사용자 PASS 등록, 합성 MediaRecorder preview/upload, 3개 품질 WARNING, 실패·timeout·FFmpeg 미설치, upload/submit 멱등, 만료·cancel·cleanup UX를 다루는 Validation E2E를 추가했다.
- 설치된 Chrome·Edge, Playwright Firefox와 Pixel 7·iPhone 14 에뮬레이션을 분리한 browser matrix와 실제 `MediaRecorder.isTypeSupported()`·Blob MIME probe를 추가했다.
- 실제 기기·실제 microphone 검증과 자동화 결과를 구분한 Validation Report, 운영 전 점검과 Phase 9 선행 항목을 분리한 수동 체크리스트를 추가했다.
- 자동 Validation은 완료했지만 실제 Android/iOS/Safari·Bluetooth microphone, 인증·소유권과 장기 운영 monitoring이 남아 F6는 `[진행 중]`을 유지한다.

### 추가 — Guided Voice Enrollment 운영 안정성

- FastAPI lifecycle에 AI Worker와 분리된 process-local scheduler를 등록해 시작 시 crash recovery를 1회 수행하고 만료·cleanup·orphan scan을 설정 주기로 실행한다.
- 마지막 성공 mutation 기준 24시간 sliding 및 생성 기준 7일 absolute 만료를 DB query로 처리하고, 조회 요청은 만료 시간을 연장하지 않도록 유지한다.
- `DELETE_PENDING`·`DELETE_FAILED`·중단된 `VALIDATING`·`SUBMITTING`·cleanup `RUNNING` 상태를 멱등 복구하고 부분 정규화 파일·누락 파일·중복 삭제를 안전하게 처리한다.
- DB를 source of truth로 Enrollment/Profile/Sample 소유권과 Storage 파일을 대조하며, server-generated 경로로 확정 가능한 orphan만 grace period 이후 자동 삭제하고 나머지는 경로 없이 경고한다.
- cleanup 성공·실패, retry, 만료, orphan, 복구 건수의 process-local metric snapshot과 민감 경로를 포함하지 않는 운영 로그를 추가했다.
- scheduler·만료·retry query·부분 삭제·재시작·중단 submit/normalize/cleanup·orphan·Storage 멱등 삭제 자동 test를 추가했다.
- Frontend·공개 API·migration은 변경하지 않았으며 F6는 인증·소유권과 실제 사용자 마이크 평가가 남아 `[진행 중]`을 유지한다.

### 수정 — Guided Voice Enrollment FFmpeg 정규화

- FFmpeg 임시 출력 파일의 확장자가 `.normalizing`이어도 PCM16 WAV가 생성되도록 출력 포맷을 명시해 실제 WebM/Ogg 정규화 실패를 수정했다.
- WebM/Ogg 최초 처리 시 FFmpeg 실행 파일의 존재와 `ffmpeg -version` 응답을 확인하고 검증된 경로를 process 수명 동안 재사용하도록 탐지를 강화했다.
- 합성 Opus WebM/Ogg의 PCM16 48kHz mono 실변환과 Unicode·공백 경로, truncated 입력, 미설치·timeout·비정상 종료·부분 출력 cleanup을 검증하는 자동 test를 추가했다.
- Ubuntu 전체 Backend와 Windows FFmpeg 집중 통합 test를 수행하는 GitHub Actions workflow를 추가하고 Windows Winget 설치·PATH/절대 경로·재시작·codec 확인·라이선스 검토 절차를 문서화했다.
- F6는 cleanup scheduler·인증·실기기 MIME/수동 녹음 평가가 남아 `[진행 중]`으로 유지하고 Phase 8 `15/15, 100%`는 변경하지 않았다.

### 추가 — Guided Voice Enrollment Frontend

- `/voice`에 안내·동의·방법 선택·녹음/업로드·품질 확인·대표 Sample 선택·검토·완료의 8단계 Guided Wizard를 추가했다.
- `MediaRecorder` MIME feature detection, 마이크 권한·일시정지·재개·60초 자동 종료, Web Audio 입력 수준, 메모리 preview와 stream·Object URL 정리를 구현했다.
- Enrollment create·조회, Sample upload·조회·삭제, submit·cancel API client와 DTO allowlist mapper, UUID 기반 create/upload/submit 멱등성 재시도를 추가했다.
- `sessionStorage`에는 Enrollment ID와 단계만 보존해 새로고침을 복원하고, 만료·not found·cleanup 실패·FFmpeg 미설치 오류에 대한 사용자 복구 흐름을 추가했다.
- 합성 WAV API mock을 사용한 Desktop·Mobile Playwright E2E와 MIME·DTO·품질·오류·session·Wizard unit/component test를 추가했다.

### 변경 — Guided Voice Enrollment Frontend 호환성

- 기존 단일 WAV Profile 등록은 `/voice`의 `빠른 WAV 등록` fallback으로 유지하고, 신규 Profile 생성 후 기존 목록과 Studio 선택 상태를 즉시 갱신한다.
- 모바일 action이 Sample 선택을 가리지 않도록 Wizard action을 콘텐츠 흐름에 배치하고 44px 이상 기존 공통 control을 재사용했다.
- F6는 Frontend 구현 완료를 반영하되 실제 WebM/Ogg FFmpeg 통합, 주기적 expiration·cleanup scheduler와 Phase 9 인증·소유권이 남아 `[진행 중]`으로 유지한다.

### 추가 — Guided Voice Enrollment Backend

- `VoiceEnrollment`·`VoiceSample` ORM, lifecycle 상태 전이와 최소 Repository CRUD·만료·cleanup 조회를 추가했다.
- `VoiceProfile.active_reference_sample_id`와 Profile 1:N Sample 관계를 추가하고, 대표 Sample의 소유 Profile·`PROMOTED` 상태를 Repository에서 검증한다.
- Alembic `20260801_0010`에서 기존 Profile을 파일 접근 없이 결정적 `LEGACY_REFERENCE` Sample로 backfill하고, 신규 Enrollment·비레거시 Sample이 있으면 데이터 유실성 downgrade를 차단한다.
- 기존 단일 Voice Profile 생성도 호환 Sample과 대표 reference를 함께 기록하며 기존 Voice API·Pipeline·Voice Conversion 공개 계약을 유지한다.
- Enrollment 생성·조회·Sample 업로드·조회·삭제·제출·취소 7개 API와 안전한 공개 DTO·오류 계약을 추가했다.
- WAV를 Python으로, WebM/Ogg Opus를 optional FFmpeg로 decode해 PCM16 48kHz mono WAV로 정규화하고 duration·metadata·peak·RMS·silence·clipping을 검증한다.
- UUID 기반 임시 Enrollment Storage, Profile Sample reference 승격과 원본·취소·삭제·lazy 만료 cleanup primitive를 추가했다.
- create·upload·submit에 hashed `Idempotency-Key`와 request fingerprint를 적용하고 Alembic `20260801_0011`에 `idempotency_records`와 versioned sample 품질 metrics를 추가했다.
- 신규 Enrollment API·normalizer·validator·Storage·migration과 기존 Voice Profile 호환 회귀 test를 추가했다.

### 변경 — Guided Voice Enrollment 호환성

- Enrollment submit으로 만든 대표 reference를 기존 `VoiceProfile.reference_file_path`에 연결해 기존 Pipeline·Voice Conversion이 새 Profile을 그대로 사용하도록 했다.
- 기존 단일 `/api/voice-profiles/upload` 경로는 유지하고 Profile 삭제가 Enrollment의 여러 retained reference도 정리하도록 확장했다.
- F6 상태를 Backend 구현 완료·Frontend와 scheduler 미구현인 `[진행 중]`으로 갱신하고 Phase 8 `15/15, 100%`는 유지했다.

### 문서 — Guided Voice Enrollment

- 브라우저 WAV·WebM/Ogg를 Backend에서 PCM16 48kHz mono WAV로 정규화하는 경계, Profile 1:N Sample과 명시적 대표 reference, 임시 Enrollment의 24시간 sliding/7일 absolute 만료·idempotency·cleanup을 ADR-024~026 `[제안]`으로 기록했다.
- 현재 단일 WAV API와 구분되는 `/api/voice-enrollments` endpoint·상태·안전한 오류·테스트 계약 및 현재 schema와 구분되는 VoiceEnrollment·VoiceSample ERD·backfill·transaction 설계를 추가했다.
- F6의 Backend 구현 순서와 Storage·동의 경계를 구체화했으며 Runtime 코드·migration·UI는 추가하지 않고 F6 `[계획]`, Phase 8 `15/15, 100%`, Phase 7 Dataset 분리를 유지했다.
- 현재 단일 WAV Voice Profile 계약을 기준으로 사용자 안내형 Voice Enrollment Wizard, 녹음 문장, 길이·업로드·품질·상태·오류·접근성·테스트 요구사항을 문서화했다.
- MediaRecorder WebM/Ogg와 WAV-only Backend 차이, 단일 reference와 다중 sample 모델, 사전 validation·임시 upload·cleanup·동의 철회를 ADR·Backend 선행 항목으로 분리했다.
- Frontend Roadmap에 F6 Guided Voice Enrollment `[계획]` Track을 추가하고 Phase 8 기존 `15/15, 100%`와 Phase 7 개인화 Dataset·학습 경계를 유지했다.
- 음성 동의 정책에 명시적 제출 전 서버 미전송, Web Storage·Analytics 음성 저장/전송 금지, 철회·삭제와 공개 운영 선행 조건을 보완했다.

### Frontend 기준선 복구

- Python packaging용 `lib/` ignore 규칙에 누락됐던 Frontend shared mapper와 Result metadata allowlist 모듈을 기존 import·테스트·공개 DTO 계약에 맞춰 복구했다.
- 공개 Audio URL만 same-origin Backend 경로로 변환하고 내부 경로·알 수 없는 metadata를 차단해 Frontend typecheck·build·test 기준선을 회복했다.

### Phase 6.5 비용 발생 방지

- 유료 외부 Lyrics 테스트에 사용자 승인·실행 opt-in·API Key의 3중 조건을 적용했다.
- 실제 실측 상태를 `[사용자 승인 필요] [API Key 필요] [유료 실측 미수행]`으로 통일했다.
- 실제 유료 API 호출 없음, 발생 비용 0원, API Key 사용 없음과 미측정 항목을 운영·실험·Roadmap 문서에 명시했다.

### K-POP Creation Control Layer

- Provider-neutral `HookAnalyzer`와 NumPy·SciPy 기반 기본 구현을 추가해 final WAV의 에너지와 반복 패턴에서 15초 후렴 후보와 `0.0~1.0` confidence를 추정한다.
- `result_metadata.audio_analysis.hook`에 version·status·candidate 구간·confidence·`energy_repetition`/`energy_peak`/`fallback_middle` strategy를 저장하고 내부 frame score·경로는 공개 DTO에서 제외한다.
- 신뢰도 `0.50` 미만은 곡 중앙 fallback으로 처리하며 무음·짧은·손상·미지원 WAV와 분석 예외가 Pipeline 성공 및 final WAV Result를 실패시키지 않도록 했다.
- Result는 후렴 후보의 추정 구간과 신뢰도를, History 목록은 후보 유무만, Project 상세은 요약 구간을 표시하며 Chorus 확정 표현을 사용하지 않는다.
- 반복·단일 에너지 피크·후보 없음·짧은 WAV·무음 fixture와 Pipeline·Retry·공개 allowlist·Frontend·Desktop/Mobile 회귀를 검증해 K3.3을 `[완료]`로 갱신했다.
- Preview Export·Lyrics Alignment·Voice Analysis·ML Hook Detector·DB Migration·Provider 변경은 구현하지 않았으며 K3.4 Preview Export를 다음 계획으로 유지한다.
- Provider-neutral `TempoAnalyzer`를 추가해 완료 Pipeline의 `final.wav`에서 예상 BPM과 `0.0~1.0` confidence를 추정하고 요청 BPM signed/absolute error와 half/double-time 후보를 기록한다.
- K3.1의 비차단 완료 경계를 유지한 채 `result_metadata.audio_analysis.tempo`만 확장하고, Retry는 새 Job의 새 WAV를 다시 분석하며 이전 Tempo 결과를 복사하지 않는다.
- 공개 DTO를 requested/detected BPM·confidence·error·candidate·status·version allowlist로 제한하고 Result 상세 Tempo 카드, History 상태, Project 요약과 구형·partial·failed fallback을 추가했다.
- 60·80·100·120·140·160 BPM 합성 fixture, 요청값 비편향, half/double·무음·짧은·손상 WAV, Pipeline·Retry·Frontend·Desktop/Mobile 회귀를 검증해 K3.2를 `[완료]`로 갱신했다.
- 새 의존성·DB Migration·Provider 변경 없이 기존 NumPy·SciPy를 사용했으며 K3.3 Hook/Chorus, K3.4 Preview, True Peak·LoRA·Dataset·Voice 학습은 구현하지 않았다.
- Provider-neutral `AudioQualityAnalyzer`와 SciPy WAV decode·NumPy Sample Peak/clipping·pyloudnorm BS.1770 Integrated LUFS 구현을 추가했다.
- `final.wav`와 Pipeline Result를 먼저 `COMPLETED`로 확정한 뒤 versioned `result_metadata.audio_analysis`를 비차단 갱신해 분석·저장 실패가 재생·다운로드를 막지 않도록 했다.
- Pipeline·History·Project 공개 DTO와 Frontend parser를 allowlist로 제한하고 Result 전체 품질 요약, History·Project 간결 상태, 구형·partial·failed fallback을 추가했다.
- mono/stereo·sine·silence·clipping·short·invalid fixture, LUFS reference, Pipeline 완료 경쟁·실패·Retry·DTO, Frontend·Desktop/Mobile E2E와 30/60초 성능을 검증해 K3.1을 `[완료]`로 갱신했다.
- DB Migration·Provider 변경 없이 K3.1에서는 K3.2 Tempo, K3.3 Hook/Chorus Candidate, K3.4 Preview와 True Peak를 구현하지 않았다.
- K3.0 Audio Analysis의 최종 `final.wav` 분석 source, 비차단 Pipeline 성공 경계, versioned Result metadata JSON, 공개 allowlist와 secure Preview 수명주기를 정의했다.
- Quality Metrics·Tempo·Hook Candidate·15초 Preview를 K3.1~K3.4로 분리하고 confidence·실패·Cancel·Retry/Re-analysis·fallback·단계별 DoD를 문서화했다.
- Audio Analysis 라이브러리·ITU-R BS.1770-5·EBU R 128 후보 비교, EVAL-008 검증 계획과 ADR-023을 추가했다. 코드·DTO·DB·의존성은 변경하지 않았고 K3 기능은 `[계획]`으로 유지했다.
- optional `generation_options`에 Preset·목표 BPM·언어 비율·Hook·Post-Chorus·Dance Break·보컬 에너지·Concept strict DTO와 사용자 친화 validation 오류를 추가했다.
- Backend 최종 `KPopPromptCompiler` 결과와 원본·정규화 옵션·compiler version을 기존 JSON Input Snapshot에 저장하고 Retry·History·Project·Result 공개 allowlist에 연결했다.
- Studio에 Preset별 기본 Structured Options, 고급 설정·초기화·즉시 Prompt Preview·Review·Desktop/Mobile History 요약을 추가했다.
- K-POP Lyrics Template이 언어 비율 목표, Hook 문구·방식·반복과 Post-Chorus 포함 여부를 Prompt 목표로 반영하도록 확장했다.
- K2를 `[완료]`로 갱신했으며 DB Migration·Provider 변경 없이 기존 Pipeline 요청과 구형 Snapshot Retry 호환성을 유지했다.
- 실제 BPM 검출·Hook timestamp·15초 Preview·Audio Analysis·LUFS·True Peak·LoRA·Dataset·Voice 학습은 구현하지 않았다.
- Dance·Easy Listening·Performance Preset Registry와 Provider-neutral `KPopPromptCompiler`를 추가하고 사용자 Prompt를 최우선으로 유지했다.
- Studio에서 기본 Dance Preset, Preset 설명과 Prompt Preview를 제공하고 컴파일 결과를 기존 Pipeline DTO의 `prompt`·`genre`로만 전송하도록 연결했다.
- K-POP Lyrics Template에 Intro·Verse·Pre-Chorus·Chorus·Post-Chorus·Bridge·Final Chorus 구조와 저작권·아티스트 모방 방지 규칙을 반영했다.
- K1 Preset MVP의 Backend·Frontend·Desktop·Mobile 회귀 검증을 완료해 `[완료]`로 갱신했다. `generation_options`, API·DB Migration, Provider 전용 제어, BPM·Hook Timestamp·Audio Analysis·LoRA·Dataset은 구현하지 않았다.
- Phase 8 이후 별도 K0~K4 제품 고도화 Track과 K-POP Dance·Easy Listening·Performance Preset 계약을 정의했다.
- Generation Options, `KPopPromptCompiler`, Lyrics Template, Provider Capability Matrix와 capability 기반 Frontend UX 경계를 문서화했다.
- EVAL-007 평가 계획, 권리 중심 Style Dataset 정책, ADR-022를 추가하고 현재 API·DB·Provider·Frontend에는 구현되지 않았음을 명시했다.

### Phase 2 Listening Evaluation

- Phase 2 사용자 청취 평가 점수와 근거를 EVAL-001에 반영하고, 동일 PCM 산출물의 중복 집계를 제거했으며 미평가 2건을 남긴 채 ACE-Step을 조건부 채택으로 기록했다.
- README·Master Roadmap·실행 Roadmap·ADR-006·Phase-02 DoD에서 조건부 채택, 기본 Provider `mock`, 운영 Provider 미확정, 사용자 평가 진행 중 상태를 동기화했다.
- Phase 2 대표 평가를 Korean Dance Pop으로 정하고 Instrumental·Korean Ballad를 보조 비교군으로 유지했으며, Dance Pop 평가 기준·Prompt·Hook 가사·0.6B LM 후속 실험 계획과 Phase 7 이후 LoRA·권리 확보 데이터 방향을 문서화했다.

### Phase 6.6~6.9 — Local Lyrics LLM

#### 문서

- 공개 Instruct Base Model과 권리 확보 Lyrics Dataset의 QLoRA SFT, LoRA Adapter·병합 모델 산출물, `LocalLyricsLLMAdapter` 목표 구조를 정의했다.
- Dataset Policy, Model Card template, ADR-016과 Dataset → Fine-tuning → Provider Integration → Quality Gate Roadmap을 추가했다.
- Base 미선정·Dataset 미구축·학습 미착수·checkpoint 없음·Adapter 미구현·평가 미실시·운영 미승인 상태를 명시했다.
- OpenAI API Experimental 비교군, FastAPI OpenAPI 명세, Planned Local Lyrics LLM을 구분하고 Frontend Provider-neutral 원칙을 보강했다.

### Phase 8 — Doha Studio Frontend MVP

- `POST /api/pipelines/{job_id}/cancel`과 `retry`를 추가하고 `CANCEL_REQUESTED` 단계 경계 cooperative 취소, 입력 Snapshot·원본 self FK 기반 새 Job Retry를 구현했다.
- 취소·재시도 상태와 가능 action을 Pipeline·History·Project 공개 DTO와 일반 사용자용 Generation·History UX에 연결했다.
- Alembic 0009, Backend·Frontend·Desktop/Mobile E2E와 ADR-021을 추가하고 로컬 단일 사용자 Phase 8을 `15/15, 100%`로 완료했다.

- 기술 중심의 화면 문구를 일반 사용자 중심의 한국어 창작 흐름으로 개편하고 장르·분위기·길이 선택, 단계별 도움말, 첫 방문 안내와 비활성 사유를 추가했다.
- `NEXT_PUBLIC_ENABLE_DEVELOPER_INFO` 플래그로 내부 연결·생성 방식 정보를 기본 화면에서 분리하고, 사용자 친화적 오류·로딩·빈 상태와 키보드 포커스 동작을 보강했다.
- 기존 API 계약과 Backend 동작, Phase 8 `14/15, 93%` 진행률은 변경하지 않았다.

- History 최신순·검색·상태·페이지네이션·상세 API와 Project CRUD·Default Project 자동 연결을 추가했다.
- Project 삭제 시 Pipeline Job·결과 파일을 보존하고 연결만 해제하는 migration과 ADR-020을 추가했다.
- `/history`, `/projects`, `/projects/[id]` 화면, Zustand Store, Result 재진입·Player·Download 연결을 추가하고 Phase 8을 `14/15, 93%`로 갱신했다.

- consent 필수 WAV multipart Voice Profile upload와 list/get API, 25MB·5~60초·16kHz·mono/stereo·16-bit PCM·signature/decode 검증을 추가했다.
- 업로드를 UUID 기반 안전 경로에 atomic 저장하고 실패 temp cleanup, 사용 중 삭제 차단과 관리 파일 삭제 정책을 구현했다.
- Voice 페이지와 Studio 단계에 Profile 등록·목록·warning·선택·삭제 UX를 연결하고 개발 경로 입력은 기본 비노출로 유지했다.
- Voice metadata migration과 ADR-019를 추가하고 Phase 8 Upload DoD 완료에 따라 진행률을 `11/15, 73%`로 갱신했다.

- 완료 Pipeline의 허용된 WAV에 경로 비노출 `GET|HEAD content`·`download` API와 단일 byte Range `206/416` 처리를 추가했다.
- Job/File 소속·완료 상태·Storage root·symlink·regular file·크기·MIME·확장자·RIFF header 검증 및 `no-store`·`nosniff` 응답 경계를 적용했다.
- 공개 files DTO의 capability URL을 전역 Player·seek·volume·Result 다운로드에 연결하고 unavailable·loading·오류 상태를 구현했다.
- Phase 8 Audio Player와 WAV Download DoD를 완료해 진행률을 `10/15, 67%`로 갱신하고 ADR-018에 로컬 단일 사용자 경계와 운영 승격 조건을 기록했다.

- 공개 Generation·Stem·Voice Conversion·Pipeline file DTO와 Voice Profile 응답에서 내부 `file_path`·`reference_file_path`를 제거하고 content·download 가능 여부만 명시하도록 보안 경계를 강화했다.
- Voice 서버 참조 경로 입력을 기본 비노출 개발 플래그로 제한하고 Backend에서 Storage root·파일 존재·확장자·traversal·절대 경로·symlink를 검증한다.
- API Client가 `INVALID_RESPONSE`, `REQUEST_TIMEOUT`, `REQUEST_ABORTED`, `NETWORK_ERROR`, HTTP·Backend 오류 코드를 구분하고 caller signal과 timeout signal을 보존하도록 개선했다.
- Pipeline polling에 연속 오류 5초·10초 backoff, 404·terminal 중단, hidden 최소 5초와 수동 재조회를 적용했다.
- Lyrics revision UI를 Backend capability로 제어하고, 결과 metadata allowlist·local Settings persist·Studio step 분리·역할별 CSS 구조를 적용했다.
- 취약 transitive dependency 수정 버전을 lockfile override로 고정해 `npm audit` 0건을 확인했다.

#### 추가

- Next.js 16 App Router·TypeScript 기반 `frontend/`와 npm lockfile, Premium Dark responsive Landing·Studio·Lyrics·Voice·Progress·Result·Settings·About·404 화면을 추가했다.
- Zustand session draft, TanStack Query server state, React Hook Form·Zod form, 공통 API client·안전한 오류 정규화·DTO mapper를 추가했다.
- Health·Lyrics 생성/검증/수정/삭제·Voice Profile 생성/삭제·Pipeline 생성/조회/files metadata를 실제 FastAPI 계약에 연결했다.
- 초기 5회 1초·foreground 2초·background 5초 polling과 terminal 중단, URL 복원, network/Job 실패 분리를 구현했다.
- Vitest·React Testing Library 12건과 Playwright Chromium Desktop·Mobile E2E 4건을 추가했다.

#### 변경

- ADR-017을 npm·Next.js 16·CSS token·Zustand·TanStack Query·React Hook Form·Zod·Lucide·Vitest·Playwright 조합으로 승인했다.
- Phase 8을 `[진행 중] 53%`로 갱신하고 F0~F3 완료, F4 부분 완료, F5 계획 상태로 구분했다.
- Voice upload/list/get, History·Project, cancel/retry, 인증·소유권·모델 목록·Playlist는 Backend API 전까지 disabled 또는 미구현 상태를 유지한다.

#### 검증

- Lint·Type Check·unit/component test·production build·Desktop/Mobile E2E를 통과했고 FastAPI와 same-origin proxy의 `/health` 응답을 확인했다.

### Phase 8 — Doha Studio Frontend Design

#### 문서

- 첨부된 Vinyl Music Dashboard를 기준으로 Premium Dark Music Studio의 Frontend Overview, Architecture, Design System, Atomic Component, Responsive, Studio UX, Navigation과 Page Structure를 설계했다.
- Desktop 3-column workspace, Tablet drawer, Mobile bottom navigation·step flow와 Player·Waveform·motion·접근성 기준을 정의했다.
- 현재 FastAPI endpoint별 page·request·response·loading·error·retry·polling 흐름과 upload/download·history·cancel/retry·인증 등 미구현 API gap을 구분했다.
- Phase 8 상태는 Frontend 코드 미구현에 따라 `[계획] 0%`로 유지하고 구현 순서를 Frontend Roadmap으로 정리했다.
- Pipeline 요청에 없는 `instrumental`을 Music Settings 활성 필드에서 제거하고 `planned/disabled`로 정정했다.
- F0 OpenAPI 계약 검토 대상·필드·응답·오류·DTO·완료 기준과 `Available`·`Partial`·`Backend Required`·`Planned` 지원 범위를 정의했다.
- Responsive Web과 Native/PWA 범위를 분리하고 디자인 레퍼런스 사용 정책과 `[검토 필요]` ADR-017 기술 스택 비교 초안을 추가했다.

### Phase 6.5 — External Lyrics LLM Provider

#### 추가

- OpenAI Responses API `gpt-5-mini-2025-08-07` Experimental Lyrics Adapter, strict JSON Schema mapper, Provider Factory와 opt-in paid integration test를 추가했다.
- `POST /api/lyrics/{id}/revise`, 원본 보존 parent/version·수정 지시·전후 SHA-256, Alembic 0006을 추가했다.
- retry·5초 deadline·안전한 오류 변환·명시적 Template fallback·token/예상 비용 metadata를 추가했다.

#### 변경

- 기본 Provider는 `template`로 유지하고 외부 Provider를 명시 선택했을 때만 API Key를 요구한다.
- `httpx`를 실제 Adapter runtime 의존성으로 이동했다.

#### 보안

- 외부 전송 필드를 가사 입력으로 제한하고 `store=false`, 비밀·ID·경로·음성 제외, 원문 Provider 오류 비노출 정책을 적용했다.

#### 문서

- Provider 공식 비교, 선정 정책, 데이터·운영 정책, ADR-015, EXP-008, EVAL-006과 API·DB·Architecture·DoD를 최신화했다. 외부 실측은 API Key 부재로 `[차단]`이다.

### 추가

- `LyricsGenerator` 인터페이스와 외부 통신 없는 `TemplateLyricsGenerator`, 테스트용 `MockLyricsGenerator`, `template`·`mock` Provider Factory를 추가했다.
- 한국어·영어 구조화 가사 생성, 섹션 파싱, 길이·반복·구조 검증, 생성·검증 metadata를 추가했다.
- 동기식 가사 생성·조회·검증·삭제 API, `lyrics_documents`, Alembic 0005를 추가했다.
- Lyrics benchmark, EXP-007, 사용자 EVAL-005, ADR-014와 Provider·API·검증·오류 회귀 테스트를 추가했다.

- `AudioMixer` 인터페이스, 실제 NumPy/SciPy 기반 `DefaultAudioMixer`, 유지되는 `MockAudioMixer`와 `default`·`mock` Provider Factory를 추가했다.
- gain, 48kHz Stereo 동기화, length padding, -1dBFS headroom, peak normalization, soft limiter, fade와 PCM16 WAV 출력을 추가했다.
- peak·RMS·headroom·clipping·처리 시간·CPU·RSS·출력 크기 metadata, Mixer benchmark, EXP-006, 사용자 EVAL-004와 ADR-013을 추가했다.
- gain·headroom·clipping·fade·metadata·format sync·Provider·Pipeline 연결 테스트를 추가했다.

- `PipelineService`, `PipelineContext`, `PipelineExecutor`, 5개 `PipelineStep`과 Mock Mixer·WAV Exporter를 추가했다.
- `pipeline_jobs`, `pipeline_files`, Alembic 0004와 비동기 Pipeline 생성·조회·파일 API를 추가했다.
- 단계별 진행률, 자동 재시도, timeout 판정, 구조화 오류, 부분 출력 정리와 JSON metadata를 추가했다.
- 재현 가능한 Mock benchmark 실행기, EXP-005, ADR-012와 성공·Music/Stem/Voice 실패·재시도·timeout 테스트를 추가했다.

- Seed-VC, OpenVoice, CosyVoice, Fish Speech, RVC, Amphion Vevo2의 공식 근거 비교표와 100점 Provider Score를 추가했다.
- Primary 미선정, RVC Secondary 평가 후보, Seed-VC·Vevo2 Experimental 결정을 기록한 ADR-011을 추가했다.

- Voice Provider 수명주기와 승격 조건을 정의한 ADR-010, Provider 정책, Voice Conversion 운영 준비도 QG-001을 추가했다.

- `VoiceConverter`, `MockVoiceConverter`, 격리형 `SeedVCAdapter`와 `mock`·`seed_vc` Provider Factory를 추가했다.
- 비동기 Voice Conversion API, `voice_conversion_jobs/files`, `VOICE_CONVERTING`, Alembic migration을 추가했다.
- Seed-VC 44k F0 runner, 3회 GPU Benchmark, opt-in GPU 통합 테스트와 48kHz stereo PCM16 자동 검증을 추가했다.
- EXP-004, 사용자 EVAL-003 양식, Seed-VC 검증 Provider 결정을 기록한 ADR-009를 추가했다.

- 프로젝트 전체 Phase·실제 진행률·선행 조건·산출물·다음 작업을 관리하는 `MASTER_ROADMAP.md`를 추가했다.
- Phase 1~9의 완료 판정과 공통 Git·문서 게이트를 관리하는 `docs/DoD/` 문서 체계를 추가했다.

- `StemSeparator` 인터페이스, `MockStemSeparator`, 격리형 `DemucsAdapter`, `mock`·`demucs` Provider Factory를 추가했다.
- 비동기 Stem 생성·조회·파일 조회 API와 `stem_jobs`, `stem_files`, `STEM_SEPARATING` 상태를 추가했다.
- HTDemucs 오프라인 단독 실행기, 3회 Benchmark, opt-in GPU Backend E2E 및 자동 오디오 검증을 추가했다.
- EXP-003, EVAL-002, Stem Provider·2-stem·48kHz Stereo float32 결정을 기록한 ADR-008을 추가했다.

- ACE-Step 동일·다른 Seed, 상주 반복, 0.6B LM을 명시 실행하는 benchmark suite와 결과 집계·WAV sample 비교 도구를 추가했다.
- 실제 음원을 사용자가 직접 평가하는 EVAL-001과 재현성·안정성·운영 결정을 기록한 EXP-002를 추가했다.
- ACE-Step 기본 Provider 채택 보류 ADR-006과 Job별 subprocess 유지 ADR-007을 추가했다.
- ACE-Step 1.5 v0.1.8을 격리된 런타임에서 실행하는 선택적 Adapter, Provider Factory, 오류 체계를 추가했다.
- 단독 instrumental·한국어 가사 smoke 실행기, 고정 benchmark 입력, WAV 신호 분석기와 opt-in GPU 통합 테스트를 추가했다.
- RTX 3060 Ti 8GB 실측과 Backend 종단 간 연결 결과를 기록한 `EXP-001` 보고서를 추가했다.
- FastAPI Router·Service·Repository 계층과 교체 가능한 의존성으로 Backend Foundation을 구축했다.
- SQLite·SQLAlchemy·Alembic 기반 `generation_jobs`, `generated_files`, `voice_profiles` schema를 추가했다.
- Mock `MusicGenerator`, ThreadPool Worker, 로컬 Storage와 생성·조회·음성 프로필 API를 추가했다.
- 생성 성공·조회·Mock Worker 실패·입력 예외·음성 동의·migration·Storage를 검증하는 테스트를 추가했다.

### 변경

- Phase 6을 로컬 Template·Mock 기반 완료로 갱신하고, 실제 LLM 도입과 Pipeline 자동 연결은 별도 검토로 유지했다.

- Pipeline Mixer 기본값을 Mock 복사에서 실제 `DefaultAudioMixer`로 교체하고 Mock AI 단계와 Orchestrator 구조는 유지했다.
- `numpy`, `scipy`, `psutil`을 Backend DSP·resampling·resource 측정 의존성으로 추가했다.

- 공유 단일 ThreadPool에 Pipeline Worker를 연결하고 애플리케이션 종료 시 SQLAlchemy Engine을 명시적으로 dispose하도록 변경했다.
- Phase 5를 Mock Voice 기반 기술 Orchestrator 완료로 갱신하되 Primary Voice와 실제 Mixer의 운영 게이트는 유지했다.

- Voice Provider Matrix를 `Primary 미선정 → Fallback 미선정 → Experimental → Mock`으로 정리하고 Experimental의 자동 fallback 참여를 금지했다.
- Phase 4를 Provider 평가 완료·Primary 미선정인 `[검증 필요]` 94%로 유지하고 Phase 5 착수를 계속 보류했다.

- Seed-VC를 `Experimental`·운영 보류로 확정하고 기본 Provider `mock`을 유지했다.
- Phase 4는 EVAL-003과 clipping·라이선스 해제 조건이 남아 `[검증 필요]` 94%로 유지하고 Phase 5 착수를 보류했다.

- 생성·Stem·Voice Worker가 동일한 GPU 동시성 1 executor를 공유하도록 확장했다.
- Phase 4를 기술 구현 완료·사용자 품질 평가 대기인 `[검증 필요]` 94%로 갱신했다.

- 새 기능 작업은 Master Roadmap, 해당 Phase DoD, AGENTS 지침 순으로 확인하고 완료 후 진행률·DoD·README·ROADMAP·CHANGELOG를 함께 갱신하도록 운영 규칙을 확장했다.

- AI 작업은 생성 Worker와 Stem Worker가 GPU 동시성 1인 공유 ThreadPool을 사용하도록 조립했다.
- 개발 상태를 Phase 3 Stem Separation 기술 검증 완료·사용자 청취 평가 대기로 갱신했다.

- 반복 실험 결과에 따라 현재 ACE-Step 운영 방식을 Job별 격리 subprocess로 확정하고 Mock 기본 Provider를 유지했다.
- 개발 단계를 Phase 2.5 기술 검증 완료·사용자 청취 평가 진행 중으로 갱신했다.
- `MusicGenerator` 결과 계약에 Provider·모델 버전·실제 Seed·추론 시간·최대 VRAM·메타데이터를 포함했다.
- Mock 전용 Worker를 Provider-neutral Worker로 확장하고 설정으로 `mock` 또는 `ace_step`을 선택하도록 변경했다.
- 개발 단계를 Phase 2 진행 중으로 갱신하고 기술 검증과 수동 청취 평가 상태를 분리했다.

### 수정

- 전체 Python 소스를 현재 Ruff 규칙에 맞게 정리하고, AI subprocess 경계의 의도적인 catch-all 예외 처리 사유를 명시했다.

### 제거

### 보안

- 가사 요청의 입력 개수·길이 상한, HTML·script·control 문자 제거, 구조화 오류 응답과 원문 전체를 남기지 않는 로그 정책을 적용했다.

- 신규 Voice Provider 검증 전에 checkpoint 출처·hash·역직렬화·원격 코드·의존성 lock과 학습 산출물 삭제 정책을 확인하도록 공급망 통제를 보강했다.

- Seed-VC 상용 SaaS와 Docker·온프레미스 외부 배포는 배포 단위별 GPL 준수 목록과 법률 검토 전까지 보류하도록 명시했다.

- Voice Conversion 입력을 DB의 vocals Stem과 명시적 동의 Voice Profile로 제한하고 참조 경로가 `voices/references` 밖으로 벗어나면 거부한다.

### 문서

- README, Master Roadmap, ROADMAP, DoD, Architecture, API, Database, Evaluation, Operations, Security 문서를 Phase 6 구현과 외부 LLM 보류 상태에 맞게 갱신했다.

- README, Master Roadmap, ROADMAP, Pipeline·Architecture·API·Evaluation·Operations·라이선스·Phase 5 DoD를 실제 Audio Mixer 기준으로 최신화했다.

- README, Master Roadmap, ROADMAP, Architecture, API, ERD, 상태, Evaluation, Operations, Security와 Phase 5 DoD를 실제 Pipeline 구현에 맞게 최신화했다.

- README, Master Roadmap, ROADMAP, Voice Model, Architecture, Operations, Security와 ADR 목록을 Phase 4.6 선정 결과에 맞게 최신화했다.

- EXP-004 기존 결과를 재실험 없이 재집계해 시간·VRAM·RMS·peak·파일 크기·hash와 clipping 원인·미확정 경계를 기록했다.
- EVAL-003의 사용자 평가표·체크리스트·기준을 보강하고 점수와 최종 청취 판정은 비워 두었다.
- README, Master Roadmap, ROADMAP, Model, Evaluation, Operations, Security와 ADR을 Phase 4.5 운영 품질 게이트 결정에 맞게 최신화했다.

- Seed-VC·OpenVoice·CosyVoice·Fish Speech의 공식 용도와 라이선스, archive 위험, RTX 3060 Ti 실측을 연구·모델·Architecture·API·DB·평가·운영 문서에 반영했다.

- README와 ROADMAP을 Master Roadmap·DoD에 연결하고 기존 Phase 4 이후 명칭을 Voice Conversion → Pipeline → Lyrics AI → Doha Voice → Doha Studio → Production 체계로 통합했다.

- Demucs·HTDemucs·MDX-Net·Open-Unmix 비교, Demucs 코드·가중치 MIT 확인, RTX 3060 Ti 실측을 조사·모델·Architecture·API·DB·평가·운영 문서에 반영했다.

- 동일 Seed PCM 재현성, 다른 Seed 파형 차이, 상주 CPU 메모리 증가, 0.6B LM 성능과 사용자 평가 상태를 관련 모델·아키텍처·운영 문서에 반영했다.
- ACE-Step 공식 출처·라이선스·격리 설치·저 VRAM 설정·성능·평가·오류·운영 문서와 ADR-005를 최신화했다.
- DohaMusic 초기 설계, 요구사항, 아키텍처, 데이터, API, 평가, 보안, 운영 문서 체계
- 단계별 계획과 실험 보고서 템플릿
- 저장소 전체에 적용되는 Codex Git 작업 지침과 문서 최신화·변경 이력 관리 규칙
- 장기 유지보수를 위한 구현 전 분석, 재사용, Adapter, 비동기 작업, 테스트·로그·성능 기록과 코드 품질 원칙
- README, Backend·Worker·Storage Architecture, API, ERD, 상태 모델과 로컬 운영 문서를 실제 Mock 구현에 맞게 갱신했다.
- ADR-002의 Adapter 경계와 ADR-003의 Phase 1 비동기 처리 결정을 구현 기준으로 검토·승인했다.
