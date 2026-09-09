# Workspace REST API 전환 전략

> 문서 상태: [진행 중]
> 최종 수정일: 2026-08-10
> 관련 기능: 현행 기능별 API에서 Workspace v1 API로 단계적 전환
> 구현 상태: `/api/v1` 공통 기반·명시적 Bootstrap 도구·HMAC Cursor와 Resource Endpoint 30개 구현; 나머지 Endpoint·Adapter·Redirect·정적 OpenAPI 파일 미구현
> 관련 문서: [API 기반·Bootstrap](workspace-api-foundation-bootstrap.md), [현재 API 개요](api-overview.md), [목표 공통 계약](workspace-rest-api-contract.md), [목표 Endpoint 목록](workspace-rest-api-endpoints.md), [DB 전환 전략](../07-database/database-redesign-migration-strategy.md)

## 1. 현재와 목표

현재 API는 `/api/generations`, `/api/stems`, `/api/voice-conversion`, `/api/pipelines`, `/api/lyrics`, `/api/voice-profiles`, `/api/voice-enrollments`, `/api/projects`와 `/api/history`처럼 기능별 Resource를 노출합니다.

목표 API는 `/api/v1` 아래 Workspace·Project·Asset·AssetVersion·Artifact·CompositionSnapshot·Job 중심으로 통합합니다. 공통 Router·응답·request ID·오류와 HMAC Cursor 기반에 Workspace·MusicProject·ProjectAsset·Asset·AssetVersion·Artifact·CompositionSnapshot Resource Route 25개를 연결했습니다. CompositionSnapshot은 불변 생성·Cursor·필수 `Idempotency-Key`와 목록·생성·상세 Router 3개를 구현했으며, 기존 Runtime Endpoint의 동작이나 status는 변경하지 않습니다.

## 2. 현행 경로 매핑

| 현행 경로 | 목표 경로·Resource | 전환 원칙 |
|---|---|---|
| `POST /api/generations` | `POST /api/v1/jobs` | `job_type=music_generation`, 결과를 Music AssetVersion·Artifact로 등록 |
| `GET /api/generations/{id}` | `GET /api/v1/jobs/{job_id}` | 공통 Job 상태로 projection |
| `GET /api/generations/{id}/files` | Job output + Artifact API | 경로 대신 Artifact ID·link 반환 |
| `POST /api/stems` | `POST /api/v1/jobs` | `job_type=stem_separation`, input AssetVersion 또는 Artifact 사용 |
| `GET /api/stems/{id}` | `GET /api/v1/jobs/{job_id}` | stage를 별도 field로 보존 |
| `GET /api/stems/{id}/files` | Stem AssetVersion·Artifact | source Music과 AssetRelation 기록 |
| `POST /api/voice-conversion` | `POST /api/v1/jobs` | `job_type=voice_conversion`, Enrollment 참조 분리 |
| `GET /api/voice-conversion/{id}` | `GET /api/v1/jobs/{job_id}` | ModelUsage와 공통 Error 연결 |
| `GET /api/voice-conversion/{id}/files` | Vocal AssetVersion·Artifact | converted Vocal의 Lineage 반환 |
| `POST /api/pipelines` | Snapshot 생성 + `POST /api/v1/jobs` | 직접 Pipeline 실행 대신 정확한 CompositionSnapshot과 독립 Job 사용 |
| `GET /api/pipelines/{id}` | `GET /api/v1/jobs/{job_id}` | 기존 단계 상태는 `stage`·History로 보존 |
| `POST /api/pipelines/{id}/cancel` | `POST /api/v1/jobs/{job_id}/cancel` | 공통 취소 계약 사용 |
| `POST /api/pipelines/{id}/retry` | `POST /api/v1/jobs/{job_id}/retry` | 새 Job ID와 `retry_of_job_id` 사용 |
| `/api/pipelines/{id}/files/...` | `/api/v1/artifacts/{artifact_id}/...` | Job·File 경로 대신 Artifact 권한·checksum 검증 |
| `POST /api/lyrics` | Lyrics Job + AssetVersion | 생성은 Job, 검증 결과는 새 Lyrics Version |
| `POST /api/lyrics/{id}/revisions` | AssetVersion 생성 또는 후속 versioned Job type | 현재 공식 10개 Job type에는 `lyrics_revision`을 넣지 않고 원본 Version 불변 유지 |
| `GET /api/lyrics/{id}` | AssetVersion API | Lyrics Asset와 Version을 분리 |
| `DELETE /api/lyrics/{id}` | `DELETE /api/v1/assets/{asset_id}` | Version 직접 삭제가 아닌 Asset Soft Delete |
| `/api/projects` | `/api/v1/projects` | 기본 Workspace ID를 명시적으로 연결 |
| `/api/history` | `/api/v1/history` | Pipeline projection에서 append-only History로 전환 |
| `/api/voice-profiles` | Recording + Enrollment | Recording Asset/Take와 음색 Enrollment·Approval을 분리 |
| `/api/voice-enrollments` | `/api/v1/enrollments` | upload는 Recording Take API, Consent는 Enrollment API로 분리 |
| `/health` | `/health` | unversioned process probe 유지 |

HTTP redirect만으로 바꾸면 request·response 의미가 달라질 수 있으므로 write Endpoint에 자동 redirect를 사용하지 않습니다.

## 3. 상태 매핑

| 현행 상태 | 목표 Job 상태 | 추가 보존 |
|---|---|---|
| `PENDING` | `queued` | `current_step`은 `stage` |
| `VALIDATING`, `GENERATING`, `STEM_SEPARATING`, `VOICE_CONVERTING`, `MIXING`, `EXPORTING`, `CANCEL_REQUESTED` | `running` | 원래 단계는 `stage`와 History |
| `COMPLETED` | `succeeded` | Artifact checksum 검증 후 확정 |
| `FAILED` | `failed` | error code·retryable·details ID |
| `CANCELLED` | `cancelled` | 취소 요청·확정 시각 |

상태 문자열을 단순 번역하는 것으로 성공을 인정하지 않습니다. DB Redesign의 Job·Output·Artifact 검증이 함께 완료돼야 합니다.

## 4. 단계적 전환

### Phase A — 계약 고정

1. Common Specification PR #2와 DB Redesign PR #52를 승인·병합합니다.
2. Workspace API v1의 Resource Schema, enum, ID와 Error code를 확정합니다.
3. Recording typed surface와 generic Asset mutation 중 canonical write 경로를 결정합니다.
4. Approval·AssetRelation·ProcessingChain의 공개 API 범위를 결정합니다.
5. OpenAPI 작성 전 계약 검토를 완료합니다.

Workspace·Project 목록의 `(created_at DESC, UUID DESC)` keyset Repository와 Service page 결과, filter fingerprint와 HMAC-SHA256 codec은 구현했습니다. Router 연결과 실제 `cursor` Query 처리는 Resource Endpoint 작업에서 수행합니다.

### Phase B — Read Projection

1. 현행 DB를 source of truth로 유지합니다.
2. 기존 row를 목표 Workspace Resource 형태로 읽는 projection을 추가합니다.
3. `/api/v1` GET response와 현행 response의 Resource 수·상태·권한·Artifact link를 비교합니다.
4. path·비밀정보·내부 Metadata 누출 검사를 수행합니다.
5. 차이가 있으면 새 GET을 공개하지 않습니다.

### Phase C — 목표 DB와 Write 도입

1. DB Redesign의 additive schema와 backfill을 먼저 검증합니다.
2. Project·Asset·Version·Selection·Snapshot·Job 순서로 v1 write를 추가합니다.
3. Idempotency-Key, transaction, History와 Error contract를 검증합니다.
4. Provider API는 Local Adapter와 HTTP Runtime 모두 같은 의미 계약 뒤에 둡니다.
5. 새 write 결과를 현행 API가 읽어야 하는 기간의 compatibility projection을 검증합니다.

### Phase D — Frontend 전환과 Deprecation

1. Frontend를 Workspace v1 API로 전환합니다.
2. browser·API E2E에서 AssetVersion 불변성, Snapshot 재현, Job 취소·재시도와 Artifact access를 검증합니다.
3. 현행 Endpoint에 `Deprecation`, `Sunset`과 replacement link를 추가합니다.
4. Legacy 사용량과 실패율을 측정합니다.
5. 문서·OpenAPI에서 현재와 목표 상태를 명확히 구분합니다.

### Phase E — Legacy 제거

1. 모든 client 전환과 보존 기간 완료를 확인합니다.
2. 별도 승인 PR에서 기능별 Legacy Router와 DTO를 제거합니다.
3. DB Legacy Table 제거와 같은 배포에서 Big-bang으로 수행하지 않습니다.
4. 운영 rollback window와 backup 복구를 검증합니다.
5. 제거 뒤 API v1 OpenAPI, 문서와 DoD를 갱신합니다.

## 5. Compatibility 원칙

- 현행 Endpoint는 v1이 구현되기 전까지 구현 완료 상태를 유지합니다.
- 새 v1 문서가 존재한다는 이유로 현재 response field나 status를 변경하지 않습니다.
- Legacy와 v1 response를 한 Endpoint에서 client header에 따라 바꾸지 않습니다.
- write 요청을 자동 redirect하지 않습니다.
- Legacy response에 새 Artifact ID를 additive로 제공할지 여부는 별도 계약으로 결정합니다.
- deprecated Endpoint는 제거일, 대체 경로와 제한을 문서화합니다.
- Provider Runtime API를 Legacy Workspace Endpoint의 대체 public API로 사용하지 않습니다.

## 6. Rollback

| 전환 단계 | Rollback 원칙 |
|---|---|
| Read Projection | v1 GET 비활성화, 현행 API 유지 |
| Dual Write | v1 write 중단, 목표 row 격리, 현행 source of truth 유지 |
| Frontend 전환 | client base URL을 현행 API로 복원하되 v1에서만 생성된 데이터의 projection을 먼저 검증 |
| Legacy 제거 | backup·직전 release 복구. 신규 Version·Artifact 손실 가능 시 자동 rollback 금지 |

Rollback이 AssetVersion, Snapshot, Job과 History를 삭제하거나 덮어쓰지 않습니다.

## 7. 검증 Gate

| Gate | 통과 기준 |
|---|---|
| Endpoint | Method·Path 중복과 의미 충돌 0건 |
| REST | GET side effect 0건, Version·Snapshot PATCH/DELETE 0건 |
| Resource | Asset·Version·Artifact 혼용 0건 |
| Job | 공통 다섯 상태와 새 Retry Job 계약 100% |
| Snapshot | 최신 Asset 간접 참조 0건 |
| Artifact | 절대·상대 경로 응답 0건 |
| Idempotency | 같은 key·fingerprint의 중복 Resource 0건 |
| Error | 모든 오류에 error code·message·request ID, 비밀정보 0건 |
| Permission | Workspace 교차 접근 0건 |
| Provider | Provider 직접 호출 client 0건, Provider 간 직접 호출 0건 |
| Compatibility | Legacy client 회귀와 v1 projection 동등성 검증 |

## 8. 미확정 사항

- Common Specification과 DB Redesign의 병합 순서
- v1 API와 목표 DB를 같은 release에서 시작할지 분리할지
- Legacy 지원 기간과 Sunset 날짜
- Artifact Resolver 구현 전 content·download compatibility
- 현재 Voice Profile ID를 Recording·Enrollment ID로 분리하는 client 전환 방식
- 기존 Pipeline 입력 JSON을 CompositionSnapshot으로 승인할 기준
- OpenAPI 문서를 하나의 schema로 만들지 Workspace·Provider로 분리할지
