# Asset 중심 목표 Table Definition

> 문서 상태: [진행 중]
> 문서 분류: **TARGET / PARTIALLY IMPLEMENTED**
> 최종 수정일: 2026-09-03
> 관련 기능: DohaMusic Workspace 데이터베이스 재설계
> 구현 상태: Workspace 도메인 Entity/Table 33개·Catalog 1개·Clip persistence·Working Preview·Clip Gain·Fade source revision `0026` 구현
> 미구현 전환: backfill·dual write·Runtime read source 전환·Legacy 제거
> 관련 문서: [재설계 개요](database-redesign-overview.md), [목표 ERD](database-redesign-erd.md), [Migration 전략](database-redesign-migration-strategy.md)

이 문서의 Table은 논리 TARGET이면서 현재 SQLAlchemy metadata와 additive schema에 구현된 물리 구조다. 아직 기존 Runtime 14개의 source of truth를 대체하지 않았으며, nullable staging 강화·데이터 backfill·dual write·read 전환은 [Migration 전략](database-redesign-migration-strategy.md)의 후속 단계다.

## 1. 표기 기준

- 타입은 특정 DB 제품의 SQL이 아닌 논리 타입입니다.
- `UUID`, `string`, `text`, `integer`, `bigint`, `decimal`, `boolean`, `timestamp`, `json`을 사용합니다.
- 모든 시각은 UTC입니다.
- `PK`, `FK`, `Unique`, `Index`는 구현 시 반드시 반영할 논리 제약입니다.
- 불변 Table은 생성 후 내용 변경을 허용하지 않습니다.
- enum 최종 값과 field 길이는 Common Specification의 contract version 확정 후 고정합니다.

## 2. Workspace와 Project

### 2.1 `workspaces`

Workspace는 사용자·Project·권한·Asset·Job·결과를 관리하는 최상위 제품 영역입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `workspace_id` | UUID | 아니요 | PK | Workspace 식별자 |
| `owner_id` | UUID | 아니요 | Index | 외부 사용자·권한 경계의 Owner 식별자 |
| `name` | string | 아니요 |  | 표시 이름 |
| `lifecycle_status` | string | 아니요 | Index | `active`, `archived`, 삭제 lifecycle |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | Metadata 최종 변경 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete 시각 |

Unique 후보는 초기 단일 사용자 강제를 위한 `owner_id` 단독 제약이 아닙니다. 향후 한 Owner가 여러 Workspace를 가질 수 있도록 허용합니다.

### 2.2 `music_projects`

MusicProject는 하나의 음악 작업 단위입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `project_id` | UUID | 아니요 | PK | Project 식별자 |
| `workspace_id` | UUID | 아니요 | FK, Index | `workspaces.workspace_id` |
| `title` | string | 아니요 | Index | Project 제목 |
| `description` | text | 예 |  | 설명 |
| `lifecycle_status` | string | 아니요 | Index | Project lifecycle |
| `created_by` | UUID | 아니요 | Index | 생성 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | Metadata 최종 변경 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete 시각 |

### 2.3 `project_assets`

ProjectAsset는 MusicProject와 Asset의 N:M 연결을 소유합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `project_asset_id` | UUID | 아니요 | PK | 연결 식별자 |
| `project_id` | UUID | 아니요 | FK, Index | `music_projects.project_id` |
| `asset_id` | UUID | 아니요 | FK, Index | `assets.asset_id` |
| `role` | string | 예 | Index | Project 안 표시·구성 역할 |
| `display_order` | integer | 아니요 |  | Project 안 표시 순서 |
| `created_at` | timestamp | 아니요 |  | 연결 생성 시각 |
| `deleted_at` | timestamp | 예 | Index | 연결 해제 시각 |

`(project_id, asset_id)`는 Unique입니다. 다시 연결할 때 새 중복 row를 만들지 않고 기존 Soft Delete row를 복원합니다.

## 3. Asset와 Artifact

### 3.1 `assets`

Asset은 실제 파일이 아닌 논리 작품 객체입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `asset_id` | UUID | 아니요 | PK | Asset 식별자 |
| `workspace_id` | UUID | 예 | FK, Index | 자산을 소유하는 Workspace 식별자. 저장소별 단일 Workspace 계약에서는 생략 가능 |
| `owner_id` | UUID | 아니요 | Index | 사용자 또는 Workspace Owner 식별자 |
| `asset_type` | string | 아니요 | Index | `lyrics`, `music`, `vocal`, `stem`, `recording`, `mix`, `export` |
| `selected_asset_version_id` | UUID | 예 | FK, Unique | 현재 Selection인 `asset_versions.asset_version_id` |
| `lifecycle_status` | string | 아니요 | Index | `draft`, `active`, `archived`, 삭제 lifecycle |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | 변경 가능한 Metadata 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete 시각 |

`selected_asset_version_id`는 같은 Asset의 Version만 허용합니다. 이 교차 row 규칙은 Service transaction에서 검증합니다.

### 3.2 `asset_versions`

AssetVersion은 Asset의 불변 상태와 계보입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `asset_version_id` | UUID | 아니요 | PK | 불변 Version 식별자 |
| `asset_id` | UUID | 아니요 | FK, Index | `assets.asset_id` |
| `version_number` | integer | 아니요 | Unique 조합 | Asset 내부 단조 증가 번호 |
| `version_origin` | string | 아니요 | Index | Common Specification의 Version 생성 원인 |
| `parent_asset_version_id` | UUID | 예 | FK, Index | 같은 Asset의 직접 이전 Version |
| `processing_chain_id` | UUID | 예 | FK, Index | 적용된 `processing_chains.processing_chain_id` |
| `provider_id` | string | 예 | Index | 생성 Provider. 사용자 작성이면 없음 |
| `model_manifest_id` | string | 예 | Index | 사용 Model Manifest. 모델 미사용이면 없음 |
| `settings_snapshot` | json | 아니요 |  | 생성 시점 불변 설정 |
| `created_by` | UUID | 아니요 | Index | 사용자 또는 시스템 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`(asset_id, version_number)`는 Unique입니다. `version_number`는 감소·재사용하지 않으며 row의 UPDATE와 직접 DELETE를 금지합니다.

### 3.3 `artifacts`

Artifact는 실제 파일 또는 직렬화된 Payload의 식별·무결성 Metadata입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `artifact_id` | UUID | 아니요 | PK | 저장 위치와 독립적인 Artifact 식별자 |
| `asset_version_id` | UUID | 아니요 | FK, Index | 소유 `asset_versions.asset_version_id` |
| `artifact_kind` | string | 아니요 | Index | Artifact 논리 유형 |
| `media_type` | string | 아니요 | Index | MIME 또는 직렬화 형식 |
| `duration_us` | bigint | 예 | Check | trusted ingestion이 Payload에서 계산한 양의 media duration μs; 미검증·미지원은 `NULL` |
| `size_bytes` | bigint | 아니요 |  | Payload 크기 |
| `checksum_algorithm` | string | 아니요 |  | 기본 `sha256` |
| `artifact_checksum` | string | 아니요 | Index | Payload checksum |
| `producer_type` | string | 아니요 | Index | `user`, `provider`, `workspace`, `import` |
| `producer_id` | string | 예 | Index | Provider 또는 actor 식별자 |
| `run_id` | string | 예 | Index | Job·Training·Evaluation Run 식별자 |
| `retention_status` | string | 아니요 | Index | 보존·삭제 lifecycle |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

절대 경로와 상대 경로 field를 두지 않습니다. 같은 `artifact_id`의 Payload는 덮어쓰지 않습니다. `(checksum_algorithm, artifact_checksum, size_bytes)`는 중복 탐지 Index이며 자동 병합 Unique로 사용하지 않습니다.

### 3.4 `artifact_storage_locations`

ArtifactStorageLocation은 Artifact ID와 승인된 Storage root 내부 locator를 1:1로 연결하는 내부 운영 Entity입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `storage_location_id` | UUID | 아니요 | PK | Catalog row 식별자 |
| `artifact_id` | UUID | 아니요 | FK, Unique | `artifacts.artifact_id`, 삭제 `RESTRICT` |
| `storage_backend` | string | 아니요 | Unique 조합 | 초기 지원값 `local`; 비어 있지 않음 |
| `storage_domain` | string | 아니요 | Unique 조합, Check | `lm`, `audio`, `vocal`, `music` |
| `storage_key` | string | 아니요 | Unique 조합 | domain root 기준 canonical 상대 key |
| `locator_version` | integer | 아니요 | Check | 1 이상의 locator 계약 version |
| `published_at` | timestamp | 아니요 |  | 불변 Payload publish 완료 시각 |
| `created_at` | timestamp | 아니요 |  | Catalog row 생성 시각 |

`(storage_backend, storage_domain, storage_key)`는 Unique입니다. 절대·상대 path column을 두지 않으며 DB는 최소 불변 조건만 강제합니다. 구현된 local Resolver가 traversal·symlink·junction·reparse·root containment와 canonical key를 read boundary에서 재검증합니다.

### 3.5 `asset_relations`

AssetRelation은 부모·파생·Stem·Voice Conversion 등 의미 관계를 저장합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `relation_id` | UUID | 아니요 | PK | 관계 식별자 |
| `source_asset_id` | UUID | 예 | FK, Index | source `assets.asset_id` |
| `target_asset_id` | UUID | 예 | FK, Index | target `assets.asset_id` |
| `source_asset_version_id` | UUID | 예 | FK, Index | source `asset_versions.asset_version_id` |
| `target_asset_version_id` | UUID | 예 | FK, Index | target `asset_versions.asset_version_id` |
| `relation_type` | string | 아니요 | Index | versioned 관계 유형 |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

Asset 쌍 또는 AssetVersion 쌍 중 하나만 완성하는 Check가 필요합니다. source와 target이 같은 자기 관계, 순환 파생과 완전히 같은 중복 관계를 금지합니다.

## 4. Composition Snapshot과 Processing

### 4.1 `composition_snapshots`

CompositionSnapshot은 Project의 정확한 작품 구성을 고정한 불변 객체입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `composition_snapshot_id` | UUID | 아니요 | PK | Snapshot 식별자 |
| `project_id` | UUID | 아니요 | FK, Index | `music_projects.project_id` |
| `snapshot_version` | integer | 아니요 | Unique 조합 | Project 내부 단조 증가 번호 |
| `processing_chain_id` | UUID | 예 | FK, Index | 대표 Processing Chain |
| `mix_settings_snapshot` | json | 아니요 |  | 생성 시점 Mix 설정 |
| `provider_versions` | json | 아니요 |  | Provider와 contract version snapshot |
| `model_manifest_ids` | json | 아니요 |  | Model Manifest ID와 model version snapshot |
| `created_by` | UUID | 아니요 | Index | 생성 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`(project_id, snapshot_version)`는 Unique입니다. 생성 후 UPDATE와 DELETE를 금지합니다.

### 4.2 `project_composition_selections`

Project별 명시적 current Snapshot을 저장하는 별도 1:1 상태입니다. row가 없으면 선택이 없는 초기 상태이며 Snapshot 자체는 변경하지 않습니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `project_id` | UUID | 아니요 | PK, FK | `music_projects.project_id` |
| `selected_composition_snapshot_id` | UUID | 아니요 | Unique, 복합 FK | 선택된 불변 Snapshot |
| `created_at` | timestamp | 아니요 |  | 최초 선택 시각 |
| `updated_at` | timestamp | 아니요 |  | 마지막 선택 변경 시각 |

`(project_id, selected_composition_snapshot_id)` 복합 FK는 `composition_snapshots(project_id, composition_snapshot_id)`를 참조해 same-Project 불변식을 강제합니다. Project 삭제는 `CASCADE`, Snapshot 삭제는 `RESTRICT`이며 source revision `20260820_0018`에만 존재하고 실제 사용자 DB에는 아직 적용하지 않았습니다.

### 4.3 `snapshot_items`

SnapshotItem은 Snapshot 안의 역할별 정확한 AssetVersion을 고정합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `snapshot_item_id` | UUID | 아니요 | PK | Item 식별자 |
| `composition_snapshot_id` | UUID | 아니요 | FK, Index | `composition_snapshots.composition_snapshot_id` |
| `asset_version_id` | UUID | 아니요 | FK, Index | 고정된 `asset_versions.asset_version_id` |
| `item_role` | string | 아니요 | Index | Lyrics, Music, Vocal, Stem, Mix 역할 |
| `sort_order` | integer | 아니요 |  | 같은 역할 안 순서 |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`(composition_snapshot_id, item_role, sort_order)`와 `(composition_snapshot_id, asset_version_id, item_role)`는 Unique입니다. Snapshot과 함께 불변입니다.

### 4.4 `working_compositions`

Project마다 하나인 mutable draft authority입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `working_composition_id` | UUID | 아니요 | PK | 서버 발급 draft identity |
| `project_id` | UUID | 아니요 | FK, Unique | `music_projects.project_id` |
| `base_composition_snapshot_id` | UUID | 예 | 복합 FK, Index | 같은 Project의 base Snapshot |
| `mix_settings` | json | 아니요 |  | Repository에서 8,192 UTF-8 bytes로 제한한 설정 object |
| `revision` | integer | 아니요 | Check | 0 이상 optimistic concurrency token |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | 최종 mutation 시각 |

`(project_id, base_composition_snapshot_id)` 복합 FK가 same-Project를 보장합니다. Repository의 expected revision update는 일치할 때만 정확히 1 증가하며 commit/rollback은 호출하지 않습니다.

### 4.5 `composition_tracks`

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `track_id` | UUID | 아니요 | PK | canonical Track identity |
| `working_composition_id` | UUID | 아니요 | FK, Unique 조합 | 소유 WorkingComposition |
| `track_type` | string | 아니요 | Check | V1 allowlist `audio` |
| `name` | string | 아니요 |  | 표시 이름 |
| `track_order` | integer | 아니요 | Check, active Unique | 0 이상 Track 순서 |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | 수정 시각 |
| `deleted_at` | timestamp | 예 | Index | tombstone 시각 |

active row의 `(working_composition_id, track_order)`는 partial Unique입니다. canonical 조회는 `(track_order, track_id)`입니다.

### 4.6 `composition_clips`

시간값은 모두 exact integer microseconds입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `clip_id` | UUID | 아니요 | PK | canonical Clip identity |
| `working_composition_id` | UUID | 아니요 | FK, 복합 FK | 소유·same-composition key |
| `track_id` | UUID | 아니요 | 복합 FK | 같은 WorkingComposition의 Track |
| `source_asset_version_id` | UUID | 아니요 | FK, Index | exact source AssetVersion |
| `timeline_start` | bigint | 아니요 | Check | 0 이상 Timeline 시작 μs |
| `source_in` | bigint | 아니요 | Check | 0 이상 source 시작 μs |
| `source_out` | bigint | 아니요 | Check | `source_in`보다 큰 끝 μs |
| `source_duration` | bigint | 아니요 | Check | 양수이며 `source_out` 이상인 고정 source 길이 μs |
| `timeline_duration` | bigint | 아니요 | Check | 양수인 canonical Timeline 길이 μs; Loop off에서는 source span과 동일 |
| `loop_enabled` | boolean | 아니요 | Check | 기본 false인 반복 상태 |
| `loop_phase` | bigint | 아니요 | Check | source window 내부 canonical phase μs |
| `gain_db` | decimal(5,2) | 아니요 | Check | 기본 `0.00`, `-24.00..+24.00 dB` static Clip Gain |
| `fade_in` | bigint | 아니요 | Check | 기본 0, Clip-relative Fade-in μs |
| `fade_out` | bigint | 아니요 | Check | 기본 0, `fade_in + fade_out <= timeline_duration` |
| `split_from_clip_id` | UUID | 예 | same-composition FK, Index | immediate split parent identity |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | 수정 시각 |
| `deleted_at` | timestamp | 예 | Index | tombstone 시각 |

같은 Track active overlap은 Repository의 반개구간 helper가 거부하고 인접 `[end == start]`는 허용합니다. trusted ingestion duration과 exact-one active audio Artifact 조회 기반은 구현했다. 최종 mutation race 방어와 Asset 활성·Owner·Workspace·ProjectAsset eligibility는 후속 Service가 transaction 안에서 강제해야 합니다.

### 4.7 `composition_snapshot_tracks`

불변 Snapshot의 frozen Track arrangement입니다. `(snapshot, canonical_track_id)`와 `(snapshot, track_order)`가 Unique이며 mutable Track을 FK로 참조하지 않습니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `snapshot_track_id` | UUID | 아니요 | PK, snapshot-local Unique | frozen row identity |
| `composition_snapshot_id` | UUID | 아니요 | FK | 불변 Snapshot |
| `canonical_track_id` | UUID | 아니요 | Unique 조합 | 복사된 lineage identity |
| `track_type` | string | 아니요 | Check | frozen `audio` |
| `name` | string | 아니요 |  | frozen 이름 |
| `track_order` | integer | 아니요 | Check, Unique 조합 | frozen 순서 |

### 4.8 `composition_snapshot_clips`

불변 Snapshot의 frozen Clip arrangement이며 mutable Clip을 FK로 참조하지 않습니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `snapshot_clip_id` | UUID | 아니요 | PK | frozen row identity |
| `composition_snapshot_id` | UUID | 아니요 | FK, 복합 FK | 소유 Snapshot |
| `snapshot_track_id` | UUID | 아니요 | 복합 FK | 같은 Snapshot의 Track |
| `canonical_clip_id` | UUID | 아니요 | Unique 조합 | 복사된 Clip lineage |
| `source_asset_version_id` | UUID | 아니요 | FK, Index | exact source AssetVersion |
| `timeline_start` | bigint | 아니요 | Check | frozen Timeline μs |
| `source_in` | bigint | 아니요 | Check | frozen source 시작 μs |
| `source_out` | bigint | 아니요 | Check | frozen source 끝 μs |
| `source_duration` | bigint | 아니요 | Check | frozen source 길이 μs |
| `gain_db` | decimal(5,2) | 아니요 | Check | frozen Clip Gain, `-24.00..+24.00 dB` |
| `fade_in` | bigint | 아니요 | Check | frozen Clip Fade-in μs, 기본 0 |
| `fade_out` | bigint | 아니요 | Check | frozen Clip Fade-out μs, 합은 frozen duration 이하 |
| `split_from_clip_id` | UUID | 예 |  | frozen parent lineage 값 |

### 4.8A Working Preview persistence

additive revision `20260828_0024`는 canonical Snapshot과 분리된 Preview lifecycle table 네 개를 추가한다.

| Table | 핵심 필드 | 불변식 |
|---|---|---|
| `working_preview_assets` | `project_id` PK, `asset_id` Unique FK | Project당 non-canonical Preview Asset 하나, 일반 ProjectAsset binding 없음 |
| `working_preview_renders` | render ID, Project·WorkingComposition·revision·Job·Preview Asset, nullable completion AssetVersion, payload expiry | Job당 manifest 하나, 성공 AssetVersion당 render 하나 |
| `working_preview_render_tracks` | render ID, canonical Track ID, order | render 안 Track identity/order Unique |
| `working_preview_render_clips` | render/Clip/Track, canonical order, exact source AssetVersion·Artifact, source/timeline μs, `timeline_duration_us`, `loop_enabled`, `loop_phase_us`, Gain·Fade | >16 Clip 손실 없는 schema 5 exact pin, same-render Track 복합 FK, geometry·Gain·Fade Check |

`preview_asset_version_id`는 render 성공 전만 nullable이다. 성공 transaction이 새 immutable AssetVersion·Artifact·JobOutput과 함께 연결한다. `artifacts.asset_version_id NOT NULL`은 변경하지 않는다. `payload_expires_at`은 payload retention scan 근거이며 AssetVersion·manifest provenance 삭제 시각이 아니다. 실제 사용자 DB 적용과 backfill은 없다.

additive revision `20260830_0025`는 `composition_clips`, `composition_snapshot_clips`, `working_preview_render_clips`에 non-null `gain_db`, server default `0.00`과 inclusive 범위 CHECK를 추가한다. table 수는 48개로 유지되며 실제 사용자 DB에는 적용하지 않았다.

additive revision `20260903_0026`은 같은 세 table에 non-null integer Fade column, server default 0과 non-negative·합계 duration 범위 CHECK를 추가한다. table 수는 48개로 유지되며 실제 사용자 DB에는 적용하지 않았다.

additive revision `20260905_0027`은 같은 세 table에 canonical timeline duration, Loop enable과 phase를 추가하고 기존 row를 source span, loop false, phase 0으로 deterministic backfill한다. Fade CHECK authority는 timeline duration으로 교체하며 table 수는 48개로 유지되고 실제 사용자 DB에는 적용하지 않았다.

additive revision `20260905_0028`은 `working_composition_history_states`와 `working_composition_history_entries`를 추가해 persisted cursor와 immutable before/after command journal을 저장한다. source metadata는 50개 table이며 실제 사용자 DB에는 적용하지 않았다.

### 4.9 `processing_chains`

ProcessingChain은 순서가 있는 처리 정의입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `processing_chain_id` | UUID | 아니요 | PK | Chain 식별자 |
| `name` | string | 아니요 |  | 표시 이름 |
| `chain_version` | string | 아니요 | Unique 조합 | Chain version |
| `chain_checksum` | string | 아니요 | Unique | 단계·설정 전체 checksum |
| `created_by` | UUID | 아니요 | Index | 생성 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`(name, chain_version)`은 Unique입니다. 사용된 Chain은 수정하지 않고 새 version을 만듭니다.

### 4.10 `processing_steps`

ProcessingStep은 Chain 안의 개별 처리 단계입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `processing_step_id` | UUID | 아니요 | PK | Step 식별자 |
| `processing_chain_id` | UUID | 아니요 | FK, Index | `processing_chains.processing_chain_id` |
| `step_order` | integer | 아니요 | Unique 조합 | 1부터 증가하는 실행 순서 |
| `step_type` | string | 아니요 | Index | Noise, Pitch, Timing, Normalize, AutoTune, Voice Conversion 등 |
| `settings_snapshot` | json | 아니요 |  | 단계별 불변 설정 |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`(processing_chain_id, step_order)`는 Unique입니다. Chain과 함께 불변입니다.

## 5. Job과 ModelUsage

### 5.1 `jobs`

Job은 Provider 또는 Workspace가 수행하는 독립 비동기 실행 단위입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `job_id` | UUID | 아니요 | PK | Job 식별자 |
| `project_id` | UUID | 아니요 | FK, Index | `music_projects.project_id` |
| `workspace_id` | UUID | 예 | FK | nullable staging. 새 Job은 필수로 기록하며 기존 row는 Project에서 backfill |
| `composition_snapshot_id` | UUID | 예 | FK, Index | 실행 문맥 Snapshot |
| `job_type` | string | 아니요 | Index | `lyrics_generation`, `music_generation`, `stem_separation`, `voice_conversion`, `audio_analysis`, `mix`, `export` |
| `status` | string | 아니요 | Index | `queued`, `running`, `succeeded`, `failed`, `cancelled` |
| `provider_id` | string | 예 | Index | 실행 Provider 또는 Workspace component |
| `api_contract_version` | string | 아니요 | Index | 사용한 계약 version |
| `model_manifest_id` | string | 예 | Index | 대표 실행 Model Manifest. 모델 미사용이면 없음 |
| `progress_percent` | decimal | 예 |  | 0~100 진행률 |
| `stage` | string | 예 | Index | 현재 안전한 처리 단계 |
| `settings_snapshot` | json | 아니요 |  | 실행 시점 설정 |
| `retry_of_job_id` | UUID | 예 | FK, Index | 원본 실패·취소 Job |
| `error_code` | string | 예 | Index | 구조화된 안전 오류 코드 |
| `error_message` | text | 예 |  | 한국어 사용자 메시지 또는 지역화 key |
| `error_retryable` | boolean | 예 |  | 재시도 가능 여부 |
| `error_details_id` | string | 예 | Index | 비공개 상세 진단 참조 |
| `requested_by` | UUID | 아니요 | Index | 요청 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `started_at` | timestamp | 예 |  | 실행 시작 시각 |
| `completed_at` | timestamp | 예 |  | 종료 시각 |
| `cancel_requested_at` | timestamp | 예 |  | 공개 상태와 분리한 내부 취소 요청 시각 |
| `claim_token` | UUID | 예 |  | Worker claim fencing token |
| `claimed_by` | string(128) | 예 |  | 길이가 제한된 Worker 식별자 |
| `lease_expires_at` | timestamp | 예 |  | claim lease 만료 시각 |
| `heartbeat_at` | timestamp | 예 |  | 마지막 heartbeat 시각 |
| `attempt` | integer | 아니요 | Check | 같은 Job의 실행 시도, 기본 0·음수 금지 |

종료 상태는 다른 상태로 되돌리지 않습니다. Retry는 새 row입니다. 기존 Index 외에 Workspace 목록용 4개 keyset Index와 claim queue·lease recovery Index를 둡니다. `workspace_id`는 실제 DB 검증과 전환 전 nullable staging이지만 논리 계약과 새 Job 생성에서는 필수·불변입니다.

### 5.2 `job_inputs`

JobInput은 실행 입력 Version 또는 직접 입력 Artifact를 연결합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `job_input_id` | UUID | 아니요 | PK | 입력 연결 식별자 |
| `job_id` | UUID | 아니요 | FK, Index | `jobs.job_id` |
| `asset_version_id` | UUID | 예 | FK, Index | 입력 `asset_versions.asset_version_id` |
| `artifact_id` | UUID | 예 | FK, Index | 직접 입력 `artifacts.artifact_id` |
| `input_role` | string(64) | 예 |  | nullable staging. Job type Matrix의 입력 역할 |
| `input_order` | integer | 아니요 | Unique 조합 | 입력 순서 |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |

`asset_version_id`, `artifact_id` 중 정확히 하나만 필요합니다. `(job_id, input_order)`는 Unique이며 row는 불변입니다.

### 5.3 `job_outputs`

JobOutput은 성공 후 등록된 출력 Version 또는 Artifact를 연결합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `job_output_id` | UUID | 아니요 | PK | 출력 연결 식별자 |
| `job_id` | UUID | 아니요 | FK, Index | `jobs.job_id` |
| `asset_version_id` | UUID | 예 | FK, Index | 출력 `asset_versions.asset_version_id` |
| `artifact_id` | UUID | 예 | FK, Index | 출력 `artifacts.artifact_id` |
| `output_role` | string(64) | 예 |  | nullable staging. Job type Matrix의 출력 역할 |
| `output_order` | integer | 아니요 | Unique 조합 | 출력 순서 |
| `created_at` | timestamp | 아니요 |  | 등록 시각 |

`asset_version_id`, `artifact_id` 중 정확히 하나만 필요합니다. `(job_id, output_order)`는 Unique이며 `succeeded` 확정 전에 Artifact checksum을 검증합니다.

### 5.4 `model_usages`

ModelUsage는 실제 실행 Model과 권리 계보를 Job 결과에 연결합니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `model_usage_id` | UUID | 아니요 | PK | 사용 기록 식별자 |
| `job_id` | UUID | 아니요 | FK, Index | `jobs.job_id` |
| `asset_version_id` | UUID | 예 | FK, Index | 결과 `asset_versions.asset_version_id` |
| `provider_id` | string | 아니요 | Index | Provider 식별자 |
| `model_manifest_id` | string | 아니요 | Index | Model Manifest 식별자 |
| `model_id` | string | 아니요 | Index | Model 논리 식별자 |
| `model_version` | string | 아니요 |  | Model version |
| `checkpoint_version` | string | 예 |  | Checkpoint version |
| `api_contract_version` | string | 아니요 | Index | Provider 계약 version |
| `license_status` | string | 아니요 | Index | License 검토 상태 |
| `commercial_usage_status` | string | 아니요 | Index | 상업 이용 검토 상태 |
| `created_at` | timestamp | 아니요 |  | 기록 시각 |

`(job_id, model_manifest_id, asset_version_id)`는 중복 방지 Unique 후보입니다. ModelUsage는 게시 후 불변입니다.

### 5.5 `provider_job_bindings`

ProviderJobBinding은 Workspace Job과 Provider 실행 identity의 1:N 불변 이력입니다. Provider 상태는 저장하지 않습니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `provider_job_binding_id` | UUID | 아니요 | PK | binding 식별자 |
| `workspace_job_id` | UUID | 아니요 | FK, Index | `jobs.job_id`, 삭제 `RESTRICT` |
| `provider_id` | string(128) | 아니요 | Unique 조합 | Provider namespace |
| `provider_job_id` | string(256) | 아니요 | Unique 조합 | opaque Provider Job ID |
| `retry_of_provider_job_id` | string(256) | 예 | composite self FK | 같은 Provider의 retry parent |
| `created_at` | timestamp | 아니요 | history Index | 기록 시각 |

`(provider_id, provider_job_id)`는 Unique이고 self retry는 CHECK로 금지합니다. `(workspace_job_id, created_at, provider_job_binding_id)`는 history/latest recovery Index입니다. identity update·일반 delete API는 제공하지 않습니다.

## 6. Enrollment, Approval과 Workspace Metadata

### 6.1 `recording_enrollments`

RecordingEnrollment는 작품 Recording과 분리된 음색 등록·참조 승인 과정입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `recording_enrollment_id` | UUID | 아니요 | PK | Enrollment 식별자 |
| `workspace_id` | UUID | 아니요 | FK, Index | `workspaces.workspace_id` |
| `recording_asset_version_id` | UUID | 아니요 | FK, Index | 승인된 Recording AssetVersion |
| `status` | string | 아니요 | Index | Enrollment lifecycle |
| `consent_policy_version` | string | 아니요 | Index | 동의문 version |
| `consent_evidence_id` | string | 아니요 | Index | 접근 제어된 동의 증적 식별자 |
| `created_by` | UUID | 아니요 | Index | 등록 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `completed_at` | timestamp | 예 |  | 완료 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete·철회 요청 시각 |

작품 Recording, Enrollment와 Training Dataset은 서로 다른 객체입니다. `(workspace_id, recording_asset_version_id, consent_policy_version)`는 중복 등록 방지 Unique입니다.

### 6.2 `approvals`

Approval은 대상과 목적별 승인 판단·근거를 보존하는 불변 이벤트입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `approval_id` | UUID | 아니요 | PK | 승인 이벤트 식별자 |
| `asset_version_id` | UUID | 예 | FK, Index | 승인 대상 AssetVersion |
| `recording_enrollment_id` | UUID | 예 | FK, Index | 승인 대상 RecordingEnrollment |
| `model_usage_id` | UUID | 예 | FK, Index | 승인 대상 ModelUsage |
| `usage_purpose` | string | 아니요 | Index | 최종 사용·음성 사용·상업 이용 등 목적 |
| `status` | string | 아니요 | Index | 검토·승인·거절·철회 상태 |
| `approved_by` | UUID | 아니요 | Index | 결정 actor |
| `evidence_id` | string | 아니요 | Index | 근거 식별자 |
| `decided_at` | timestamp | 아니요 | Index | 결정 시각 |
| `created_at` | timestamp | 아니요 |  | 기록 시각 |

세 대상 FK 중 정확히 하나만 필요합니다. 판단 변경과 철회는 기존 row UPDATE가 아니라 새 Approval 이벤트로 기록합니다.

### 6.3 `tags`

Tag는 Asset 분류 Metadata입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `tag_id` | UUID | 아니요 | PK | Tag 식별자 |
| `asset_id` | UUID | 아니요 | FK, Index | `assets.asset_id` |
| `name` | string | 아니요 | Index | 정규화된 Tag 이름 |
| `created_by` | UUID | 아니요 | Index | 생성 actor |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete 시각 |

`(asset_id, name)`은 Unique이며 다시 추가할 때 기존 row를 복원합니다.

### 6.4 `comments`

Comment는 특정 불변 AssetVersion에 대한 의견입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `comment_id` | UUID | 아니요 | PK | Comment 식별자 |
| `asset_version_id` | UUID | 아니요 | FK, Index | `asset_versions.asset_version_id` |
| `created_by` | UUID | 아니요 | Index | 작성 actor |
| `body` | text | 아니요 |  | Comment 본문 |
| `created_at` | timestamp | 아니요 | Index | 생성 시각 |
| `updated_at` | timestamp | 아니요 |  | 본문 수정 시각 |
| `deleted_at` | timestamp | 예 | Index | Soft Delete 시각 |

`(asset_version_id, created_at)`을 조회 Index로 둡니다. Comment 수정은 History에 기록합니다.

### 6.5 `favorites`

Favorite는 Workspace와 Asset의 선호 연결입니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `favorite_id` | UUID | 아니요 | PK | Favorite 식별자 |
| `workspace_id` | UUID | 아니요 | FK, Index | `workspaces.workspace_id` |
| `asset_id` | UUID | 아니요 | FK, Index | `assets.asset_id` |
| `created_at` | timestamp | 아니요 |  | 생성 시각 |
| `deleted_at` | timestamp | 예 | Index | 해제 시각 |

`(workspace_id, asset_id)`는 Unique이며 다시 선택할 때 기존 row를 복원합니다.

### 6.6 `history`

History는 별도 감사 Entity이며 현재 상태를 재구성하는 원본 Table로 사용하지 않습니다.

| Field | Type | Null | Key | 설명 |
|---|---|---:|---|---|
| `history_id` | UUID | 아니요 | PK | History 식별자 |
| `workspace_id` | UUID | 아니요 | FK, Index | `workspaces.workspace_id` |
| `actor_id` | UUID | 아니요 | Index | 행위 actor |
| `entity_type` | string | 아니요 | Index | allowlist 대상 Entity 유형 |
| `entity_id` | UUID | 아니요 | Index | 대상 식별자 |
| `action` | string | 아니요 | Index | 생성·Selection·승인·삭제 요청 등 |
| `before_snapshot` | json | 예 |  | 민감정보를 제거한 변경 전 Metadata |
| `after_snapshot` | json | 예 |  | 민감정보를 제거한 변경 후 Metadata |
| `created_at` | timestamp | 아니요 | Index | 발생 시각 |

`(workspace_id, created_at)`과 `(entity_type, entity_id, created_at)`을 복합 Index로 둡니다. History는 append-only이며 절대 경로, 비밀정보, 개인 음성 Payload와 동의 증적 원문을 저장하지 않습니다.

## 7. 공통 FK 삭제 동작

| 관계 | 삭제 동작 |
|---|---|
| Workspace → MusicProject | 물리 삭제 제한, Soft Delete 사용 |
| MusicProject → ProjectAsset·Snapshot·Job | 물리 삭제 제한 |
| Asset → AssetVersion | 물리 삭제 제한 |
| AssetVersion → Artifact·SnapshotItem·JobInput/Output·ModelUsage | 물리 삭제 제한 |
| Artifact → ArtifactStorageLocation | 1:1, 물리 삭제 제한 |
| Job → Input·Output·ModelUsage·ProviderJobBinding | 물리 삭제 제한 |
| ProcessingChain → Step·Version·Snapshot | 물리 삭제 제한 |
| Project·Snapshot → WorkingComposition | 물리 삭제 제한, same-Project 복합 FK |
| WorkingComposition → Track·Clip | 물리 삭제 제한, Track·Clip은 tombstone |
| Track·Clip → Clip ownership·split lineage | 물리 삭제 제한 |
| Snapshot → SnapshotTrack·SnapshotClip | 물리 삭제 제한, committed history 보존 |
| RecordingEnrollment·ModelUsage → Approval | 물리 삭제 제한 |

불변·감사 근거에 `CASCADE DELETE`를 사용하지 않습니다. 구현 전에 보존 기간과 개인정보 삭제 의무가 충돌하는 사례를 별도 검증해야 합니다.

## 8. 필수 Unique와 Index 요약

| Table | Unique | 주요 Index |
|---|---|---|
| `project_assets` | `(project_id, asset_id)` | `project_id`, `asset_id`, `deleted_at` |
| `asset_versions` | `(asset_id, version_number)` | `parent_asset_version_id`, `version_origin` |
| `composition_snapshots` | `(project_id, snapshot_version)`, `(project_id, composition_snapshot_id)` | `project_id`, `created_at` |
| `project_composition_selections` | `selected_composition_snapshot_id` | PK `project_id`, same-Project 복합 FK |
| `snapshot_items` | `(composition_snapshot_id, item_role, sort_order)` | `asset_version_id` |
| `working_compositions` | `project_id` | `base_composition_snapshot_id` |
| `composition_tracks` | `(working_composition_id, track_id)`, active `(working_composition_id, track_order)` | active order composite |
| `composition_clips` | `(working_composition_id, clip_id)` | active Timeline, source AssetVersion, split parent |
| `composition_snapshot_tracks` | snapshot-local ID·canonical ID·order | deterministic frozen order |
| `composition_snapshot_clips` | `(snapshot_id, canonical_clip_id)` | frozen Timeline, source AssetVersion |
| `working_preview_assets` | `project_id`, `asset_id` | Project Preview Asset 1:1 binding |
| `working_preview_renders` | `workspace_job_id`, `preview_asset_version_id` | working/revision와 payload expiry |
| `working_preview_render_tracks` | `(render_id, track_id)`, `(render_id, track_order)` | immutable ordered manifest |
| `working_preview_render_clips` | `(render_id, clip_id)`, `(render_id, canonical_order)` | exact Artifact·geometry manifest |
| `processing_chains` | `(name, chain_version)`, `chain_checksum` | `created_by` |
| `processing_steps` | `(processing_chain_id, step_order)` | `step_type` |
| `job_inputs` | `(job_id, input_order)` | `asset_version_id`, `artifact_id` |
| `job_outputs` | `(job_id, output_order)` | `asset_version_id`, `artifact_id` |
| `tags` | `(asset_id, name)` | `name`, `deleted_at` |
| `favorites` | `(workspace_id, asset_id)` | `workspace_id`, `deleted_at` |
| `jobs` | 없음 | 기존 Index, Workspace keyset 4개, claim queue·lease recovery 2개 |
| `artifacts` | Artifact ID | checksum 조합, `retention_status`, `run_id` |
| `artifact_storage_locations` | `artifact_id`, `(storage_backend, storage_domain, storage_key)` | Unique 제약으로 역조회 지원, 별도 Index 없음 |

## 9. source of truth 전환 전 남은 검증 항목

- 순환 FK인 Asset Selection 생성 순서와 deferred constraint 지원 여부
- SQLite와 향후 운영 DB에서 Check·partial unique 동등성
- 불변 row의 UPDATE 차단을 Service, Repository 또는 DB 중 어디서 강제할지
- `json` field의 JSON Schema와 versioning
- History의 논리 참조 무결성과 개인정보 최소화
- `[완료]` 실제 사용자 DB `20260808_0015 → 20260809_0016` 안전 적용과 복구 Gate
- 구현된 Catalog Resolver를 사용하는 API·Worker 전환 순서와 Legacy 경로 제거 Gate
- persisted trusted duration을 사용하는 Owner·ProjectAsset·audio eligibility mutation Service
- same-Track overlap의 최종 mutation transaction race 방어와 public API idempotency orchestration은 WorkingComposition Service에서 구현
