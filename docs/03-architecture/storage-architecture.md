# 저장소 아키텍처

> 문서 상태: [운영 기준 + 계획]
> 최종 수정일: 2026-08-10
> 관련 문서: [Artifact Storage 계약](artifact-storage-contract.md), [Verified Durable Staging Authority](verified-durable-staging-authority.md), [Workspace Artifact 모델](workspace-artifact-model.md), [ADR-029](../11-decisions/ADR-029-dohamusic-workspace-artifact-domain.md), [ADR-032](../11-decisions/ADR-032-artifact-storage-resolver-integrity.md), [ADR-051](../11-decisions/ADR-051-verified-durable-staging-authority.md), [데이터베이스 개요](../07-database/database-overview.md)

## 현재 구현

```text
backend/storage/
├─ inputs/
├─ outputs/
├─ samples/
├─ stems/
│  ├─ vocals/
│  ├─ instrumentals/
│  └─ metadata/
└─ voices/
   ├─ references/
   ├─ converted/
   └─ metadata/
```

루트는 `AUDIO_STORAGE_ROOT`로 설정한다. DB에는 루트 기준 상대 경로만 저장하며 `StorageService`는 path traversal과 루트 밖 접근을 거부한다. Voice Worker는 참조 경로에 더 엄격한 `voices/references` 경계를 적용한다.

Voice 출력은 `voices/converted/{job_id}.wav`, 실행 metadata는 `voices/metadata/{job_id}.json`이다. 사용자용 단일 WAV upload는 검증 전 `voices/references/.uploads/{uuid}.tmp`, 성공 후 `voices/references/{profile_uuid}/reference.wav`를 사용하며 실패 시 temp·final orphan을 정리한다. legacy 운영자 배치 참조 파일도 개발 호환으로 유지한다. 참조 음성·변환 음성·모델·cache·실험 파일은 Git에서 제외한다. 원본 참조 음성 content/download는 제공하지 않고 완료 Pipeline의 허용된 WAV만 Service 검증 후 `FileResponse`로 전달하며 Storage 디렉터리 자체는 정적으로 공개하지 않는다.

F6는 [ADR-026](../11-decisions/ADR-026-voice-enrollment-lifecycle-cleanup.md)에 따라 `voices/enrollments/{enrollment_id}/samples/{sample_id}/original.{wav|webm|ogg}`와 `normalized.wav` 임시 root, `voices/references/{profile_id}/samples/{sample_id}/reference.wav` 승격 구조를 구현했다. UUID 생성 경로, root·traversal·symlink 검사, 임시 suffix와 rename, overwrite 방지와 반복 cleanup을 적용한다. 원본은 submit·sample 삭제·취소·만료 때 제거하고 실패는 `DELETE_FAILED`/cleanup failure로 추적한다. process-local scheduler는 DB 기준 orphan scan·retry와 시작 복구를 수행하며 공개 DTO와 로그에는 내부 경로를 포함하지 않는다.

## 목표 Artifact 도메인 [계획]

현재 `AUDIO_STORAGE_ROOT` 구현과 별개로 장기 로컬 Artifact root를 다음처럼 구분한다. Catalog·`DOHA_ARTIFACT_ROOT` local Resolver와 별도 `DOHA_ARTIFACT_STAGING_ROOT` Trusted Ingestion을 구현했다. Ingestion은 staging과 네 domain root의 중첩을 거부하고 test root에서만 Payload를 publish했다. 실제 운영 root directory를 생성하거나 사용자 파일을 읽고 이동하지 않았다.

Durable `PayloadLocator` persistence foundation은 config-owned backend ID와 canonical relative `staging_key`를 저장할 schema와 lifecycle을 제공한다. [Verified Durable Staging Authority](verified-durable-staging-authority.md)에 따라 기존 `DOHA_ARTIFACT_STAGING_ROOT` 아래 locator-derived key, partial verify와 exclusive hard-link publish, restart orphan adoption을 수행하는 local adapter와 `verified_staged` CAS 통합을 구현했다. absolute path, drive/UNC, URL, traversal, root, credential과 bytes는 DB에 저장하지 않는다. downloader·Artifact ingestion·Completion·Worker wiring은 아직 미구현이다.

```text
D:/DohaArtifacts/
├── lm/       # DohaLM Provider 모델·학습·평가·Runtime 산출물
├── audio/    # DohaAudio Provider 음악 생성·분리·분석 산출물
├── vocal/    # DohaVocal Provider 보컬 생성·변환·보정 산출물
└── music/    # DohaMusic Workspace 프로젝트 결과물
    ├── mixes/
    ├── exports/
    ├── previews/
    ├── snapshots/
    └── runs/
```

| 도메인 | 소유 책임 | 대표 Artifact |
|---|---|---|
| `lm` | DohaLM Provider | LLM checkpoint, adapter, model, evaluation, run |
| `audio` | DohaAudio Provider `[계획]` | Music Generation, instrumental, stem, audio evaluation, Runtime 결과 |
| `vocal` | DohaVocal Provider `[계획]` | AI Vocal, recording 기술 처리 결과, Voice Conversion, Pitch·Timing Correction, vocal evaluation, Runtime 결과 |
| `music` | DohaMusic Workspace | Mix Asset, Export Asset, Preview, Composition Snapshot, Mix·Export Job 실행 기록 |

`audio`와 `vocal`은 모델·Provider 실행 결과를 소유하고 `music`은 사용자가 선택한 특정 AssetVersion을 조합한 프로젝트 결과를 소유한다. Provider가 Mix 또는 최종 Export를 소유하지 않으며 Provider끼리 `music` 영역을 직접 쓰지 않는다. Resolver 구성 시 네 domain directory가 모두 존재하는 안전한 directory여야 하고 root나 하위 component가 symlink·junction·reparse point이면 거부한다.

### `music` 하위 구조 [계획]

- `mixes/`: Composition Snapshot을 입력으로 DohaMusic Mixer가 만든 Mix Asset의 파일 Artifact
- `exports/`: immutable CompositionSnapshot에서 만든 WAV·MP3·FLAC 등 최종 Export Artifact. Project-owned Export Asset lineage는 [ADR-063](../11-decisions/ADR-063-export-asset-lineage-and-completion-authority.md)을 따른다.
- `previews/`: 낮은 용량의 빠른 재생 파일, waveform cache와 기타 재생 파생물
- `snapshots/`: DB가 소유하는 특정 AssetVersion 조합, processing chain과 mix settings를 재현·교환·백업하기 위한 불변 직렬화 Artifact. 권위 있는 관계 데이터는 DB에 유지
- `runs/`: Mix Job·Export Job 실행 로그, 설정 snapshot과 안전한 진단 metadata

내부 Workspace DB와 공개 API는 로컬 절대 경로를 계약으로 사용하지 않는다. Artifact의 내부 논리 URI는 `artifact://<artifact_id>`이며 공개 응답은 Artifact API link를 사용한다. Catalog·local Resolver·trusted ingestion과 Artifact content/download delivery는 완료했다. 실제 운영 Payload 등록과 Runtime 전환은 아직 `[계획]`이다.

## K3 Preview 저장 목표 [계획]

K3.4 Preview는 `pipelines/{job_id}/preview_15s.wav` 후보 경로에 원본과 분리해 저장하고 `pipeline_files`의 opaque ID로 등록한다. 내부 상대·절대 경로는 공개 DTO에 포함하지 않으며 기존 secure content/download의 완료 상태·Storage root·symlink·regular file·크기·MIME·RIFF 검증과 `private, no-store`를 재사용한다.

K3.1은 새 디렉터리·파일·테이블 없이 기존 `result_metadata.audio_analysis`와 `metadata.json`을 갱신한다. Project 삭제는 Job·분석 metadata를 보존한다. Preview 저장은 K3.4 계획이며 Retry는 새 Job의 새 final WAV를 분석하고 기존 결과를 복사하지 않는다. 세부 계약은 [Audio Analysis 결과 계약](audio-analysis-result-contract.md)을 따른다.
