# ADR-063: Export Asset Lineage and Atomic Completion Authority

> 상태: 승인
> 작성일: 2026-09-07
> 최종 수정일: 2026-09-07
> 관련 기능: Canonical WAV Export, durable publication recovery
> 관련 ADR: [ADR-029](ADR-029-dohamusic-workspace-artifact-domain.md), [ADR-058](ADR-058-mixer-and-export-rendering-authority.md), [ADR-060](ADR-060-deterministic-artifact-publish-or-adopt.md), [ADR-061](ADR-061-durable-export-publication-ledger.md), [ADR-062](ADR-062-trusted-existing-publication-artifact-registration.md), [ADR-064](ADR-064-project-export-asset-ownership-invariant.md)

## 배경

ADR-029는 Snapshot을 입력으로 Mix Asset을 만든 뒤 Mix AssetVersion을 Export Job의 즉시 입력으로 사용하는 초기 경계를 정의했다. 이후 WorkingComposition과 immutable CompositionSnapshot이 Clip, Track, Master Mixer 및 source AssetVersion lineage를 완전하게 freeze하는 canonical mix authority가 되었다. ADR-058은 Export가 명시된 Snapshot만 사용하고 `JobExportResult`가 exported AssetVersion과 Artifact를 참조하도록 결정했지만, Export Asset의 소유권과 final completion transaction owner는 확정하지 않았다.

## 검토한 모델

- Export 요청마다 Asset 생성: lineage는 단순하지만 idempotency와 사용자 Asset 목록에 불필요한 logical identity가 누적된다.
- Project-owned Export Asset: Project의 deliverable history를 하나의 logical Asset과 immutable AssetVersion들로 표현한다.
- Snapshot-owned Export Asset: Snapshot마다 Asset이 증가하고 multi-format 및 반복 Export의 사용자 의미가 불명확하다.
- Artifact 직접 결과: 현재 `Artifact -> AssetVersion -> Asset` 필수 관계와 ADR-058을 위반한다.
- Mix AssetVersion 중간 materialization 유지: 완전한 Snapshot authority를 다시 materialize하여 동일 immutable 의미를 중복한다.

## 결정

### 입력과 lineage

`CompositionSnapshot`이 Export의 canonical immutable mix input이다. ADR-029의 "Export Job은 Mix AssetVersion을 즉시 입력으로 사용한다"는 부분은 WorkingComposition/Snapshot 경로에 한해 이 ADR로 대체한다. ADR-029의 Workspace Artifact ownership과 `music` storage domain 결정은 유지한다.

```text
MusicProject
  -> project-owned Export Asset (asset_type=export)
     -> one immutable Export AssetVersion per successful explicit Export Job
        -> one canonical Artifact

CompositionSnapshot
  -> JobExportResult source lineage
     -> the same Export AssetVersion and Artifact
```

Snapshot이 참조하는 source AssetVersion 집합은 Snapshot 자체가 보존한다. Export AssetVersion에 같은 집합을 별도 관계로 복제하지 않는다. `JobExportResult.composition_snapshot_id`가 Export 결과에서 Snapshot으로 가는 canonical provenance다.

### Export Asset과 AssetVersion

각 MusicProject는 활성 Project-owned Export Asset을 정확히 하나 가진다. Asset은 `AssetType.EXPORT`, Project Workspace, 요청 owner에 속하고 Project Asset 목록에 `export` role로 표시되는 사용자-visible logical deliverable history다. 임의 파일명이나 요청 title은 identity가 아니다.

성공한 명시적 Export Job마다 같은 Export Asset 아래 새 immutable AssetVersion 하나를 만든다. 같은 Idempotency-Key와 fingerprint replay는 같은 Job, Asset, AssetVersion, Artifact 및 result를 반환한다. 같은 Snapshot과 format이어도 새 Idempotency-Key로 시작한 명시적 Export는 새 Job과 새 AssetVersion을 만든다. 향후 WAV, MP3, FLAC도 같은 Project Export Asset 아래 format-specific AssetVersion으로 확장하며 format은 `JobExportResult`와 Job settings authority에 남긴다.

### JobOutput과 JobExportResult

`JobOutput`은 모든 Job의 generic ordered output index이므로 Export에도 필요하다. Export의 output order `0`, role `export`는 canonical Artifact를 참조한다. `JobExportResult`는 Export-specific source Snapshot, format, render fingerprint, quality 측정값과 exported AssetVersion/Artifact lineage를 소유한다. 두 row는 반드시 동일 Artifact를 참조하며 두 번째 Artifact나 물리 payload를 만들지 않는다.

### Completion owner와 transaction

Provider-oriented `JobCompletionService`에 fake `ProviderResult`를 전달하지 않는다. 별도 `ExportJobCompletionService`가 기존 Job claim/status guard와 repository transaction convention을 재사용한다.

물리 payload와 expected integrity는 ADR-060/061에 따라 DB completion 전에 durable하다. Final DB logical transaction은 다음을 함께 처리한다.

1. current RUNNING claim과 cancellation 재검증
2. Project Export Asset resolve/create
3. 해당 Job의 Export AssetVersion 생성 또는 exact replay resolve
4. trusted publication에서 Artifact와 ArtifactStorageLocation 등록 및 ledger Artifact binding
5. 동일 Artifact를 가리키는 JobOutput 생성
6. JobExportResult 생성
7. Job을 SUCCEEDED로 전이
8. ledger를 COMPLETED로 전이

`TrustedArtifactRegistrationService`의 검증 및 등록 core는 caller-owned Session/UoW에 참여할 수 있도록 분리한다. 기존 standalone method와 generic ingestion 의미는 유지하며 nested independent commit은 만들지 않는다.

Transaction rollback 뒤 durable physical publication은 남고 다음 valid claim이 exact reopen/adopt하여 전체 DB completion을 재시도한다. 성공 response loss replay는 기존 row들의 exact identity를 검증하고 그대로 반환한다.

### 경쟁, stale worker, cancellation

Project Export Asset 생성은 DB uniqueness와 conditional assignment로 수렴해야 한다. AssetVersion, Artifact, JobOutput, JobExportResult 및 ledger completion은 current claim을 같은 transaction에서 확인한 뒤 생성한다. stale/replaced worker와 cancel-requested Job은 어떤 final logical row도 생성하거나 completion을 전진시킬 수 없다.

같은 Job의 경쟁 completion은 하나의 AssetVersion, Artifact, JobOutput, JobExportResult로 수렴한다. 충돌 loser는 committed authority를 exact 검증하여 replay하거나 fail closed한다.

## Schema 영향

현재 `20260907_0030` schema는 Project가 정확히 하나의 Export Asset membership을 소유한다는 관계를 경쟁 안전하게 표현하지 못한다. 구체적인 ownership FK와 DB/domain invariant 경계는 ADR-064가 이 결정을 보완한다.

따라서 WAV production implementation 전에 최소 revision `0031`이 필요하다.

- `music_projects.export_project_asset_id` nullable UUID
- `(project_id, export_project_asset_id)` composite FK -> `project_assets(project_id, project_asset_id)`, `ON DELETE RESTRICT`
- `music_projects.export_project_asset_id` unique constraint
- composite FK target을 위한 `project_assets(project_id, project_asset_id)` unique constraint
- 기존 Project는 null로 유지하고 첫 Export completion에서 conditional assignment
- assignment 대상은 같은 Workspace/owner의 active `AssetType.EXPORT`만 허용

이 관계 FK가 Project-owned Export Asset membership의 durable identity와 first-export concurrency authority다. role/type/workspace/owner/active predicate는 ADR-064의 domain service invariant다. 기존 row backfill이나 Export Asset 자동 생성은 수행하지 않는다. `JobExportResult`의 Job PK, `exported_asset_version_id`, `exported_artifact_id`, `composition_snapshot_id`는 per-Job result와 Snapshot lineage를 이미 표현하므로 추가 result column은 필요 없다.

## 실패 및 복구

- Asset/Version/Artifact/JobOutput/result/Job/ledger 중 하나라도 실패하면 final DB transaction 전체를 rollback한다.
- rollback은 durable physical payload를 삭제하지 않는다.
- adopted existing payload를 compensation으로 삭제하지 않는다.
- mismatch, lineage 불일치 또는 다른 Artifact binding은 overwrite 없이 fail closed한다.
- Job 삭제, Snapshot 삭제, Asset/Version 삭제는 현재 RESTRICT 관계와 기존 lifecycle policy를 따른다.

## 결과

- Snapshot이 중간 Mix AssetVersion 없이 Export render authority가 된다.
- Project는 하나의 logical Export history를 가지며 각 명시적 성공은 immutable version이 된다.
- generic Job output 탐색과 Export-specific quality/provenance가 동일 Artifact를 공유한다.
- filesystem과 DB를 하나의 ACID transaction으로 주장하지 않는다. durable publication과 replay 가능한 단일 DB transaction으로 recoverable atomic logical completion을 제공한다.

## 후속 작업

1. `0031`에 Project Export Asset FK와 uniqueness를 추가한다.
2. trusted registration core가 caller-owned transaction에 참여하도록 최소 분리한다.
3. `ExportJobCompletionService`와 claim/cancellation/concurrency tests를 구현한다.
4. 그 authority 위에서 Canonical WAV Export API/worker를 완성한다.
