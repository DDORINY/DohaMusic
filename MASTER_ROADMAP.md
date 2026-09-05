# DohaMusic 마스터 로드맵

> Provider 결과 경계 업데이트(2026-08-26): DohaVocal `0.2.0` payload consumer, 전용 PayloadLocator persistence와 verified durable local staging foundation을 구현했습니다. Worker/Completion/Artifact ingestion은 아직 미구현입니다.
>
> 문서 역할: 장기 Product Phase·독립 Track·완료 Gate의 최상위 기준
> 문서 상태: [운영 기준]
> 최종 수정일: 2026-09-03
> 현재 실행 순서: [ROADMAP](ROADMAP.md)
> 완료 판정: [Phase DoD](docs/DoD/README.md)

## Phase 6.5 External Lyrics LLM 상태

| 항목 | 상태 | 근거 |
|---|---|---|
| Phase 6 Lyrics AI | [완료] | 기존 Template·Mock 기반 DoD 유지 |
| OpenAI Lyrics Adapter | [Experimental] | strict Schema·Factory·오류·retry·fallback·Revision 자동 검증 완료 |
| DohaLM Lyrics Provider | [계획] `0%` | 일반 Chat REST/SSE MVP만 확인, Python SDK·전용 Lyrics API·manifest·DohaMusic Adapter 미완료 |
| 외부 Provider 실제 실측 | [사용자 승인 필요] [API Key 필요] [유료 실측 미수행] | 실제 유료 API 호출 없음, 발생 비용 0원, API Key 사용 없음 |
| External Provider 품질 승인 | [사용자 평가 필요] | EVAL-006 미작성 |
| Stable 승격·Pipeline 연결 | [보류] | 품질·비용·지연·데이터·법률·인증 게이트 미통과 |

Phase 6 완료 상태는 변경하지 않는다. 기본 Provider는 `template`이며 Phase 6 자체, OpenAI External Provider의 Experimental 상태, DohaLM Reference Integration의 Planned 상태와 Local Lyrics LLM의 Planned 상태를 분리한다. DohaLM은 별도 저장소의 모델·추론 Provider이고 현재 `AIHUB-71748` 계열은 비상업 연구 범위이므로 상업 Pipeline에서 사용할 수 없다.

## Phase 6.6~6.9 Local Lyrics LLM 상태

| Phase | 상태 | 사실 기준 |
|---|---|---|
| 6.6 Local Lyrics Dataset | [계획] `0%` | Dataset·manifest 미구축 |
| 6.7 Local Lyrics LLM Fine-tuning | [계획] `0%` | Base 미선정, QLoRA SFT 미착수, checkpoint 없음 |
| 6.8 Local Lyrics Provider Integration | [계획] `0%` | `LocalLyricsLLMAdapter`·runtime 미구현 |
| 6.9 Local Lyrics Quality Gate | [계획] `0%` | 자동·사용자 평가 미실시, 운영 미승인 |

전체 LLM 사전학습은 범위 밖이다. 공개 Instruct Base 후보를 라이선스·상업 이용·파생 모델 조건과 RTX 3060 Ti 8GB 실행성으로 검토하고 권리 확보 Lyrics Dataset으로 QLoRA SFT를 우선 검토한다. 세부 기준은 [ADR-016](docs/11-decisions/ADR-016-local-lyrics-llm-finetuning.md)과 [Roadmap](planning/local-lyrics-llm-roadmap.md)을 따른다.

DohaLM 저장소 분리 결정 이후 Dataset·Fine-tuning·Evaluation·Runtime과 Model Manifest의 기술 구현은 DohaLM이 소유한다. DohaMusic은 `LyricsGenerator` 호환 Provider Client, 편집·버전·최종 승인과 Pipeline 입력 경계를 유지한다.

## AI Provider 저장소 분리 상태

| 단계 | 상태 | 사실 기준 |
|---|---|---|
| Phase A Boundary Definition | [완료] | 책임·계약·Dataset·Artifact·Manifest·ADR 문서화와 PR #50 병합 완료 |
| Phase B New Implementation Separation | [진행 중] | DohaVocal `0.1.0` 호환·`0.2.0` payload consumer DTO/trust/transient acquisition·Trusted Payload resolver, durable locator persistence와 verified local staging foundation 구현; reclaim·concrete wiring·downloader·Completion·실제 ingestion/model 미구현 |
| Phase C Runtime Migration | [계획] | ACE-Step·Demucs·Seed-VC 이전과 Artifact URI 미착수 |
| Phase D Legacy Removal | [계획] | 내부 Runner·구형 Adapter 유지 |

DohaMusic은 제품 서비스와 Workspace·Job Orchestrator·Mixer·최종 Export를 소유한다. 기존 `PipelineExecutor`는 Legacy·Compatibility Workflow다. DohaVocal `0.1.0` 호환과 `0.2.0` payload consumer DTO·trust gate·transient acquisition, DohaMusic-owned Trusted Payload process-local adapter, durable PayloadLocator persistence 및 verified durable local staging foundation을 구현했다. `payloadref:v1` identity, exact replay, lifecycle CAS, revocation과 restart recovery가 CURRENT지만 atomic reclaim·concrete Worker wiring·인증·downloader·Completion adapter·Artifact payload ingestion·실제 Vocal model은 `[미구현]`이다.

## AI-native DAW 제품 전환 상태

| 범위 | 상태 | 사실 기준 |
|---|---|---|
| Responsive Studio MVP | [완료] | 생성·가사·음성·History·Project·Result·Settings와 Player·Cancel·Retry 구현; `/studio` 생성, `/projects` Project 관리, `/projects?mode=daw` DAW Project 선택과 `/projects/{id}` 편집 역할 구분 |
| AI-native DAW Product Direction | [완료] | PR #94가 `develop`에 병합되어 CURRENT/TARGET/NOT IMPLEMENTED 기준 확정 |
| Composition Runtime UI | [진행 중] | D1·D2와 D3 WorkingComposition·Working Preview·Composition Commit·Clip Gain/Fade/Loop Backend/Frontend integration, Track/Clip editing·explicit Clip Copy·persistent Undo/Redo, exact AssetVersion-safe media source·Track/Clip Waveform 완료; multi-user conflict recovery 완료; Section·Mixer 미구현 |
| Composition Evaluation / QA | [계획] | 통합 QA Run·Report·deep-link·Re-Evaluation 미구현 |
| Continuous Learning Hub | [계획] | Candidate review·Rights/Eligibility/Dataset 연결 미구현 |

Phase 8 완료는 로컬 Responsive Studio MVP 범위다. 장기 제품은 [AI-native DAW 제품 방향](docs/02-product/ai-native-daw-product-direction.md)과 [Frontend 전환 계획](planning/ai-native-daw-frontend-migration.md)의 독립 Track으로 관리하며 기존 Phase 8 진행률을 소급 변경하지 않는다.

Studio↔DAW navigation 정리는 기존 두 Track을 합치지 않는다. `/studio`는 생성 workflow, `/projects`는 일반 Project 관리, `/projects?mode=daw`는 DAW intent를 보존한 Project 선택이며 `/projects/{project_id}`는 Backend-authoritative Composition state를 소비하는 DAW다. 이 UX 정리로 D3 상태나 완료율을 변경하지 않는다.

> 목적: DohaMusic 전체 Phase, 실제 진행 상태, 완료 기준과 다음 작업을 한곳에서 관리한다.
> 관련 문서: [Phase DoD](docs/DoD/README.md), [실행 로드맵](ROADMAP.md), [작업 지침](AGENTS.md), [ADR](docs/11-decisions/README.md), [변경 이력](CHANGELOG.md)

이 문서는 프로젝트의 최상위 진행 기준이다. 기능 작업을 시작할 때 `MASTER_ROADMAP → 해당 Phase DoD → AGENTS.md` 순서로 범위와 완료 조건을 확인한다. 작업을 마치면 이 문서의 상태·진행률, 해당 DoD, README, ROADMAP과 CHANGELOG를 실제 구현에 맞게 갱신한다.

## 상태와 진행률 원칙

- 상태는 `[완료]`, `[진행 중]`, `[계획]`, `[검토 필요]`, `[보류]`만 사용한다.
- 진행률은 해당 Phase DoD의 완료 항목 수를 전체 판정 항목 수로 나눈 값이다. 증거가 없는 항목은 완료로 계산하지 않는다.
- 막대는 10칸 단위 근사 표시이며 숫자 백분율이 정확한 값이다.
- 문서 생성만으로 기능·품질·운영 준비가 완료된 것으로 보지 않는다.
- 사용자 청취 평가가 비어 있으면 관련 품질 게이트를 완료 처리하지 않는다.

## 전체 흐름과 현재 상태

```text
Phase 0  프로젝트 문서화             [완료]
  ↓
Phase 1  Legacy Backend Foundation   [완료]
  ↓
Phase 2  Music Generation            [진행 중]
  ↓
Phase 2.5 Quality Benchmark           [진행 중]
  ↓
Phase 3  Stem Separation             [완료]
  ↓
Phase 4  Voice Conversion            [검증 필요]
  ↓
Phase 5  Pipeline Integration        [완료]
  ↓
Phase 6  Lyrics AI                   [완료]
  ↓
Phase 6.6~6.9 Local Lyrics LLM       [계획]
  ↓
Phase 7  Doha Voice                  [계획]
  ↓
Phase 8  Doha Studio                 [완료]
  ↓ 후속 개선
F6       Guided Voice Enrollment     [진행 중]
  ↓ 독립 장기 전환
Track    AI-native DAW Product       [D0·D1·D2·D3 Backend/Frontend Clip editing·safe media source·Track/Clip Waveform·Preview·Commit 완료 / 잔여 D3 gate / D4~D9 계획]
  ↓
K0~K4   K-POP Creation Control      [K0·K1·K2·K3.0·K3.1·K3.2·K3.3 완료 / K3.4~K4 계획]
  ↓ 병행
Track    Workspace Artifact·Job      [진행 중]
  ↓ 병행
Phase 9  Production                  [계획]
  ↓ 병행
Track    AI Provider 저장소 분리     [Phase A 완료 / Phase B 진행 중 / Phase C~D 계획]
```

| Phase | 상태 | 진행률 | 사실 기준 | DoD |
|---|---|---:|---|---|
| 0. 프로젝트 문서화 | [완료] | `██████████ 100%` | 초기 문서·정책·ADR 체계 구축 | 이 문서의 Phase 0 기준 |
| 1. Legacy Backend Foundation | [완료] | `██████████ 100%` | FastAPI·SQLite·Alembic·Mock Runtime Job 검증; Workspace Job과 분리 | [Phase-01](docs/DoD/Phase-01.md) |
| 2. Music Generation | [진행 중] | `█████████░ 93%` | ACE-Step 조건부 채택, 기본 `mock`, 운영 Provider 미확정·사용자 평가 진행 중 | [Phase-02](docs/DoD/Phase-02.md) |
| 2.5 Quality Benchmark | [진행 중] | `█████████░ 93%` | 재현성·반복·VRAM·ADR 완료, EVAL-001 사용자 평가 진행 중 | [Phase-02.5](docs/DoD/Phase-02.5.md) |
| 3. Stem Separation | [완료] | `██████████ 100%` | HTDemucs Adapter·API·Benchmark·평가표 구축 | [Phase-03](docs/DoD/Phase-03.md) |
| 4. Voice Conversion | [검증 필요] | `█████████░ 94%` | 6개 Provider 평가 완료, Primary 미선정 | [Phase-04](docs/DoD/Phase-04.md) |
| 5. Pipeline Integration | [완료] | `██████████ 100%` | Mock Voice 기반 Orchestrator·API·실패 정책 검증 | [Phase-05](docs/DoD/Phase-05.md) |
| 6. Lyrics AI | [완료] | `██████████ 100%` | 로컬 Template·Mock Generator·API·검증·Benchmark 완료 | [Phase-06](docs/DoD/Phase-06.md) |
| 6.6~6.9 Local Lyrics LLM | [계획] | `░░░░░░░░░░ 0%` | Dataset·학습·Adapter·품질 게이트 미착수 | [Roadmap](planning/local-lyrics-llm-roadmap.md) |
| 7. Doha Voice | [계획] | `░░░░░░░░░░ 0%` | Dataset·LoRA·Fine Tuning 미착수 | [Phase-07](docs/DoD/Phase-07.md) |
| 8. Doha Studio | [완료] | `██████████ 100%` | 로컬 단일 사용자 Responsive Studio MVP의 Voice·History·Project·Audio·Cancel·Retry 완료; DAW TARGET과 분리 | [Phase-08](docs/DoD/Phase-08.md) |
| F6. Guided Voice Enrollment | [진행 중] | 독립 체크리스트 | 구현·자동 Browser Validation 완료, 실제 사용자 마이크·실기기와 인증은 미검증 | [Validation Report](reports/validation/VALIDATION-VOICE-ENROLLMENT.md) |
| AI-native DAW Product | [진행 중] | `D0·D1·D2·D3 WorkingComposition·Working Preview·Composition Commit·Clip Gain/Fade/Loop Backend/Frontend integration·Clip editing·Clip Copy·safe media source·Track/Clip Waveform·persistent history 완료` | Backend journal/cursor Undo/Redo·exact version media resolution·Clip source-window Waveform·revision-pinned Preview·immutable Commit·static Clip Gain/Fade와 Loop UI 완료; multi-user conflict recovery 완료; 실제 DB 적용·Section·Mixer·QA·Learning 미구현 | [AI-native DAW DoD](docs/DoD/AI-Native-DAW.md) |
| K0~K4. K-POP Creation Control | [진행 중] | `K0·K1·K2·K3.0·K3.1·K3.2·K3.3 완료 / K3.4~K4 계획` | Structured Options와 final WAV Quality Metrics·LUFS·Tempo·Hook 후보 후처리 완료 | [K-POP Roadmap](planning/kpop-creation-roadmap.md) |
| Workspace Artifact·Job Domain | [진행 중] | 독립 체크리스트 | Job Service·Completion UoW·Worker 실행 기반·공식 API 5/5, 4개 Vocal Job 계약, Provider Job 1:N persistence와 metadata Result trust gate 구현, reconciliation 계약 확정; Provider dispatch wiring·durable payload ingestion·background daemon 미구현 | [Workspace Job Foundation](docs/03-architecture/workspace-job-foundation.md) |
| 9. Production | [계획] | `░░░░░░░░░░ 0%` | 운영 인프라·보안 승인 미착수 | [Phase-09](docs/DoD/Phase-09.md) |
| AI Provider 저장소 분리 | [진행 중] | 독립 체크리스트 | Phase A 완료; DohaVocal Consumer Contract·HTTP Transport·metadata Result trust gate 구현, Worker wiring·실제 Artifact payload 통합 미구현 | [DohaVocal Consumer Contract](docs/03-architecture/dohavocal-consumer-contract.md) |
| Reviewer Authentication Authority | [Foundation 구현 / OS adapter 미구현] | 독립 authority | V1 local-only·single owner/operator·Windows WebAuthn platform credential mechanism과 fail-closed contract 구현; production adapter·assertion·mapping·authority 활성화 미구현 | [ADR-042](docs/11-decisions/ADR-042-v1-local-operator-authentication-foundation.md) |

K-POP Track은 기존 Phase에 흡수하지 않는 제품 고도화 Track이다. K0·K1·K2·K3.0, K3.1 Audio Quality Metrics, K3.2 Tempo Analysis와 K3.3 Hook Candidate를 완료했다. Preview는 K3.4, 모델 적응은 K4 계획으로 유지한다. Phase 8 완료를 취소하지 않으며 Phase 9 운영 준비와 병행할 수 있다.

AI-native DAW Product Track도 기존 Phase 8 완료를 취소하지 않는다. D0 문서 기준은 PR #94 병합으로 완료됐고, D1은 [Composition Read 계약](docs/06-api/composition-read-workspace.md)과 [ADR-035](docs/11-decisions/ADR-035-d1-composition-read-authority.md), D3 선행 설계는 [ADR-040](docs/11-decisions/ADR-040-canonical-track-clip-working-composition-authority.md)에서 확정했다. Clip Editing Runtime부터 D9 운영 전환까지는 구현·테스트·계약·ADR Gate를 각각 통과해야 한다.

Workspace Artifact·Job Domain은 진행 중 Track이다. Job Cursor·Service·Completion UoW와 Worker foundation에 공식 Job API 5개를 연결했다. Provider Job identity는 1:N binding history로 복구하고 PayloadLocator는 ordered payload lifecycle을 별도 보존한다. D1-A product API 2개, D3 Clip Persistence·Authority, Revision-safe Idempotency, PayloadLocator, Working Preview와 Clip Gain·Fade·Loop·persistent history Backend Foundation을 구현해 source metadata는 50개 Table·Alembic `20260905_0028`이다. 실제 사용자 DB는 36개 Table·`20260810_0017`로 유지한다. Provider dispatch wiring과 background daemon·scheduler, 실제 DB 전환은 미구현이다.

## Phase 0. 프로젝트 문서화 — [완료]

- 목표: 프로젝트 목표·범위·정책·설계·작업 규칙을 추적 가능한 문서로 확립한다.
- 구현 범위·포함 기능: README, ROADMAP, AGENTS, 요구사항, Architecture, API·DB·평가·보안·운영 문서, ADR·실험 보고서 구조.
- 제외 기능: 실제 Backend와 AI 모델 실행.
- 선행 조건: 저장소와 프로젝트 목표 확정.
- 완료 조건: 문서 구조·상대 링크·상태·작업 규칙 검토 완료.
- 산출물: `README.md`, `AGENTS.md`, `docs/`, `planning/`, `reports/`.
- 관련 문서: [AI-native DAW 제품 방향](docs/02-product/ai-native-daw-product-direction.md), [문서 Authority Map](docs/DOCUMENT_AUTHORITY_MAP.md).
- 관련 ADR·실험: [ADR-001~004](docs/11-decisions/README.md), 실험 없음.
- 예상 다음 단계: Phase 1 Backend Foundation.

## Phase 1. Legacy Backend Foundation — [완료]

- 목표: AI Provider가 없어도 검증 가능한 비동기 Backend 기반을 만든다.
- 구현 범위·포함 기능: FastAPI 계층, SQLite·SQLAlchemy·Alembic, 생성 Job·파일·음성 프로필, Mock Worker, Storage, 로그·예외·테스트.
- 범위 구분: 이 완료 판정은 Legacy Runtime Job 기준이다. Workspace Job Backend Foundation은 [별도 공식 계약](docs/03-architecture/workspace-job-foundation.md)의 Migration·Cursor·claim/lease·completion·API Gate를 모두 통과한 뒤 완료로 판정한다.
- 제외 기능: 실제 AI, 인증, 외부 Queue, Frontend.
- 선행 조건: Phase 0 문서·아키텍처 기준.
- 완료 조건: [Phase-01 DoD](docs/DoD/Phase-01.md)의 모든 항목과 Mock E2E 통과.
- 산출물: `backend/`, 초기 migration, API·DB 문서.
- 관련 문서: [Backend Architecture](docs/03-architecture/backend-architecture.md), [API](docs/06-api/api-overview.md), [ERD](docs/07-database/erd.md).
- 관련 ADR·실험: [ADR-002](docs/11-decisions/ADR-002-modular-ai-pipeline.md), [ADR-003](docs/11-decisions/ADR-003-async-job-processing.md), 별도 AI 실험 없음.
- 예상 다음 단계: Phase 2 Music Generation.

## Phase 2. Music Generation — [진행 중]

- 목표: 교체 가능한 음악 생성 Adapter와 로컬 생성 경로를 검증하고, 한국 여성 댄스팝 생성 후 Voice Conversion으로 사용자 자신의 목소리를 적용하는 제품 목표에 맞춰 대표 품질 시나리오를 확립한다.
- 구현 범위·포함 기능: ACE-Step 공식 조사·격리 runtime·`MusicGenerator` Adapter·Provider Factory·Backend E2E·WAV·성능 metadata.
- 제외 기능: ACE-Step 운영·제품 기본 Provider 확정, 사용자 품질 최종 승인, Lyrics AI, 음색 변환.
- 선행 조건: Phase 1 Backend와 RTX 3060 Ti 8GB 환경.
- 완료 조건: [Phase-02 DoD](docs/DoD/Phase-02.md)와 EVAL-001의 남은 사용자 평가 후 운영·제품 기본 Provider 승인 여부 결정.
- 산출물: ACE-Step Adapter·runner·GPU 통합 테스트·EXP-001.
- 관련 문서: [Music Generation Adapter](docs/04-models/music-generation-adapter.md), [Model Comparison](docs/01-research/model-comparison.md).
- 관련 ADR: [ADR-005](docs/11-decisions/ADR-005-ai-worker-dependency-isolation.md), [ADR-006](docs/11-decisions/ADR-006-ace-step-primary-provider.md).
- 관련 실험: [EXP-001](reports/experiments/EXP-001-ace-step-local-inference.md), [EVAL-001](reports/evaluations/EVAL-001-ace-step-listening-evaluation.md).
- 예상 다음 단계: EVAL-001의 남은 2개 산출물 평가와 Phase 2 운영·제품 기본 Provider 판정.

평가 시나리오는 Instrumental·Korean Ballad를 보조 비교군으로 유지하고 Korean Dance Pop을 대표 시나리오로 사용한다. 현재 Phase 2는 Base Model 평가가 목적이며 Dance 스타일 LoRA와 권리 확보 Style Fine-tuning 데이터는 Phase 7 이후 별도 검토한다.

## Phase 2.5. Quality Benchmark — [진행 중]

- 목표: 음악 생성의 재현성·다양성·반복 안정성·자원 사용과 운영 수명을 판정한다.
- 구현 범위·포함 기능: 동일·다른 Seed, 반복 실행, 0.6B LM 비교, VRAM·RSS·시간, WAV 비교, runtime 수명 ADR.
- 제외 기능: Codex의 청감 점수 작성, 모델 기본값 강제 변경.
- 선행 조건: Phase 2 ACE-Step 단독 추론 성공.
- 완료 조건: [Phase-02.5 DoD](docs/DoD/Phase-02.5.md)와 사용자 EVAL-001 완료.
- 산출물: benchmark suite·집계 도구·EXP-002·EVAL-001 양식.
- 관련 문서: [평가 전략](docs/08-evaluation/evaluation-strategy.md), [Benchmark](docs/08-evaluation/benchmark-scenarios.md).
- 관련 ADR: [ADR-006](docs/11-decisions/ADR-006-ace-step-primary-provider.md), [ADR-007](docs/11-decisions/ADR-007-ace-step-runtime-lifecycle.md).
- 관련 실험: [EXP-002](reports/experiments/EXP-002-ace-step-quality-and-stability.md), [EVAL-001](reports/evaluations/EVAL-001-ace-step-listening-evaluation.md).
- 예상 다음 단계: 남은 사용자 청취 판정 반영 후 Phase 2·2.5 종료 여부 결정. ACE-Step은 현재 조건부 채택이며 기본 Provider는 `mock`이다.

## Phase 3. Stem Separation — [완료]

- 목표: 생성 음원을 `vocals.wav`와 `instrumental.wav`로 분리하는 교체 가능한 경로를 만든다.
- 구현 범위·포함 기능: HTDemucs 선정, `StemSeparator`, Mock·Demucs Provider, Stem API·DB·Worker, 48kHz Stereo 출력, Benchmark·자동 검사.
- 제외 기능: Seed-VC, 음색 변환, 믹싱.
- 선행 조건: Phase 1 Backend와 생성 파일 metadata.
- 완료 조건: [Phase-03 DoD](docs/DoD/Phase-03.md)의 기술 범위와 EVAL-002 평가 양식 구축.
- 산출물: Stem Adapter·API·migration·runner·EXP-003·EVAL-002.
- 관련 문서: [Stem Adapter](docs/04-models/source-separation-adapter.md), [Stem API](docs/06-api/stem-api.md).
- 관련 ADR: [ADR-008](docs/11-decisions/ADR-008-stem-separation-provider.md).
- 관련 실험: [EXP-003](reports/experiments/EXP-003-stem-separation.md), [EVAL-002](reports/evaluations/EVAL-002-stem-separation-listening-evaluation.md).
- 예상 다음 단계: Phase 4 Seed-VC 후보 조사와 EVAL-002 사용자 판정.

## Phase 4. Voice Conversion — [검증 필요]

- 목표: 동의된 본인 음성으로 분리 보컬의 음색을 변환한다.
- 구현 범위·포함 기능: Seed-VC 공식 조사, `VoiceConverter` Adapter·Mock·Provider, Voice Profile 연결, API·Worker·Benchmark·EXP·EVAL.
- 제외 기능: 무단 Voice Clone, Dataset 학습, 전체 Pipeline 믹싱.
- 선행 조건: EVAL-002 검토, 음성 동의·삭제·보안 정책 재검토.
- 완료 조건: [Phase-04 DoD](docs/DoD/Phase-04.md), 공식 라이선스·RTX 3060 Ti 실행·품질·실패 경로 검증.
- 산출물: Voice Conversion Adapter, API/DB migration, ADR-009~011, EXP-004, EVAL-003 사용자 양식, QG-001, Provider 비교·점수.
- 관련 문서: [Voice Conversion 조사](docs/01-research/voice-conversion.md), [Provider 비교](docs/01-research/voice-provider-comparison.md), [Voice Adapter](docs/04-models/voice-conversion-adapter.md), [Provider Score](docs/04-models/voice-provider-score.md), [Provider 정책](docs/04-models/voice-provider-selection-policy.md).
- 관련 ADR·실험: [ADR-009](docs/11-decisions/ADR-009-seed-vc-voice-provider.md), [ADR-010](docs/11-decisions/ADR-010-voice-provider-selection-policy.md), [ADR-011](docs/11-decisions/ADR-011-voice-provider-selection.md), [EXP-004](reports/experiments/EXP-004-seed-vc.md), [EVAL-003](reports/evaluations/EVAL-003-seed-vc-listening-evaluation.md), [QG-001](reports/quality-gates/QG-001-voice-conversion-operational-readiness.md).
- Provider Selection: Primary·Fallback 미선정, RVC Secondary 평가 후보, Seed-VC·Vevo2 Experimental, OpenVoice·CosyVoice·Fish Speech Rejected.
- Quality Gate: EVAL-003, clipping/export 회귀, 배포 라이선스, 8GB 후보 실측과 유지보수 조건 전에는 실제 Voice Provider를 운영 Pipeline에 연결하지 않는다. Mock 기반 Orchestrator 기술 검증은 ADR-012에 따라 분리했다.
- 예상 다음 단계: Primary 차단 조건을 해소할 후보 검증 범위 결정.

## Phase 5. Pipeline Integration — [완료]

- 목표: Music → Stem → Voice → Mixer를 하나의 추적 가능한 비동기 Pipeline으로 연결한다.
- 구현 범위·포함 기능: 단계 오케스트레이션, Default/Mock Audio Mixer, gain·headroom·peak normalization·soft limiter·fade·품질 metadata, 통합 API·Worker·상태·파일, 실패·재시도·timeout 정책, Benchmark·통합 테스트.
- 제외 기능: Lyrics AI, Frontend, 외부 운영 Queue의 제품 선정.
- 선행 조건: Phase 2·3·4 Provider 인터페이스. Voice 품질 게이트 미충족은 Mock Voice 고정으로 격리한다.
- 완료 조건: [Phase-05 DoD](docs/DoD/Phase-05.md), Mock AI 단일 E2E Job, 실제 Mixer와 회귀·실패 검증.
- 산출물: Pipeline Service·Worker·API, Audio Quality Engine, 통합 ADR·실험·평가 보고서.
- 관련 문서: [Pipeline Orchestrator](docs/03-architecture/pipeline-orchestrator.md), [Audio Quality Engine](docs/03-architecture/audio-quality-engine.md), [Pipeline API](docs/06-api/pipeline-api.md), [Job State](docs/07-database/job-state-model.md).
- 관련 ADR·실험: [ADR-012](docs/11-decisions/ADR-012-pipeline-orchestrator.md), [ADR-013](docs/11-decisions/ADR-013-audio-mixing-engine.md), [EXP-005](reports/experiments/EXP-005-pipeline-execution.md), [EXP-006](reports/experiments/EXP-006-audio-mixing.md), [EVAL-004](reports/evaluations/EVAL-004-audio-mixing-listening-evaluation.md).
- 예상 다음 단계: Phase 6 Lyrics AI.

## Phase 6. Lyrics AI — [완료]

- 목표: 사용자 의도를 안전하고 구조화된 가사 초안으로 변환한다.
- 구현 범위·포함 기능: `LyricsGenerator`, Template·Mock Provider, 동기식 API·DB, 한국어·영어 구조화·검증·안전 Benchmark.
- 제외 기능: 저작권 침해 가사 복제, Doha Voice 학습, Studio UI.
- 선행 조건: 생성 Provider 입력 계약과 콘텐츠 정책 검토.
- 완료 조건: [Phase-06 DoD](docs/DoD/Phase-06.md), 로컬 Provider·안전·Backend·자동 품질 검증. 실제 창작 품질은 사용자 평가로 분리한다.
- 산출물: Lyrics Adapter·API·`lyrics_documents`·Benchmark·안전 평가·ADR·EXP·EVAL.
- 관련 문서: [Lyrics AI](docs/03-architecture/lyrics-ai.md), [DohaLM 연동](docs/03-architecture/dohalm-integration.md), [Lyrics API](docs/06-api/lyrics-api.md), [Generated Content Policy](docs/09-security/generated-content-policy.md).
- 관련 ADR·실험: [ADR-014](docs/11-decisions/ADR-014-lyrics-generator-architecture.md), [EXP-007](reports/experiments/EXP-007-lyrics-generation.md), [EVAL-005](reports/evaluations/EVAL-005-lyrics-quality.md).
- 예상 다음 단계: Phase 7 Doha Voice 또는 Phase 8 Studio 선행 설계 검토.

## Phase 6.6~6.9. Local Lyrics LLM — [계획]

- 목표: 공개 Instruct Base Model을 권리 확보 Lyrics Dataset으로 QLoRA SFT하고 기존 `LyricsGenerator` 경계의 운영 Provider 후보로 검증한다.
- 포함: Dataset 계보·권리·split, Base 후보·license 비교, QLoRA SFT, `LocalLyricsLLMAdapter`, Validator·성능·사용자 품질 게이트.
- 제외: 전체 LLM 사전학습, 무단 상업 가사 수집, 기본 Provider 즉시 변경, 승인 전 Pipeline 자동 연결.
- 선행 조건: [Dataset Policy](docs/05-data/lyrics-dataset-policy.md), Base license·8GB 실행성 승인, Phase 6 공통 계약 유지.
- 완료 조건: Phase 6.6~6.9의 Dataset·Model Card·benchmark·사용자·운영 승인 기준 충족.
- 산출물: Dataset Card·manifest, LoRA Adapter 또는 검증 병합 모델, Model Card, Local Adapter, Quality Gate 보고서.
- 관련 문서: [Lyrics Architecture](docs/03-architecture/lyrics-ai.md), [ADR-016](docs/11-decisions/ADR-016-local-lyrics-llm-finetuning.md), [Roadmap](planning/local-lyrics-llm-roadmap.md).
- 예상 다음 단계: Phase 6.6 Dataset 권리·schema 승인.

## Phase 7. Doha Voice — [계획]

- 목표: 동의·삭제 가능한 본인 가창 Dataset으로 개인화 음성 품질을 검증한다.
- 구현 범위·포함 기능: DohaVocal `[계획]`의 Dataset, preprocessing, LoRA·Fine Tuning 후보, Checkpoint, Benchmark·Evaluation과 Runtime 후보. DohaMusic은 동의·권한·삭제 결정과 Provider Orchestration을 유지한다.
- 제외 기능: 타인 음성 학습, 무동의 수집, 대규모 기반 모델 사전학습.
- 선행 조건: Voice Conversion baseline, 동의·삭제·라이선스·보안 ADR 승인.
- 완료 조건: [Phase-07 DoD](docs/DoD/Phase-07.md), 데이터 계보·삭제·전후 비교·사용 승인.
- 산출물: Dataset schema·전처리 도구·학습 실험·평가·Model Card.
- 관련 문서: [Audio Data Policy](docs/05-data/audio-data-policy.md), [Voice Consent](docs/09-security/voice-consent-policy.md).
- 관련 ADR·실험: 개인 음성 학습 ADR·실험 필요, 아직 없음.
- 예상 다음 단계: Phase 8 Doha Studio.

Phase 7은 DohaVocal에서 동의된 사용자 음성으로 `VoiceConverter` 후보를 개인화하는 별도 단계다. 신규 Singing Voice·Voice Conversion도 DohaVocal에서 구현한다. 가사 text Dataset, Instruct LLM, LoRA Adapter와 checkpoint·Model Card·저장 정책을 공유하지 않는다. F6 Voice Enrollment는 DohaMusic의 기존 Voice Conversion용 참조 음성 등록 UX이며 장시간 Dataset·학습 동의·split·artifact를 포함하지 않는다.

## Phase 8. Doha Studio — [완료]

- 목표: 생성 구성·재생·이력·파일 관리를 제공하는 로컬 사용자 Responsive Studio MVP를 구축한다.
- 구현 범위·포함 기능: Premium Dark responsive shell, Prompt·Lyrics·Voice·Review, 오류 backoff Pipeline polling, History·Project CRUD와 자동 Default Project, 공개 allowlist 결과 metadata, 완료 Pipeline의 보안 WAV content·download, 전역 Player, cooperative Cancel과 Snapshot 기반 새 Job Retry.
- 제외 기능: 편집 가능한 DAW Timeline·Track·Clip·Mixer·Composition QA, Production 인프라 전환과 공개 운영 승인.
- 선행 조건: Phase 5 Pipeline API 충족. 인증·소유권은 Phase 9 공개 운영 차단 조건이며 로컬 MVP 완료와 구분한다.
- 완료 조건: [Phase-08 DoD](docs/DoD/Phase-08.md), 주요 화면·접근성·빌드·E2E·권한 검증.
- 산출물: Frontend 애플리케이션, Design System·Component·Responsive·Studio UX 문서, E2E 결과.
- 관련 문서: [Frontend Overview](docs/03-architecture/frontend-overview.md), [Frontend Architecture](docs/03-architecture/frontend-architecture.md), [Design Reference Policy](docs/03-architecture/design-reference-policy.md), [Studio UX](docs/03-architecture/studio-ux-flow.md), [Frontend Roadmap](planning/frontend-roadmap.md), [User Scenarios](docs/00-overview/user-scenarios.md).
- 관련 ADR·실험: [ADR-017 Frontend Technology Stack](docs/11-decisions/ADR-017-frontend-technology-stack.md)은 `[승인]`; 파일 전달은 [ADR-018](docs/11-decisions/ADR-018-secure-audio-file-access.md), Voice upload는 [ADR-019](docs/11-decisions/ADR-019-secure-voice-profile-upload.md), History 보존은 [ADR-020](docs/11-decisions/ADR-020-project-history-retention.md)을 따른다.
- 예상 다음 단계: Phase 9 Production, 기존 완료 범위를 바꾸지 않는 F6 Guided Voice Enrollment 또는 독립 AI-native DAW Product Track.

### F6. Guided Voice Enrollment — [진행 중]

- 목표: 사용자가 안내에 따라 본인 참조 음성을 녹음하거나 기존 WAV를 제출하고 기본 문제를 확인한 뒤 Voice Profile을 등록하게 한다.
- 현재 사실: 기존 단일 WAV upload와 Enrollment 7개 endpoint, PCM16 48kHz mono 정규화, 임시·최종 Storage, 기본 품질 검사, 멱등성, 24시간 sliding/7일 absolute 만료와 cleanup scheduler/crash recovery를 구현했다. `/voice` Wizard와 Windows·Ubuntu CI WebM/Ogg 검증도 완료했다.
- 선행 조건: [ADR-024](docs/11-decisions/ADR-024-browser-voice-recording-server-normalization.md)·[ADR-025](docs/11-decisions/ADR-025-voice-profile-multiple-samples-reference.md)의 Provider 품질 근거를 보완하고, 구현된 [ADR-026](docs/11-decisions/ADR-026-voice-enrollment-lifecycle-cleanup.md)과 [API](docs/06-api/voice-enrollment-api.md)·[데이터 모델](docs/07-database/voice-enrollment-data-model.md) 계약을 유지한다.
- 남은 gap: chunk resume, 동의 철회·파생 삭제, FFmpeg build 라이선스 운영 검토, 실제 사용자 수동 녹음·브라우저 MIME 평가, 장기 운영 모니터링.
- 보안 조건: 음성 binary Web Storage·Analytics 저장/전송 금지. 공개 운영 인증·소유권·감사·rate limit은 Phase 9 선행이다.
- 산출물: [Voice Enrollment 요구사항](docs/02-requirements/voice-enrollment-requirements.md), [ADR-024~026](docs/11-decisions/README.md#f6-guided-voice-enrollment), [API](docs/06-api/voice-enrollment-api.md)·[데이터 모델](docs/07-database/voice-enrollment-data-model.md), `/voice` Wizard·녹음·품질·대표 선택 UI, Frontend unit·component·E2E와 [Validation Report](reports/validation/VALIDATION-VOICE-ENROLLMENT.md).
- 완료 판정: [Frontend Roadmap F6](planning/frontend-roadmap.md#f6--guided-voice-enrollment-진행-중)의 독립 체크리스트. Phase 8 `15/15, 100%`의 분모·상태를 변경하지 않는다.
- Phase 7 경계: F6 sample을 학습 Dataset에 자동 재사용하지 않는다. 재사용은 별도 opt-in·lineage·삭제 ADR이 필요하다.

## Phase 9. Production — [계획]

- 목표: DohaMusic을 복구·관측·보안 가능한 운영 환경으로 전환한다.
- 구현 범위·포함 기능: PostgreSQL, Redis·Celery 후보 검증, Docker, HTTPS, Monitoring, Backup, Security·배포 자동화.
- 제외 기능: 검증 없는 클라우드·Queue·DB 선택과 `main` 자동 배포.
- 선행 조건: Studio MVP, 부하·보안·비용 요구사항, 릴리스 승인.
- 완료 조건: [Phase-09 DoD](docs/DoD/Phase-09.md), migration·복구·부하·보안·배포·rollback 검증.
- 산출물: 배포 구성, Runbook, 모니터링·백업·보안 보고서, 릴리스 기록.
- 관련 문서: [Deployment Guide](docs/10-operations/deployment-guide.md), [Security Policy](docs/09-security/security-policy.md).
- 관련 ADR·실험: DB·Queue·Storage·배포·보안 ADR와 부하 실험 필요, 아직 없음.
- 예상 다음 단계: 사용자 승인 후 `develop → main` 안정화 릴리스.

## 현재 권장 다음 작업

```text
D1 Composition Read 계약 확정
  ↓
D1-A Backend Composition aggregate read [완료]
  ↓
D1-Transition 무선택 Workspace bootstrap gate [완료]
  ↓
D1-B Frontend Workspace Composition 연결·fixture Snapshot 통합 [완료]
  ↓
D2 Timeline Playback Foundation [완료]
  ↓
Waveform / richer Playhead [완료]
  ↓
Clip Domain / Persistence ADR [설계 완료]
  ↓
Clip Persistence Foundation [완료]
  ↓
Clip Service Authority Foundation [완료]
  ↓
Revision-safe Idempotency Foundation [완료]
  ↓
WorkingComposition Service + Product API [완료]
  ↓
Frontend Clip Editing [완료]
  ↓
AssetVersion-safe media source Backend [완료]
  ↓
Track/Clip Waveform Frontend [완료]
  ↓
Working Preview Backend Foundation [완료]
  ↓
Working Preview Frontend Integration [완료]
  ↓
Composition commit [완료]
  ↓
Clip Copy [완료]

Clip Gain Backend Foundation [완료]

Clip Gain Frontend UI [완료]

Fade Authority / Foundation [완료]

Fade Frontend Integration [완료]

Loop Authority / Foundation [완료]

Loop Frontend Integration [완료]

Loop History Phase Restore Authority [완료]

Persistent History Authority / Foundation [완료]

Persistent History Frontend Integration [완료]

Multi-user 동시 편집 recovery [완료]

Mixer와 Export Authority [다음]
```

이 순서는 기존 Phase·품질 평가·Provider 분리 Track을 취소하지 않는다. D1-B와 WorkingComposition persistence·Service·Product API·safe media resolution·Preview·Clip Gain/Fade/Loop·persistent history Backend/Frontend 완료는 source·격리 fixture 기준이며 실제 사용자 DB `0017 → 0028` 적용을 뜻하지 않는다.
