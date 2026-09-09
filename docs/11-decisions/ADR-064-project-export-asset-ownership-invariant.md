# ADR-064: Project Export Asset Ownership Invariant

> 상태: 승인
> 작성일: 2026-09-07
> 최종 수정일: 2026-09-07
> 관련 기능: Project Export Asset authority
> 관련 ADR: [ADR-030](ADR-030-asset-version-centric-database.md), [ADR-063](ADR-063-export-asset-lineage-and-completion-authority.md)

## 배경

ADR-063은 Project마다 하나의 user-visible Export Asset을 소유하도록 결정했다. 처음 제안한 `music_projects.export_asset_id -> assets.asset_id`는 identity cardinality만 표현하고, 기존 canonical Project membership entity인 `ProjectAsset`을 우회한다. 또한 단일 Asset FK만으로 role, type, workspace, owner 또는 soft-delete predicate를 보장한다고 주장할 수 없다.

## Source authority

`ProjectAsset`은 durable `project_asset_id` PK, `project_id`, `asset_id`, role, display order와 soft-delete lifecycle을 가진 canonical Project-Asset membership entity다. `(project_id, asset_id)`는 이미 unique하다. `WorkspaceService.attach_asset()`이 Project/Asset scope를 검증하고 관계 생성 또는 soft-delete 복구를 소유한다. role은 자유 문자열이며 service가 변경할 수 있으므로 DB enum invariant가 아니라 domain classification이다.

`AssetType`은 SQLAlchemy enum으로 저장되지만 다른 table의 FK가 특정 enum value를 요구하도록 만들지는 않는다. Project와 Asset은 모두 Workspace identity를 가지며 effective owner는 Workspace/Asset authority에서 검증한다. Foreign key는 soft-deleted row를 구분하지 못한다.

## 후보 평가

- Candidate A, bare Asset FK: ProjectAsset membership을 우회하므로 미선택이다.
- Candidate B, ProjectAsset FK: canonical membership을 직접 가리키므로 선택한다.
- Candidate C, same-Project composite FK: Candidate B에 결합하여 cross-Project relation 참조를 구조적으로 차단한다.
- Candidate D, trigger: repository의 일반 invariant 방식이 아니며 SQLite/PostgreSQL 이중 구현과 숨은 mutation side effect가 필요해 미선택이다.
- Candidate E, partial unique role: role이 자유롭게 변경되고 soft-delete를 포함하므로 ownership identity로 사용하지 않는다.

## 결정

Project Export ownership column은 `music_projects.export_project_asset_id`다. bare Asset ID가 아니라 canonical ProjectAsset membership을 참조한다.

### DB structural invariants

- Project row 하나는 Export ProjectAsset pointer를 최대 하나 가진다.
- `export_project_asset_id`는 전체 Project에서 unique하여 같은 membership을 둘 이상의 Project가 소유할 수 없다.
- composite FK가 referenced ProjectAsset의 `project_id`와 owning MusicProject의 `project_id`가 같음을 보장한다.
- referenced ProjectAsset의 물리 삭제는 `ON DELETE RESTRICT`로 차단한다.
- referenced ProjectAsset과 Asset의 존재는 각 FK가 보장한다.

### Domain service invariants

Authoritative Export completion service는 existing binding을 사용할 때마다 다음을 exact 검증한다.

- ProjectAsset이 active이며 soft-deleted 상태가 아님
- `ProjectAsset.role == "export"`
- 연결 Asset이 active이며 soft-deleted 상태가 아님
- `Asset.asset_type == AssetType.EXPORT`
- Asset Workspace가 Project Workspace와 일치
- Asset owner가 effective owner authority와 일치

Role, type, workspace, owner와 active predicate는 cross-table mutable business predicate이므로 DB가 보장한다고 주장하지 않는다. Authoritative service path를 통하지 않은 arbitrary assignment는 지원되는 domain mutation이 아니다. Generic repository는 commit/rollback을 소유하지 않는 내부 persistence primitive이며 public authority가 아니다.

## 0031 executable schema

Revision `20260907_0031`, down revision `20260907_0030`은 다음 additive schema를 사용한다.

1. `project_assets`에 `UNIQUE(project_id, project_asset_id)`를 추가한다. `project_asset_id` PK와 중복처럼 보이지만 SQLite와 PostgreSQL에서 composite FK의 명시적 parent key가 된다.
2. `music_projects.export_project_asset_id` nullable UUID column을 추가한다.
3. `UNIQUE(export_project_asset_id)`를 추가한다.
4. `(project_id, export_project_asset_id)`에서 `project_assets(project_id, project_asset_id)`로 `ON DELETE RESTRICT` composite FK를 추가한다.

기존 Project 값은 `NULL`이며 placeholder Asset이나 ProjectAsset을 backfill하지 않는다. Downgrade는 composite FK, unique constraint, column과 composite target unique constraint를 역순으로 제거하며 Asset/ProjectAsset row는 삭제하지 않는다.

두 DB 모두 composite foreign key와 unique parent key를 지원한다. SQLite migration은 repository의 batch-table convention과 foreign-key test를 사용하고 PostgreSQL은 named constraints를 사용한다. Partial index나 trigger에 의존하지 않는다.

## First-export concurrency

각 completion attempt는 하나의 transaction에서 candidate Export Asset과 ProjectAsset을 생성한 뒤 `export_project_asset_id IS NULL` 조건부 update를 수행한다.

- Winner는 pointer assignment와 candidate rows를 같은 transaction에서 commit한다.
- Loser는 update row count 0을 확인하면 candidate를 commit하지 않고 transaction 전체를 rollback한다.
- Loser의 새 transaction은 winner relation을 reload하고 모든 domain invariant를 exact 검증한 뒤 재시도한다.
- candidate Asset이나 ProjectAsset만 남는 partial commit은 허용하지 않는다.

이미 pointer가 있으면 새 candidate를 만들지 않는다. mismatch는 repair, reassignment 또는 fallback 없이 fail closed한다.

## Deletion and lifecycle

- Referenced ProjectAsset physical deletion은 DB가 거부한다.
- `detach_asset`과 Asset soft-delete 같은 authoritative service는 Export pointer를 확인하고 active ownership의 soft deletion을 거부해야 한다.
- Project soft deletion은 기존 lifecycle을 유지하며 Export Asset history를 cascade 삭제하지 않는다.
- Project가 유지되는 동안 dangling active ownership을 자동 복구하거나 다른 Asset으로 교체하지 않는다.

## 결과

ProjectAsset이 membership authority로 유지되고 ADR-063의 Project-owned user-visible Export history와 일치한다. DB는 identity와 same-Project structure를, domain service는 mutable semantic validity를 각각 정확히 소유한다. 다음 pass는 이 schema와 service guard를 구현한 뒤 completion UoW를 연결한다.
