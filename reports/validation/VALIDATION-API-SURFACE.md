# API Surface 검증

> 문서 상태: [완료]
> 기준 커밋: `45ed1632b7e992b0260eb84dba5b17ec6ac00098`
> 검증일: 2026-09-06

## 범위

실제 `backend.app.factory.create_app()` Runtime과 생성된 OpenAPI 문서를 authority로 사용한다. 정적 grep 결과나 생성 파일을 source of truth로 사용하지 않는다.

## 기준선

| 항목 | 수정 전 | 수정 후 |
|---|---:|---:|
| Route | 105 | 107 |
| APIRoute | 101 | 103 |
| OpenAPI Path | 82 | 82 |
| OpenAPI Operation | 103 | 103 |
| duplicate operation ID | 2 | 0 |
| duplicate method/path | 0 | 0 |

Route와 APIRoute 증가는 기존 GET/HEAD 결합 route 두 곳을 method별 metadata로 분리한 결과다. 공개 method/path operation 집합은 동일하다.

## Canonical fingerprint

SHA-256: `54e2a14db4d80e990b142f6cb3611e9553b26b363a4acf36cdff4d12537550ec`

정규화 입력은 path, HTTP method, operation ID, request parameter 이름·위치·필수성·schema, JSON request body schema와 2xx response status/schema다. timestamp와 등록 순서는 포함하지 않는다.

## 자동 Gate

- OpenAPI 생성
- operation ID와 method/path 유일성
- 내부 `$ref` resolution
- path placeholder와 required path parameter 일치
- critical Workspace·Project·Asset·Artifact·Snapshot·Job·WorkingComposition·History·Preview·Commit·Gain·Fade·Loop·Pipeline·Voice·Generation·Lyrics API 존재
- 전체 count와 canonical fingerprint
- 의도적으로 operation ID 충돌을 주입했을 때 fail-closed 검증

## 검증 결과

- API Surface focused: 40 passed
- 최종 핵심 focused 재검증: 4 passed
- Full Backend suite: 1326 passed, 12 skipped, 0 failed, 0 xfailed
- Full Backend 경고: 기존 Starlette/httpx deprecation warning 1건
- Alembic single head: `20260905_0028`

## 제외 범위

신규 Product API, payload semantics, authorization, persistence, migration, Frontend, 실제 사용자 DB·Artifact·Provider 접근은 변경하거나 실행하지 않는다.
