# ADR-029 — DohaMusic Workspace 전용 `music` Artifact 도메인

> 상태: 부분 대체됨
> 대체 결정: [ADR-063](ADR-063-export-asset-lineage-and-completion-authority.md)에서 WorkingComposition/CompositionSnapshot 기반 Export의 즉시 입력 계약을 대체한다. Artifact 도메인과 `music` 저장 영역 결정은 계속 유효하다.
> 작성일: 2026-08-05
> 최종 수정일: 2026-08-08
> 관련 문서: [Artifact Storage 계약](../03-architecture/artifact-storage-contract.md), [Workspace Artifact 모델](../03-architecture/workspace-artifact-model.md), [Storage Architecture](../03-architecture/storage-architecture.md), [Database Overview](../07-database/database-overview.md), [ADR-032](ADR-032-artifact-storage-resolver-integrity.md)

## 배경

DohaLM, DohaAudio와 DohaVocal의 모델·학습·평가·Runtime 산출물은 Provider 도메인이다. DohaMusic은 이 결과를 선택하고 조합해 Mix·Preview·최종 Export를 만드는 개인 음악 제작 Workspace이므로 Provider 산출물과 프로젝트 결과물을 같은 Artifact 도메인에 두면 소유 책임과 보존 정책이 모호해진다.

현재 구현은 `AUDIO_STORAGE_ROOT` 아래 Pipeline 중심 파일 구조를 사용한다. AssetVersion Entity·API와 Artifact Catalog·local Resolver는 구현했지만 `D:/DohaArtifacts/music`, trusted ingestion, Composition Snapshot과 독립 Mix·Export Asset은 구현하지 않았다.

## 문제

- 최종 Mix·Export를 `audio`에 두면 DohaAudio Provider가 제품 조합 결과까지 소유하는 것처럼 보인다.
- `vocal`에 두면 음악·보컬·Mix 책임이 섞인다.
- 최신 Asset만 참조하면 입력이 바뀐 뒤 과거 Mix와 Export를 재현하기 어렵다.
- Provider Runtime과 Workspace 결과의 보존·삭제·접근 정책을 독립적으로 발전시키기 어렵다.

## 결정

1. `D:/DohaArtifacts/music`을 DohaMusic Workspace 전용 목표 Artifact 도메인으로 정의한다.
2. 하위 영역은 `mixes`, `exports`, `previews`, `snapshots`, `runs`로 구분한다.
3. `lm`, `audio`, `vocal`은 계속 각 AI Provider의 모델·학습·평가·Runtime 산출물을 소유한다.
4. Mix와 최종 Export는 DohaMusic 책임이며 AI Provider 책임이 아니다.
5. Composition Snapshot은 Asset이 아니라 불변 AssetVersion을 참조한다.
6. Mix Job은 Snapshot을 입력으로 Mix Asset을 만들고 Export Job은 Mix AssetVersion을 입력으로 Export Asset을 만든다.
7. DB·Manifest·공개 API에는 로컬 절대 경로를 저장하지 않는다. 내부 URI는 `artifact://<artifact_id>`, 공개 응답은 Artifact API link를 사용하고 물리 locator는 별도 내부 Catalog가 소유한다.
8. 이번 결정은 문서 계약이며 폴더 생성, 파일 이동, 환경 변수, 코드와 DB migration을 수행하지 않는다.
9. Composition Snapshot의 권위 있는 관계 데이터는 DB가 소유한다. `snapshots` 폴더는 재현·직렬화·백업용 Artifact만 저장하며 별도 관계 원장이 아니다.

## 선택 이유

Provider 모델 수명주기와 사용자의 프로젝트 결과 수명주기를 분리하면서, DohaMusic이 조합·믹싱·출력의 최종 책임자라는 경계를 명확히 할 수 있다. AssetVersion 기반 Snapshot은 원본이 발전해도 과거 결과의 provenance를 보존한다.

## 대안

- `audio`에 Mix·Export 저장: Provider 생성 결과와 Workspace 조합 결과의 소유권이 섞여 제외한다.
- 기존 Pipeline Storage만 영구 유지: 현재 호환에는 필요하지만 독립 Asset·Snapshot과 외부 Provider 전환을 표현하기 어려워 장기 기준으로 채택하지 않는다.
- Project별 임의 절대 경로 저장: 이동성과 보안 검증을 해치므로 제외한다.

## 장단점

장점은 책임 경계, 재현성, 프로젝트 결과 검색과 보존 정책의 명확성이다. 단점은 Artifact resolver, AssetVersion·Snapshot DB, 기존 파일 backfill과 중복 저장 방지 설계가 추가된다는 점이다.

## 영향

현재 Runtime·API·DB·환경 변수와 로컬 파일은 변경하지 않는다. 향후 Storage Architecture, Asset API, Mix·Export Job, 보안 다운로드와 cleanup 정책이 이 경계를 따라야 한다. 기존 `PipelineExecutor`와 `pipeline_files`는 migration 완료 전까지 호환 계층으로 유지한다.

## 마이그레이션

1. 문서와 책임 경계를 확정한다.
2. `[부분 완료]` Artifact ID/URI, 별도 Catalog·Resolver와 ingestion·무결성 경계를 ADR-032로 승인했다. Catalog Entity·Migration과 local Resolver는 구현·검증했고 ingestion·전체 무결성 검증은 후속 작업이다.
3. 신규 Mix·Export 결과부터 `music` 도메인을 사용한다.
4. 기존 Pipeline 결과는 checksum·참조·rollback 검증 후 선택적으로 backfill한다.
5. 모든 소비자가 전환된 뒤에만 구형 경로 제거를 검토한다.

## 재검토 조건

- Artifact Catalog·Resolver를 구현하거나 object storage로 확장할 때
- Object Storage 또는 다중 Workspace를 도입할 때
- Snapshot schema와 Asset 삭제·보존 정책을 확정할 때
- Provider가 Workspace Artifact를 직접 쓰거나 Mix 책임을 요구할 때
- 기존 Pipeline Storage를 제거할 때

## 관련 PR

- [PR #51 — DohaMusic music Artifact 도메인 문서화](https://github.com/DohaStudio/DohaMusic/pull/51)
