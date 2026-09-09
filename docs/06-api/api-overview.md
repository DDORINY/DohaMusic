# API 개요

> 문서 목적: 구현된 REST API와 공통 계약을 정의한다.
> 현재 상태: **Legacy Backend Foundation 구현 완료 / Workspace Job 계약·schema·Cursor·Service·Completion UoW·Worker execution foundation·공식 API 5/5 구현 / Provider dispatch wiring·background daemon 미구현**

기본 prefix는 `/api`다. 현재 인증과 사용자 소유권 검사는 구현하지 않았다. OpenAPI 문서는 서버 실행 후 `/docs`, 스키마는 `/openapi.json`에서 확인할 수 있다.

## 현재 Runtime API Surface

`2026-09-06` 기준 실제 FastAPI application import와 OpenAPI 생성 결과는 Route 107개, APIRoute 103개, OpenAPI Path 82개, Operation 103개다. 공개 operation은 GET 40개, POST 39개, PATCH 13개, DELETE 9개, HEAD 2개이며 operation ID와 `(method, path)` 중복은 각각 0개다. 이 수치와 request/response 핵심 계약은 전역 API surface regression Gate가 검증한다. 상세 측정 방법과 fingerprint는 [API Surface 검증](../../reports/validation/VALIDATION-API-SURFACE.md)을 따른다.

## Workspace v1 목표 계약 — [진행 중]

DohaStudio Common Specification과 Asset 중심 DB Redesign을 기준으로 `/api/v1` Workspace REST API를 별도로 설계했다. 공통 기반과 Workspace Resource Endpoint 30개를 구현했다. Workspace Job은 Aggregate·상태·입출력 role·Provider·claim/lease·completion 경계, revision `20260810_0017` schema·Index, Cursor·Repository keyset, Service·Completion UoW·Worker execution foundation과 공식 Router 5개를 구현했다. Job API는 5/5다. 목표 계약은 16개 그룹·64개 Method/Path 조합이며 나머지 34개는 `[계획]`이다. Provider dispatch wiring과 background daemon·scheduler는 미구현이며 아래 Legacy API와 payload는 그대로 유지한다.

- [Workspace REST API 공통 계약](workspace-rest-api-contract.md)
- [API 기반·Bootstrap](workspace-api-foundation-bootstrap.md)
- [목표 Endpoint 목록](workspace-rest-api-endpoints.md)
- [Artifact Storage 계약](../03-architecture/artifact-storage-contract.md)
- [CompositionSnapshot 기반](composition-snapshot-foundation.md)
- [D1 Composition Read Workspace 계약](composition-read-workspace.md)
- [WorkingComposition·Working Preview Product API](working-composition-api.md)
- [Persistent WorkingComposition History API](persistent-working-history-api.md)
- [Workspace Job Foundation](../03-architecture/workspace-job-foundation.md)
- [Provider API 계약](provider-api-contract.md)
- [API 전환 전략](api-contract-migration-strategy.md)
- [ADR-031](../11-decisions/ADR-031-workspace-rest-api-contract.md)
- [ADR-035 D1 Composition Read 권위](../11-decisions/ADR-035-d1-composition-read-authority.md)
- [ADR-052 Working Preview Render 권위](../11-decisions/ADR-052-working-composition-preview-render-authority.md)

| 메서드 | 경로 | 성공 응답 | 설명 |
|---|---|---:|---|
| `GET` | `/health` | 200 | 애플리케이션 상태 확인 |
| `POST` | `/api/generations` | 202 | Mock 생성 Job 생성 |
| `GET` | `/api/generations/{id}` | 200 | Job 상태 조회 |
| `GET` | `/api/generations/{id}/files` | 200 | Job 결과 파일 메타데이터 조회 |
| `POST` | `/api/stems` | 202 | 생성 파일 기반 Stem 분리 Job 생성 |
| `GET` | `/api/stems/{job}` | 200 | Stem Job 상태 조회 |
| `GET` | `/api/stems/{job}/files` | 200 | vocals·instrumental·metadata 조회 |
| `POST` | `/api/voice-profiles` | 201 | 동의가 확인된 음성 프로필 메타데이터 생성 |
| `POST` | `/api/voice-profiles/upload` | 201 | 동의된 WAV를 검증·저장하고 Profile 생성 |
| `GET` | `/api/voice-profiles` | 200 | 공개 Voice Profile 목록 |
| `GET` | `/api/voice-profiles/{id}` | 200 | 공개 Voice Profile 상세 |
| `DELETE` | `/api/voice-profiles/{id}` | 204 | 음성 프로필 삭제 |
| `POST` | `/api/voice-conversion` | 202 | vocals와 동의된 Voice Profile로 변환 Job 생성 |
| `POST` | `/api/voice-enrollments` | 201 | Guided Voice Enrollment draft 생성 |
| `GET` | `/api/voice-enrollments/{id}` | 200 | Enrollment·Sample·cleanup 요약 조회와 lazy 만료 |
| `POST` | `/api/voice-enrollments/{id}/samples` | 201 | WAV/WebM/Ogg upload·정규화·기본 품질 검사 |
| `GET/DELETE` | `/api/voice-enrollments/{id}/samples/{sample_id}` | 200 | Sample 조회·cleanup 삭제 |
| `POST` | `/api/voice-enrollments/{id}/submit` | 201 | 대표 Sample을 Voice Profile로 승격 |
| `POST` | `/api/voice-enrollments/{id}/cancel` | 200 | 미완료 Enrollment 취소·cleanup |
| `GET` | `/api/voice-conversion/{job}` | 200 | Voice Conversion Job 조회 |
| `GET` | `/api/voice-conversion/{job}/files` | 200 | converted_voice·metadata 조회 |
| `POST` | `/api/pipelines` | 202 | Mock AI·Default Audio Mixer Pipeline Job 생성 |
| `GET` | `/api/pipelines/{job}` | 200 | 단계·진행률·metadata 조회 |
| `GET` | `/api/pipelines/{job}/files` | 200 | Pipeline 결과 파일 metadata 조회 |
| `GET` | `/api/history` | 200 | 최신순 History 목록·검색·상태·페이지네이션 |
| `GET` | `/api/history/{job}` | 200 | History 상세 조회 |
| `GET` | `/api/projects` | 200 | Project 목록과 Job 수 조회 |
| `POST` | `/api/projects` | 201 | Project 생성 |
| `GET` | `/api/projects/{id}` | 200 | Project와 포함 Job 조회 |
| `PATCH` | `/api/projects/{id}` | 200 | Project 제목·설명 변경 |
| `DELETE` | `/api/projects/{id}` | 204 | Project 연결 해제 삭제, Job·파일 유지 |
| `POST` | `/api/lyrics` | 201 | Template·Mock 가사 초안 생성·저장 |
| `GET` | `/api/lyrics/{id}` | 200 | 가사 문서 조회 |
| `POST` | `/api/lyrics/validate` | 200 | 직접 작성 가사 정규화·검증 |
| `DELETE` | `/api/lyrics/{id}` | 204 | 가사 문서 삭제 |

생성, Stem, Voice, Pipeline 요청은 비동기이며 `202 Accepted`와 `PENDING` Job을 반환한다. Lyrics와 Guided Voice Enrollment 정규화·제출은 로컬 동기 계약이며 `201`/`200`을 반환한다. Enrollment의 CPU·subprocess 처리는 threadpool을 사용하지만 별도 durable Job은 만들지 않는다. 오류는 다음 형식을 사용한다.

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Generation job을(를) 찾을 수 없습니다."
  }
}
```

Pipeline 결과 WAV의 content/download, 기존 Voice Profile 단일 WAV upload, Guided Enrollment 다중 sample·사전 validation·임시 상태와 Pipeline 취소·새 Job 재시도를 제공한다. Guided Enrollment의 주기 만료·cleanup·crash recovery는 공개 endpoint 없이 Backend lifecycle에서 동작한다. 인증·사용자 소유권, Voice 원본 content와 개별 생성 모듈 download API는 후속 단계 범위다.
