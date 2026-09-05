# Frontend 아키텍처

> 문서 상태: [진행 중]
> 최종 수정일: 2026-09-03
> 관련 기능: Phase 8 Doha Studio, F6 Guided Voice Enrollment Frontend, D1 Composition Read [완료], D2 Timeline Playback [완료], D3 Clip Editing·Gain/Fade/Loop Integration [완료]
> 관련 문서: [Frontend Overview](frontend-overview.md), [AI-native DAW 전환 계획](../../planning/ai-native-daw-frontend-migration.md), [Clip Domain ADR](../11-decisions/ADR-040-canonical-track-clip-working-composition-authority.md), [Studio UX Flow](studio-ux-flow.md), [Voice Enrollment API](../06-api/voice-enrollment-api.md), [Frontend Roadmap](../../planning/frontend-roadmap.md), [ADR-017](../11-decisions/ADR-017-frontend-technology-stack.md)

## 아키텍처 목표

Next.js App Router 기반 `frontend/`에서 화면, 기능, 서버 상태, 전역 Player와 디자인 토큰을 분리한다. 현재 MVP는 FastAPI의 Health·Lyrics·Voice Profile·Voice Enrollment·Pipeline·Audio content/download 계약을 연결하며 Backend 계약은 [API 개요](../06-api/api-overview.md)를 사실 기준으로 사용한다.

현재 `/studio`는 생성 Workflow용 Responsive Studio이고 실제 DAW는 `/projects/{project_id}`의 Composition Timeline·WorkingComposition Editor다. Backend WorkingComposition aggregate가 canonical truth이며 selected Clip inspector는 Gain, Fade와 Loop absolute state를 편집한다. Loop toggle과 duration input은 normal endpoint를 사용하고 phase는 표시하거나 계산하지 않는다. Undo/Redo만 canonical before/after phase를 전용 restore endpoint로 왕복한다. Clip 폭과 Fade invariant는 `timeline_duration`을 따르며 Waveform source/cache와 WebAudio DSP는 Loop 변경의 영향을 받지 않는다. persistent history와 multi-user conflict recovery를 구현했으며 Section·Mixer는 아직 구현되지 않았다.

Onboarding은 `doha-studio-settings`의 persisted completion을 명시적으로 hydration한 뒤 dialog를 노출한다. local development에서 `127.0.0.1`로 접근할 때 Next dev assets가 hydration 전에 차단되지 않도록 해당 loopback origin만 `allowedDevOrigins`에 허용하며, 닫기와 Escape는 동일한 완료 action과 focus 복원을 사용한다.

WorkingComposition Clip Waveform은 `source_asset_version_id`를 Project-scoped media-source endpoint로 해석하고 safe same-origin Artifact content를 D2 WebAudio·bounded peak loader로 한 번 decode한다. editor session의 최대 64개 memory cache가 동일 exact AssetVersion resolve/fetch/decode를 공유하며 Project·WorkingComposition 변경과 unmount에서 pending 요청을 abort한다. 각 Clip은 frozen `source_duration` timebase에서 `[source_in, source_out)`만 투영하고 move·trim·split·same-ID restore·Undo/Redo·zoom은 canonical peak를 재사용한다. 이 시각화는 Track mixdown이나 playback authority가 아니다.

```mermaid
flowchart LR
  U[User] --> A[App Router Pages]
  A --> T[Page Templates]
  T --> F[Feature Modules]
  F --> Q[Server State / API Client]
  F --> S[Client Stores]
  Q --> B[FastAPI]
  S --> P[Player / Studio Draft / Settings]
  F --> D[Design System Components]
```

## 계층과 책임

| 계층 | 책임 | 금지 |
|---|---|---|
| `app` | route, layout, loading, error boundary, metadata | API 세부 로직과 거대 화면 컴포넌트 |
| `features` | Studio·Lyrics·Voice·Pipeline·Player 단위 사용자 행동 | 다른 feature 내부 구현 직접 참조 |
| `components` | 재사용 가능한 시각 구성 요소와 접근성 | Backend response 형식 직접 의존 |
| `services` | HTTP client, endpoint 함수, 오류 정규화, polling | UI 상태 저장 |
| `stores` | Client 전용 상태와 session 복원 | 서버 응답의 영구 source of truth 대체 |
| `hooks` | feature 조합, polling lifecycle, media query | 도메인 규칙 은닉 |
| `types` | API DTO, view model, discriminated union | 서버와 다른 상태값 발명 |
| `lib` | formatter, validation helper, constants | feature 전용 비즈니스 로직 |
| `styles` | token, base, layout, component, page, responsive motion/accessibility 정책 | 페이지별 임의 색상·spacing |

## CURRENT 구현 폴더 구조

```text
frontend/
├─ app/
│  ├─ page.tsx
│  ├─ studio/page.tsx
│  ├─ lyrics/page.tsx
│  ├─ voice/page
│  ├─ generation/[jobId]/page.tsx
│  ├─ result/[jobId]/page.tsx
│  ├─ history/page.tsx
│  ├─ projects/page.tsx
│  ├─ projects/[id]/page.tsx
│  ├─ settings/page
│  ├─ about/page
│  ├─ loading.tsx
│  ├─ error.tsx
│  └─ not-found.tsx
├─ components/
│  └─ app shell·brand·공통 UI components
├─ features/
│  ├─ studio/             # orchestration, step UI, schema, request mapper
│  ├─ lyrics/
│  ├─ voice/
│  ├─ pipeline/
│  ├─ player/
│  ├─ audio/
│  ├─ history/
│  ├─ projects/
│  ├─ composition/        # aggregate selection, read projection, Timeline Playback Foundation
│  └─ kpop/
├─ hooks/
├─ services/
├─ stores/
├─ lib/
├─ types/
└─ styles/                # tokens, base, pages, layout, components, responsive
```

실제 라이브러리와 버전은 승인된 [ADR-017](../11-decisions/ADR-017-frontend-technology-stack.md)과 `frontend/package-lock.json`으로 고정한다. 위 구조는 현재 checkout을 요약한다. TARGET DAW feature 폴더는 실제 구현 PR에서 책임 경계를 확정하기 전 미리 만들지 않는다.

## Design·반응형 구현 기준

현재 token과 component의 source of truth는 계획 문서가 아니라 `frontend/styles/tokens.css`, `base.css`, `layout.css`, `components.css`, `pages.css`, `responsive.css`와 실제 `frontend/components/`, `frontend/features/`다. breakpoint도 특정 기기 이름이 아니라 이 CSS와 회귀 테스트에서 검증된 content collision 지점을 따른다.

- Desktop·Tablet·Mobile은 같은 정보 우선순위를 sidebar, compact layout, mobile navigation과 단계형 화면으로 표현한다.
- interactive target, label, focus-visible, keyboard flow, `aria-live`와 reduced motion을 component 책임에 포함한다.
- loading·empty·error·offline/reconnecting과 비동기 Job 상태를 가짜 성공으로 대체하지 않는다.
- token이나 component 목록을 문서에 복제하지 않고 실제 code와 test를 변경의 기준으로 삼는다.
- 과거 Phase 8 Design System·navigation·page·responsive·component 초안은 `docs/archive/frontend/`에 보존한다.

## 상태 모델

| State | Source of truth | 주요 값 | 지속 범위 |
|---|---|---|---|
| Global | App shell | online, API health, active overlay | session |
| Studio | client draft | current workspace, settings, lyrics, voice profile ID, review validity | `sessionStorage` allowlist |
| Lyrics | API + draft | document, validation, revision, provider 표시 | API 결과 + 편집 draft |
| Pipeline | API | job ID, status, current step, progress, safe error, files metadata | URL로 복원 |
| Voice | API + draft | upload·list·get·delete Profile metadata, 선택 ID·이름, consent draft | API 결과 + `sessionStorage` 선택 allowlist; 음성 binary 저장 금지 |
| Player | client media | 단일 active source, play/pause, currentTime, duration, seek, loading/error, volume | session; Backend 저장 금지 |
| Settings | client | 현재는 reduced motion만 구현 | `localStorage` allowlist |

서버 상태는 client store에 복제해 진실처럼 사용하지 않는다. Job URL을 canonical identity로 사용하고 terminal state에서 polling을 중단한다. 새로고침 시 `GET /api/pipelines/{job}`으로 복원한다.

### Timeline playback authority

- AppShell의 `GlobalPlayer`만 `<audio>`를 소유한다. Timeline은 같은 `player-store`의 source·play/pause·currentTime·duration·seek를 사용하며 별도 audio element나 CSS playhead animation을 만들지 않는다.
- Composition source는 explicit selected Snapshot의 `mix` Item이 정확히 하나이고 safe `audio/*` Artifact도 정확히 하나일 때만 확정한다. first/latest Item·Artifact·AssetVersion fallback은 금지한다.
- duration은 `loadedmetadata`, Playhead는 media `timeupdate`를 따른다. 값이 없을 때 가상 3분 길이, BPM·meter·Bar/Beat를 합성하지 않는다.
- pointer 좌표는 viewport offset과 horizontal scroll, `pixelsPerSecond`를 반영하고 `0...duration`으로 clamp한다. Track 선택과 playhead time은 UI/session 상태이며 Backend에 저장하지 않는다.
- keyboard는 input·textarea·select·contenteditable 밖에서만 Space play/pause와 좌우 5초 seek를 처리한다.
- Waveform은 canonical source와 동일한 same-origin Artifact content만 fetch하며 metadata·response 크기를 128 MiB 이하로 검증한다. 최대 2,048개 peak, 각 channel의 각 peak bucket에서 sample array element 접근 최대 256회로 축약한 단일 SVG path만 상태에 보존하고 `AudioBuffer`는 즉시 해제한다.
- source 변경·unmount는 진행 요청을 abort하고 stale decode 결과를 폐기한다. decode 실패·크기 초과는 Waveform만 fail-closed하며 기존 Global Player 재생을 차단하거나 원본 URL·오류 세부를 노출하지 않는다.

### Working composition edit authority — [완료]

- canonical Track·Clip과 durable edit state는 [ADR-040](../11-decisions/ADR-040-canonical-track-clip-working-composition-authority.md)의 Backend WorkingComposition이 소유한다. `projection_id`, component UUID, React state와 Web Storage를 canonical ID/persistence로 사용하지 않는다.
- Frontend drag 중 preview, hover, selection과 Undo/Redo command stack만 memory state다. 성공한 move·trim·split·delete는 `expected_revision`과 함께 서버 mutation으로 반영하며 refresh 뒤 server state를 다시 읽는다.
- committed GlobalPlayer/Master Waveform과 working Clip preview/Waveform은 서로 다른 source·duration authority다. Working Preview는 revision-pinned render가 성공하고 사용자가 재생을 선택한 경우에만 working edit의 별도 결과로 표시하며 committed playback으로 위장하지 않는다.
- `dohaApi`의 20개 WorkingComposition Product operation DTO/client, Project Composition의 Track/Clip editor와 strict LIFO memory command stack을 구현했다. Clip Copy는 선택한 source와 화면에 명시된 target Track·timeline position만 전송하며 copied ID를 새 selection과 command에 기록한다. Undo는 copied ID delete, Redo는 같은 ID restore이고 Ctrl/Cmd+C/V clipboard shortcut은 도입하지 않았다. initialize·checkout·성공한 Composition Commit·Project 변경은 history boundary다. revision conflict는 stale intent를 자동 replay하지 않고 generation-guarded WorkingComposition/history refetch로 복구하며 canonical aggregate에 남은 selected Clip ID만 유지한다. Commit 실패는 stack을 보존하고 Commit 자체는 Undo/Redo command로 넣지 않는다.
- idempotent logical mutation은 operation별 새 key를 갖고 response loss retry에 같은 key를 유지한다. 성공 응답의 `completed_revision`을 반영한 뒤 GET으로 canonical aggregate를 reconcile하며 실패한 command는 stack을 이동시키지 않는다.
- Track/Clip Waveform은 exact media-source와 bounded editor-session cache를 통해 구현했다. Working Preview는 explicit POST, adaptive Workspace Job polling, exact `working_preview` Artifact output, stale/rerender와 기존 Global Player handoff를 사용한다. completion은 auto-play하지 않고 refresh 뒤 공식 latest read가 없으므로 session은 idle로 시작한다. Composition Commit action은 active Clip이 있을 때만 활성화하고 성공 시 working revision·Composition selection/history를 refetch하며 history를 비운다. 기존 Preview는 stale로 표시하고 Global Player를 자동 전환하지 않는다. Mixer는 구현하지 않았다.

## API 연결 원칙

```mermaid
sequenceDiagram
  participant UI as Studio UI
  participant API as FastAPI
  UI->>API: POST /api/pipelines
  API-->>UI: 202 PENDING + job id
  loop terminal state 전
    UI->>API: GET /api/pipelines/{job_id}
    API-->>UI: status/current_step/progress/error
  end
  UI->>API: GET /api/pipelines/{job_id}/files
  API-->>UI: file metadata
```

- Lyrics 생성·검증은 동기 요청이다. `POST /api/lyrics`, `POST /api/lyrics/validate`의 loading과 validation 결과를 구분한다.
- Pipeline은 `202` 이후 정상 응답 초기 5회 1초, foreground 2초, background 최소 5초 간격으로 polling한다. 연속 오류 1~2회는 5초, 3회 이상은 10초이며 성공 시 오류 횟수를 초기화한다.
- `COMPLETED`, `FAILED`, `CANCELLED`, 404에서 polling을 중단한다. `CANCEL_REQUESTED`는 취소 확정까지 제한적으로 polling한다. Timeout·network 오류는 Job 실패와 분리하고 같은 Job ID 수동 재조회를 제공하며 자동 재제출하지 않는다.
- API Client는 caller signal과 10초 timeout을 `AbortSignal.any()`로 결합한다. Node 24·현재 TypeScript DOM 계약과 Chromium E2E에서 지원을 확인했으며 외부 취소는 `REQUEST_ABORTED`, timeout은 `REQUEST_TIMEOUT`으로 구분한다.
- 성공 HTTP의 JSON parsing 실패는 실제 status를 가진 `INVALID_RESPONSE`다. API의 `{ error: { code, message } }`는 Backend code를 보존하고 그 밖의 HTTP·network 오류와 구분한다.
- Files public DTO와 Voice Profile response에는 내부 물리·상대 Storage 경로가 없다. Result UI는 실제 공개 필드 allowlist만 표시하며 알 수 없는 nested metadata를 숨긴다.
- 실패·취소 Retry는 서버 입력 Snapshot으로 새 Job을 생성한다. mutation 중에는 버튼을 잠그고 성공한 새 Job ID의 Generation route로 이동한다.

## 현재 API 제약

| UX | 현재 가능 여부 | 설계 처리 |
|---|---|---|
| Health 표시 | 가능 | `GET /health` |
| Lyrics 생성·조회·수정·검증·삭제 | 가능 | 현재 계약 사용 |
| Pipeline 생성·상태·파일 metadata | 가능 | Studio 핵심 흐름 |
| 음성 프로필 upload·조회·삭제 | 가능 | 기본 UI는 WAV 등록·목록 선택; 서버 경로 생성과 UUID 직접 입력은 개발 플래그 전용 |
| 오디오 재생·다운로드 | 가능 | 완료 Pipeline의 capability URL만 사용; 내부 경로 금지 |
| 프로젝트·생성 이력 목록 | 가능 | History 검색·상태·페이지네이션, Project CRUD·상세, Result 재진입 |
| Job 취소·수동 retry | 가능 | cooperative Cancel과 새 Job Retry |
| 인증·소유권 | 불가 | 공개 운영 차단 조건 |
| 모델 목록 | 불가 | `Backend Required`; UI에서 하드코딩된 선택 기능 금지 |

상태 분류의 단일 기준은 [Frontend Overview의 지원 범위](frontend-overview.md#frontend-지원-범위)다. 이 표의 “가능”은 `Available`, “부분 가능”은 `Partial`, “불가”는 `Backend Required`를 뜻한다.

## 페이지별 API 상태 설계

| 화면 | Request | 주요 Response | Loading | Error·Retry | Polling |
|---|---|---|---|---|---|
| Landing·Settings | `GET /health` | service health | 작은 status indicator | 수동 reconnect, Studio 정적 shell 유지 | 주기 polling 없이 진입·수동 확인 |
| Studio / Lyrics | `POST /api/lyrics`, `POST /api/lyrics/validate` | Lyrics document 또는 validation | editor action 단위 busy | field/schema 오류 수정 후 명시 재요청 | 없음, 동기 API |
| Lyrics Lab detail | `GET /api/lyrics/{id}` | stored Lyrics document | editor skeleton | 404와 network 분리 | 없음 |
| Lyrics revision | `POST /api/lyrics/{id}/revise` | 새 parent/version document | revision action busy | 미지원 Provider·차단·network별 안내 | 없음 |
| Lyrics delete | `DELETE /api/lyrics/{id}` | `204` | confirm action busy | 자식 revision·404 안내 | 없음 |
| Voice | upload·list·get·delete | 공개 Profile metadata 또는 `204` | upload/list 단위 busy | consent·파일·quality·삭제 오류 | 선택 ID를 session draft에 저장 |
| Studio Review | `POST /api/pipelines` | `202`, `PENDING`, job ID | 중복 제출 차단 | 입력 오류 수정; network 결과 불명확 시 조회 가능한 ID 없으면 자동 재제출 금지 | 생성 후 Progress로 이동 |
| Generation Progress | `GET /api/pipelines/{jobId}` | status, step, progress, safe error, metadata | 최초 skeleton, 이후 non-blocking refresh | network reconnect; `FAILED`는 새 Job만 가능 | terminal 전 adaptive polling |
| Result | Pipeline 조회·files·content·download | completed Job, 안전한 audio capability | artwork/metric skeleton | 파일별 불가 상태 표시 | 완료 후 중단 |
| 독립 Generation·Stem·Voice 화면 후보 | 각 `POST`·Job `GET`·files `GET` | 해당 Job과 file metadata | Pipeline과 동일 | safe error code 기반 새 요청 | terminal 전 polling |

Audio Metadata는 별도 endpoint가 아니라 Pipeline의 `result_metadata`와 files의 공개 metadata에서 읽는다. UI는 duration·execution time, Provider 식별자와 Mixer의 실제 audio quality 필드만 allowlist로 변환한다. 파일·모델 경로, 명령, 환경, 내부 host, stack trace와 알 수 없는 key는 표시하지 않는다. Response DTO는 API 문서와 OpenAPI에서 검증하고 UI 전용 view model로 변환한다.

OpenAPI는 FastAPI request·response 명세이고 OpenAI API는 Experimental 외부 Lyrics Provider다. Local Lyrics LLM은 공개 Base를 자체 권리 Dataset으로 파인튜닝하는 Planned Backend Provider다. Frontend API client는 이 세 용어를 구분하고 Provider SDK를 포함하지 않는다.

## F6 Voice Enrollment 책임 경계 [Frontend 완료]

F6는 `app/voice/page.tsx`와 기존 `features/voice/voice-profile.tsx`를 유지하면서 다음 책임으로 확장했다.

```text
app/voice
- route entry, loading, error boundary

features/voice
- voice-enrollment-wizard: 단계·server mutation·복원·submit orchestration
- use-voice-recorder: permission, MediaRecorder, 입력 수준, Blob·stream cleanup
- voice-enrollment-types/utils: 공개 DTO allowlist·상태·품질·오류·session mapper
- voice-profile: 기존 빠른 WAV fallback과 Profile 목록·선택·삭제

services
- 기존 Voice Profile API와 Enrollment create/get/sample get·upload·delete/submit/cancel

hooks
- microphone permission, MediaRecorder lifecycle, page-leave protection

lib
- duration formatting, client-safe audio metadata, feature detection

types
- 실제 API DTO, UI view model, UI workflow state union 분리
```

컴포넌트 후보는 `VoiceEnrollmentPage`, `VoiceEnrollmentStepper`, `VoiceConsentStep`, `VoiceMethodSelector`, `VoicePromptCard`, `VoiceRecorder`, `AudioLevelMeter`, `VoiceFileUploader`, `VoiceSampleList`, `VoiceDurationSummary`, `VoiceQualityResult`, `VoiceProfileForm`, `VoiceEnrollmentComplete`다.

- `app`은 route·loading·error boundary만 담당하고 MediaRecorder·upload orchestration을 갖지 않는다.
- `features/voice`는 녹음·파일·validation·제출 흐름을 조합하고 snake_case 공개 DTO를 명시적 mapper로 allowlist한 뒤 UI 상태와 분리한다.
- `services`에는 실제 OpenAPI의 7개 Enrollment endpoint만 추가했다. 서버 상태는 Backend enum과 일치하며 `RECORDING`, `PAUSED`, `PREVIEW`는 UI 전용이다.
- microphone stream, recording Blob과 Object URL은 메모리 lifecycle로 관리하고 `localStorage`·`sessionStorage`에 저장하지 않는다.
- `sessionStorage`에는 Studio 선택용 opaque Profile ID·이름과 Enrollment ID·현재 단계 allowlist만 유지한다. 음성 binary, idempotency raw key와 내부 path는 저장하지 않는다.
- [ADR-024](../11-decisions/ADR-024-browser-voice-recording-server-normalization.md)에 따라 `audio/wav → audio/webm;codecs=opus → audio/webm → audio/ogg;codecs=opus → audio/ogg`를 feature detection하고 Backend PCM16 48kHz mono 정규화에 전달한다. Windows/CI에서 FFmpeg WebM/Ogg 경로를 검증했으며 FFmpeg 미탐지 환경에서는 해당 오류를 WAV fallback으로 안내한다.
- sample upload 뒤 받은 server-generated Enrollment·Sample ID와 공개 metadata만 복원 후보로 두고, final submit 전 Profile 생성 완료로 표시하지 않는다. audio binary·내부 path는 Web Storage에 넣지 않는다.
- 접근성·상태·오류·테스트 기준은 [Voice Enrollment 요구사항](../02-requirements/voice-enrollment-requirements.md)을 따른다.

## 접근성·성능·보안

- 키보드만으로 Navigation, Dialog, Wizard step, Player 제어가 가능해야 한다.
- 모든 interactive target은 최소 44×44px, focus ring은 accent와 구별되는 밝은 outline을 사용한다.
- reduced motion은 사용자 `localStorage` 명시 설정, system `prefers-reduced-motion`, 기본값 순으로 결정하며 턴테이블 회전·parallax·waveform animation을 정지한다.
- artwork와 waveform은 layout shift를 막는 고정 aspect ratio를 사용한다.
- 입력 draft에 비밀이나 원본 음성 binary를 임의 저장하지 않는다.
- Backend 인증 전에는 개인 데이터가 있는 Production 공개 배포를 금지한다.
