# ADR-065: Export Production Worker Runner

> 상태: 승인
> 작성일: 2026-09-08
> 최종 수정일: 2026-09-08
> 관련 기능: Canonical WAV Export production execution
> 관련 ADR: [ADR-061](ADR-061-durable-export-publication-ledger.md), [ADR-063](ADR-063-export-asset-lineage-and-completion-authority.md), [ADR-064](ADR-064-project-export-asset-ownership-invariant.md)

## 배경

`POST /api/v1/jobs`는 Snapshot 기반 Export Job과 durable publication intent를 생성하지만,
queued Export Job을 `ExportWorkerService`로 전달하는 production lifecycle이 없었다.
기존 `JobWorkerService`는 Provider request와 Provider result completion에 결합돼 있어 Export를
Provider execution으로 가장시키지 않고 재사용할 수 없다.

## 결정

`ExportWorkerRunner`를 Export 전용 production polling 경계로 사용한다.

```text
Job API
→ queued Export Job
→ ExportWorkerRunner
→ Export-only claim/lease CAS
→ ExportWorkerService
→ canonical WAV completion
```

Runner는 polling, Export-only discovery, claim/token handoff, shutdown 및 iteration-level exception
isolation만 담당한다. Snapshot projection, render, analysis, integrity, publication, Artifact 및 Job
completion은 기존 `ExportWorkerService`와 하위 authority가 계속 소유한다.

## Claim 및 restart recovery

- 기존 `JobRepository` CAS를 `job_type="export"` 필터와 함께 재사용한다.
- cancel marker가 있거나 terminal인 Job은 claim하지 않는다.
- 만료된 Export claim은 동일 CAS authority로 queued 상태로 되돌린 뒤 새 runner가 reclaim한다.
- Provider Job의 기존 만료 처리와 claim 동작은 변경하지 않는다.
- claimant ID와 claim token은 public API에 노출하지 않는다.

## Startup 및 shutdown

Artifact root와 staging root가 모두 설정된 application lifespan에서 canonical renderer, analyzer,
publisher, completion service와 runner를 한 번만 구성한다. Startup은 runner task를 시작하고 shutdown은
새 polling을 중단한 뒤 진행 중인 iteration이 종료될 때까지 기다린다. 각 claim과 execution service는
short-lived SQLAlchemy Session을 사용한다.

## 선택 이유

- Provider dispatcher semantics를 변경하지 않는다.
- DB claim/lease authority를 그대로 재사용한다.
- process restart와 multi-instance claim race가 process-local lock에 의존하지 않는다.
- Export DSP 및 completion logic이 runner에 중복되지 않는다.

## 대안

- Provider `JobWorkerService` 확장: ProviderResult contract와 충돌하므로 제외한다.
- FastAPI background task: HTTP process lifecycle에 묶이고 restart recovery가 약해 제외한다.
- 범용 typed worker framework 신설: 현재 범위보다 큰 architecture 확장이므로 제외한다.

## 재검토 조건

여러 non-Provider Job 유형이 동일한 durable polling lifecycle을 요구하면 공통 scheduling shell을
별도 ADR로 검토한다.
