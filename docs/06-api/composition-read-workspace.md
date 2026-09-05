# D1 Composition Read Workspace API 계약

> 문서 상태: [D1-A 완료 / D1-Transition 완료 / D1-B 완료]
> 최종 수정일: 2026-08-29
> 관련 기능: AI-native DAW D1 읽기 전용 Composition aggregate
> 구현 상태: D1-A·D1-Transition·D1-B IMPLEMENTED
> 관련 문서: [CompositionSnapshot 기반](composition-snapshot-foundation.md), [Workspace REST API 공통 계약](workspace-rest-api-contract.md), [ADR-035](../11-decisions/ADR-035-d1-composition-read-authority.md), [Frontend 전환 계획](../../planning/ai-native-daw-frontend-migration.md)

## 1. CURRENT / TARGET / NOT IMPLEMENTED

### CURRENT

- Workspace·MusicProject·ProjectAsset·Asset·AssetVersion·Artifact·CompositionSnapshot Entity와 Repository·Service 기반
- `GET /api/v1/snapshots`, `POST /api/v1/snapshots`, `GET /api/v1/snapshots/{composition_snapshot_id}`
- exact AssetVersion을 고정하는 불변 Snapshot, Project·Owner scope와 HMAC Cursor
- Artifact ID 기반의 경로 비노출 metadata·content·download API
- 현재 Frontend의 Legacy Project·History·Pipeline read

### TARGET D1

- Workspace v1을 authority로 사용하는 Project 단위 Composition aggregate GET
- 명시적으로 선택된 current Snapshot과 요청한 history Snapshot의 분리
- exact AssetVersion·safe Artifact reference·snapshot-local Track projection
- Section 비가용 상태, Mix JSON과 lineage ID
- bootstrap/empty/selection-required/error/recovery를 구분하는 Frontend 소비 계약

### NOT IMPLEMENTED

- 실제 사용자 DB migration·bootstrap·backfill·인증 사용자 mapping
- canonical Track·Section·Clip Domain, Timeline·editing·typed Mixer
- Provider·MusicIntent write·Reference·Evaluation·Learning 실행

## 2. Endpoint

```http
GET /api/v1/projects/{project_id}/composition
GET /api/v1/projects/{project_id}/composition?composition_snapshot_id={uuid}
PATCH /api/v1/projects/{project_id}/composition-selection
```

Project가 aggregate root이므로 Project namespace를 사용한다. `project_id`와 effective Owner가 Workspace scope를 결정하므로 `workspace_id`를 path나 query에 중복하지 않는다.

`composition_snapshot_id`는 선택적 history preview parameter다. 지정하면 같은 Project의 exact Snapshot을 읽되 Project selection을 변경하지 않는다. 생략하면 Project의 explicit selected Snapshot만 해석한다. 최신 Snapshot으로 자동 대체하지 않는다.

이 endpoint는 단일 aggregate read이므로 cursor를 받지 않는다. Version History는 기존 `GET /api/v1/snapshots?project_id={project_id}&cursor=...`를 재사용한다.

## 3. Authorization과 scope

CURRENT D1 foundation은 bootstrap된 단일 사용자 Workspace에서 effective Owner를 파생한다.

```text
effective Owner
→ active Workspace
→ active MusicProject
→ selected/requested CompositionSnapshot
→ SnapshotItem
→ owned ProjectAsset / Asset / exact AssetVersion / Artifact
```

- request body는 없으며 `owner_id`, `created_by`, `workspace_id` override를 query·header로 받지 않는다.
- 다른 Owner·Workspace·Project의 Resource는 존재를 확인해 주지 않고 기존 `404` privacy pattern을 사용한다.
- 실제 인증 principal → Owner mapping은 TARGET이며 D1 Frontend 공개 전 필수 Gate다.

## 4. selected/current 의미

- canonical current는 Project 수준의 explicit `selected_snapshot_id`다.
- Snapshot version 최신순은 history 정렬일 뿐 current 결정 규칙이 아니다.
- `composition_snapshot_id` query는 read target만 지정하며 selection을 쓰지 않는다.
- selected pointer와 requested Snapshot은 반드시 URL의 Project에 속해야 한다.
- Snapshot 생성과 aggregate GET은 Project selection을 암묵적으로 변경하지 않는다.

selection resolution은 다음 최소 구조를 사용한다.

```json
{
  "selected_snapshot_id": "uuid-or-null",
  "resolved_snapshot_id": "uuid-or-null",
  "resolution": "selected | requested | none",
  "is_current": true
}
```

`is_current`는 resolved Snapshot이 selected Snapshot과 같을 때만 `true`다. Snapshot이 없으면 `false`다.

## 5. 응답 모델

제품 DTO 이름은 `CompositionWorkspaceRead`로 고정한다. 공통 성공 envelope의 `data` 안에 다음을 반환한다.

```json
{
  "data": {
    "state": "ready | empty | selection_required",
    "project": {
      "project_id": "uuid",
      "workspace_id": "uuid",
      "title": "string",
      "lifecycle_status": "active"
    },
    "selection": {
      "selected_snapshot_id": null,
      "resolved_snapshot_id": null,
      "resolution": "none",
      "is_current": false
    },
    "snapshot": null,
    "items": [],
    "track_projections": [],
    "section_projection": {
      "availability": "not_available",
      "items": []
    },
    "mix_settings_snapshot": {},
    "lineage": {
      "processing_chain_id": null,
      "provider_versions": {},
      "model_manifest_ids": {}
    }
  },
  "request_id": "opaque-request-id"
}
```

`snapshot`은 기존 `CompositionSnapshotSummary`와 `CompositionSnapshotDetail`의 Snapshot 헤더·lineage 필드를 조합하되 중첩 `items`는 포함하지 않는다. 실제 Snapshot Item 해석 결과는 최상위 `items` 한 곳에서만 제공하며, 각 Item마다 다음 정보를 함께 resolve해 Frontend fan-out을 제한한다.

| 영역 | 최소 필드 | 기준 |
|---|---|---|
| Snapshot Item | `snapshot_item_id`, `item_role`, `sort_order` | 기존 `SnapshotItemDetail` 재사용 |
| AssetVersion | `asset_version_id`, `asset_id`, `version_number`, `version_origin`, `parent_asset_version_id`, `processing_chain_id`, `provider_id`, `model_manifest_id`, `settings_snapshot`, `created_at` | 기존 `AssetVersionDetail` 재사용 |
| Artifact reference | `artifact_id`, `asset_version_id`, `artifact_kind`, `media_type`, `size_bytes`, checksum, `retention_status`, 허용된 `content_url`·`download_url` | 기존 안전한 `ArtifactDetail` 기반 |
| Track projection | `projection_id`, `identity_scope`, `snapshot_item_id`, `item_role`, `sort_order`, `asset_id`, `asset_version_id` | D1 product read projection |

중복 AssetVersion 또는 Artifact는 구현에서 결정적 순서로 한 번만 resolve할 수 있지만 response 의미와 ID는 바꾸지 않는다. 응답은 ORM relationship과 내부 Owner ID를 직접 직렬화하지 않는다.

## 6. Track projection

`music`, `vocal`, `stem`, `mix` Snapshot Item마다 하나의 snapshot-local projection을 만들 수 있다.

```json
{
  "projection_id": "snapshot-item-uuid",
  "identity_scope": "snapshot",
  "snapshot_item_id": "snapshot-item-uuid",
  "item_role": "stem",
  "sort_order": 0,
  "asset_id": "asset-uuid",
  "asset_version_id": "asset-version-uuid"
}
```

`projection_id` 값은 기존 `snapshot_item_id`를 그대로 사용한다. 이것은 `track_id`가 아니며 Snapshot 간 identity, edit target 또는 future canonical Track migration을 보장하지 않는다. `lyrics` Item은 Track projection을 만들지 않는다.

## 7. Section과 Clip

현재 Section으로 승인할 수 있는 canonical ID·time range·구조 metadata가 없다.

```json
{
  "availability": "not_available",
  "items": []
}
```

균등 시간 분할, 파일명, Prompt, 임의 Provider JSON에서 Section을 합성하지 않는다. 검증된 metadata contract가 생기면 versioned 변경으로 optional Section item을 추가한다. Clip은 응답에 포함하지 않으며 D2/D3 이후 DAW editing 범위다.

## 8. Mix와 lineage

`mix_settings_snapshot`은 현재 Snapshot의 bounded JSON object를 그대로 읽는다. D1에서 typed Track Mixer, automation, Volume·Pan·Mute·Solo schema를 만들지 않는다.

다음 identity를 손실 없이 유지한다.

- Project·CompositionSnapshot·SnapshotItem
- Asset·exact AssetVersion·parent AssetVersion
- Artifact
- ProcessingChain
- Provider ID/version과 Model Manifest ID

이 identity는 향후 MusicIntent·Revision·Evaluation·Learning 연결에 사용할 수 있지만 D1은 해당 기능을 실행하지 않는다.

## 9. Empty, transition과 error

| 조건 | 응답 | 의미 |
|---|---|---|
| Workspace 미Bootstrap | `409 WORKSPACE_BOOTSTRAP_REQUIRED` | 기존 오류 재사용, GET에서 bootstrap하지 않음 |
| Project 없음·비활성·다른 Owner | `404 PROJECT_NOT_FOUND` | Legacy 존재 여부를 확인하거나 노출하지 않음 |
| Project에 Snapshot 0개 | `200 state=empty` | 정상적인 빈 Composition, `snapshot=null` |
| Snapshot은 있으나 selection 없음, query 없음 | `200 state=selection_required` | latest fallback 금지 |
| selected 또는 requested Snapshot 해석 성공 | `200 state=ready` | resolved aggregate 반환 |
| requested Snapshot 없음·다른 Project | `404 COMPOSITION_SNAPSHOT_NOT_FOUND` | 기존 privacy 오류 재사용 |
| dangling/cross-Project selected pointer | 안전한 `409` integrity conflict | latest fallback·raw FK 정보 노출 금지 |

`WORKSPACE_EMPTY` 같은 새 오류 enum은 만들지 않는다. bootstrap 부재는 기존 오류를 재사용하고, 유효한 Project의 데이터 부재는 성공 response state로 구분한다. Network·DB 장애를 `empty`로 변환하지 않는다.

## 10. 부작용 금지

Aggregate GET은 다음을 수행하지 않는다.

- Legacy 조회 결과를 Workspace Resource로 합성
- Workspace·Project·Asset·Version·Artifact·Snapshot 생성 또는 변경
- bootstrap·backfill·dual write·read switch
- selected pointer 변경
- Artifact ingestion·파일 이동·Provider 호출

## 11. 기존 API와 구현 순서

기존 Snapshot API 3개는 그대로 유지한다. Aggregate GET은 Frontend read optimization과 Workspace projection이며 Resource API를 대체하지 않는다.

1. **D1-A:** Project selection persistence, aggregate DTO·Repository/Service/Router, empty/requested/selected/privacy 테스트
2. **D1-Transition:** 명시적 bootstrap과 selection authority 조사·무선택 transition inventory — PR #104 squash merge 완료
3. **D1-B:** Project 상세의 Frontend consume·명시 선택·recovery UI — 구현·검증·merge 완료, 실제 사용자 DB Snapshot E2E와 인증 Gate는 보류

D1-A와 D1-Transition은 임시 DB fixture에서 구현·테스트했다. D1-B Frontend는 같은 공개 계약을 fixture로 소비하며 실제 사용자 DB 전환은 별도 승인 Gate 뒤에 수행한다.

## 12. D1-A 구현 상태

`20260820_0018`은 nullable 초기 상태를 유지하는 `project_composition_selections` 1:1 Table을 additive하게 추가한다. `(project_id, selected_composition_snapshot_id)`는 `(composition_snapshots.project_id, composition_snapshot_id)`를 참조하므로 다른 Project의 Snapshot 선택을 DB에서 거부하며 Service도 같은 불변식을 검증한다. 실제 사용자 DB에는 이 revision을 적용하지 않았다.

선택 변경은 `PATCH /api/v1/projects/{project_id}/composition-selection`과 `{ "selected_snapshot_id": "uuid-or-null" }`을 사용한다. 같은 값을 반복 적용해도 동일 상태가 되는 자연 멱등 mutation이며 Snapshot 자체는 변경하지 않는다. `null`은 선택 row를 제거한다.

aggregate Repository는 selection, SnapshotItem, exact AssetVersion·Asset, Artifact를 bounded batch query로 읽는다. Artifact는 공개 metadata와 `/api/v1/artifacts/{artifact_id}/content|download`만 반환하며 storage path·key·locator·credential과 payload에는 접근하지 않는다. SQLite fixture의 대표 조회는 예상 Index를 사용하고 full-table scan, 불필요한 `TEMP B-TREE`, item별 N+1이 없음을 검증했다.

D1-Transition 조사 결과 pre-D1-A project-level selected Snapshot persistence는 `NO_PREEXISTING_SELECTION_AUTHORITY`다. 따라서 Snapshot이 하나 이상인 Project도 selection row를 만들지 않고 `selection_required`를 유지하며, 기존 valid `ProjectCompositionSelection`만 보존한다. Bootstrap 3회 재실행·rollback·restart와 `empty → selection_required → PATCH → ready`를 isolated SQLite에서 검증했다.

D1-A·D1-Transition·D1-B Frontend와 canonical Track·Clip persistence·trusted duration·revision-safe idempotency·WorkingComposition Service/API, Frontend Clip Editing·Clip Copy·Backend persistent Undo/Redo, Working Preview·Composition Commit과 Clip Gain/Fade/Loop Backend/Frontend integration을 구현했다. 실제 사용자 DB `0017 → 0028` migration·Bootstrap·data backfill, 실제 인증 principal, 실제 사용자 Workspace Snapshot E2E, canonical Section과 typed Mixer는 계속 `NOT IMPLEMENTED`다. mutation·Preview 계약은 [WorkingComposition Product API](working-composition-api.md)를 따른다.
