# API Surface 검증

> 문서 상태: [완료]
> 기준 브랜치: `feature/mixer-export-authority-foundation`
> 기준 커밋: `45ed1632b7e992b0260eb84dba5b17ec6ac00098` 위 local Foundation diff
> 검증일: 2026-09-09

## 범위

실제 `backend.app.factory.create_app()` Runtime과 생성된 OpenAPI 문서를 authority로 사용한다. 정적 grep 결과나 생성 파일 수를 public API source of truth로 사용하지 않는다.

## 최종 authority

| 항목 | 값 |
|---|---:|
| Route | 109 |
| APIRoute | 105 |
| OpenAPI Path | 84 |
| OpenAPI Operation | 105 |
| GET | 40 |
| POST | 39 |
| PATCH | 15 |
| DELETE | 9 |
| HEAD | 2 |
| duplicate operation ID | 0 |
| duplicate method/path | 0 |
| unresolved OpenAPI reference | 0 |
| runtime/OpenAPI mismatch | 0 |

## Canonical fingerprint

SHA-256: `81b398c306b03a75d8a8b82469712c10668740c180ee27ed9687e76b7a24ffe4`

정규화 입력은 path, HTTP method, operation ID, request parameter 이름·위치·필수성·schema, JSON request body schema와 2xx response status/schema다. timestamp와 등록 순서는 포함하지 않는다.

## 자동 Gate

- OpenAPI 생성
- operation ID와 method/path 유일성
- 내부 `$ref` resolution
- path placeholder와 required path parameter 일치
- critical Workspace, Project, Asset, Artifact, Snapshot, Job, WorkingComposition, History, Preview, Commit, Gain, Fade, Loop, Mixer, Export, Pipeline, Voice, Generation, Lyrics API 존재
- 전체 count와 canonical fingerprint
- duplicate operation ID 주입 시 fail-closed 검증

## 최종 검증 결과

- API Surface focused: PASS
- Full Backend suite: `1445 passed, 12 skipped, 0 failed`
- Alembic single head: `20260908_0032`
- public API semantic drift: 0

## 제외 범위

MP3/FLAC Export, mastering, stem Export와 실제 사용자 DB·Artifact·Provider 접근은 이 검증 범위에 포함하지 않는다.
