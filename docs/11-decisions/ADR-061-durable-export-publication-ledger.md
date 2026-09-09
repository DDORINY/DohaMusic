# ADR-061: Durable Export Publication Ledger

> 상태: 승인
> 작성일: 2026-09-07
> 최종 수정일: 2026-09-07

## 배경

ADR-060의 deterministic `publish_or_adopt`는 process restart 후 payload를 복구할 수 있지만,
어떤 Export Job과 Snapshot이 그 payload를 소유하는지 DB에 남기는 durable intent가 없었다.

## 결정

`job_export_publications`는 Export Job당 하나의 물리 publication authority를 보존한다.
`job_id`를 primary key로 사용하고 immutable `composition_snapshot_id`, `wav` format 및
`TrustedPublicationIdentity`가 만든 logical storage domain/key를 저장한다. 절대 경로와 staging
경로는 저장하지 않는다.

상태 전이는 다음으로 제한한다.

```text
INTENDED -> PUBLISHED -> COMPLETED
     |           |
     +-----------+-> RECONCILIATION_REQUIRED
```

staged canonical WAV를 검증하고 SHA-256/size를 측정한 뒤 그 expected integrity를 먼저 commit한다.
그 다음 ADR-060의 exclusive publish-or-adopt를 수행한다. 따라서 physical publish 후 DB mark 전에
crash가 발생해도 새 process가 logical identity와 expected integrity만으로 exact payload를 reopen하고
adopt할 수 있다. mismatch나 tamper는 overwrite/delete 없이 reconciliation으로 fail closed한다.

모든 mutation은 version CAS를 사용하며 같은 조건부 update에서 current Job의 `running` status,
`claimed_by`, `claim_token`, cancellation 부재를 검사한다. repository는 commit/rollback하지 않는다.
취소되거나 stale인 worker는 logical state와 completion을 전진시킬 수 없다.

## 책임 경계

- Ledger: intent, physical publication integrity, restart recovery
- `JobExportResult`: 성공한 logical Export 결과
- Artifact Publisher: exclusive publish, exact adoption, trusted reopen
- Export worker/service: rendering, canonical WAV validation, Artifact registration, completion

향후 completion은 `PUBLISHED` ledger에서 Artifact, `JobExportResult`, guarded Job completion과 ledger
`COMPLETED`를 하나의 logical transaction으로 묶는다. 본 ADR은 public Export API나 renderer를 추가하지
않는다.

## 결과

- generic Artifact `publish()` collision semantics는 변경되지 않는다.
- payload가 이미 존재해도 expected integrity가 없으면 adopt하지 않는다.
- `ADOPTED_EXISTING` payload는 현재 invocation의 compensation 대상으로 삭제하지 않는다.
- 신규 public API와 사용자 제어 storage key는 없다.
