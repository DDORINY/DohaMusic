# ADR-060 - Deterministic Artifact Publish-or-Adopt Authority

> 상태: [승인]
> 작성일: 2026-09-07
> 최종 수정일: 2026-09-07
> 관련 기능: Durable Export Publication
> 관련 ADR: [ADR-058](ADR-058-mixer-and-export-rendering-authority.md)

## 배경

일반 Artifact publish는 무작위 Artifact ID로 불변 storage key를 만들고 충돌을 거부한다. Export worker가 payload publish 후 DB completion 전에 종료되면 새 process는 catalog가 없는 payload를 안전하게 식별하거나 채택할 수 없었다.

## 결정

일반 `publish()`의 collision 및 compensation 의미는 유지한다. 별도의 trusted internal `publish_or_adopt()` 경계를 추가한다. 이 경계는 raw path나 사용자 storage key를 받지 않고 Export Job UUID에서 생성한 `TrustedPublicationIdentity`만 허용한다.

WAV Export key는 `exports/{job-prefix}/{job-id}/result.wav`이다. 같은 Job은 process restart와 retry 후에도 같은 key를 사용하고 다른 Job은 다른 key를 사용한다. Project title 등 사용자 문자열은 key에 포함하지 않는다.

Destination이 없으면 pending regular file을 검증한 뒤 exclusive hard link로 publish한다. 동시에 생성된 destination이 있으면 SHA-256, size, media type과 media container를 다시 검증하고 exact match만 채택한다. mismatch는 overwrite나 삭제 없이 fail closed한다.

결과는 `PUBLISHED_NEW`와 `ADOPTED_EXISTING`을 구분한다. 현재 invocation이 생성한 `PUBLISHED_NEW`만 identity-checked compensation 대상이다. 채택한 기존 payload는 generic failure cleanup으로 삭제하지 않는다.

`open_trusted_publication()`은 DB catalog나 이전 process의 inode 기억 없이 typed identity로 기존 payload를 reopen하고 integrity를 재검증한다. filesystem path와 storage key는 public API에 노출하지 않는다.

## 동시성 및 보안

`os.link()`의 exclusive destination semantics로 같은 key에 physical final object는 하나만 생성된다. collision winner와 loser의 content가 같으면 loser는 채택하고, 다르면 integrity mismatch로 중단한다. symlink, directory, special file과 storage-root escape는 기존 safe local file 검증으로 거부한다.

Storage layer는 Job claim 상태를 판단하지 않는다. 향후 durable publication service가 publish 전후 claim과 cancellation을 확인하고 stale worker의 DB completion을 거부한다.

## 후속 통합

```text
0030 ledger intent
-> deterministic storage identity
-> stage WAV and calculate SHA-256
-> publish-or-adopt
-> verify and mark published
-> Artifact registration
-> JobExportResult
-> guarded Job completion
```

## 제한

현재 authority는 local immutable Artifact storage에 한정된다. `0030` ledger, Export API, worker, analyzer와 최종 completion은 후속 작업이다.
