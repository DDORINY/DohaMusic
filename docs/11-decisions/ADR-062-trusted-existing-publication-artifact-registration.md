# ADR-062: Trusted Existing Publication Artifact Registration

> 상태: 승인
> 작성일: 2026-09-07
> 최종 수정일: 2026-09-07

## 배경

ADR-060과 ADR-061은 Export payload의 deterministic publication과 crash recovery를 제공하지만,
generic `ArtifactIngestionService.prepare()`는 random Artifact key로 다시 publish하므로 이미 durable한
payload를 Artifact catalog에 승격할 때 두 번째 physical object가 생긴다.

## 결정

public API가 아닌 `TrustedArtifactRegistrationService`를 둔다. 입력 authority는 current Job claim과
`PUBLISHED` ledger뿐이다. 서비스는 ledger에서 canonical identity와 expected integrity를 읽고
`open_trusted_publication()`으로 final object를 다시 검증한다. 사용자 path, storage key 또는 hash를
입력으로 받지 않는다.

검증된 payload는 기존 `PreparedArtifactIngestion` 내부 형태로 변환하여 `register_prepared()`와
`verify_registered()`의 공통 DB 등록 core를 재사용한다. `Artifact`, `ArtifactStorageLocation`, ledger
`artifact_id` CAS는 한 transaction에 포함된다. StorageLocation은 deterministic key를 그대로 사용하며
publish, copy, overwrite와 compensation delete를 수행하지 않는다.

ledger의 unique nullable `artifact_id`와 StorageLocation locator uniqueness가 concurrent registration을
제한한다. transaction response loss나 race loser는 ledger binding을 읽고 Artifact, locator, checksum,
size, media를 다시 검증한 뒤 같은 Artifact를 replay한다. 불일치는 자동 수정하지 않고 fail closed한다.

Generic ingestion의 `prepare -> publish -> register` 및 registration 실패 compensation semantics는
변경하지 않는다. 등록 후에는 기존 `ArtifactStorageResolver`와 download 경로가 동일 catalog authority를
사용한다.
