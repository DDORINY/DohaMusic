# DohaMusic Workspace Artifact 모델

> 문서 상태: [진행 중]
> 최종 수정일: 2026-08-27
> 관련 기능: AssetVersion 기반 Composition Snapshot, Mix, Export
> 관련 문서: [CompositionSnapshot 기반](../06-api/composition-snapshot-foundation.md), [Workspace Job Foundation](workspace-job-foundation.md), [Artifact Storage 계약](artifact-storage-contract.md), [Storage Architecture](storage-architecture.md), [System Architecture](system-architecture.md), [Database Overview](../07-database/database-overview.md), [ADR-029](../11-decisions/ADR-029-dohamusic-workspace-artifact-domain.md), [ADR-032](../11-decisions/ADR-032-artifact-storage-resolver-integrity.md)

## 목적

DohaMusic은 AI Provider가 아니라 개인 AI 음악 제작 Workspace다. DohaLM·DohaAudio·DohaVocal의 Runtime 산출물과 사용자가 선택·조합해 만든 프로젝트 결과물의 저장 책임을 분리한다.

이 문서는 목표 계약을 정의한다. Asset·AssetVersion Resource API와 실제 사용자 DB Asset Index는 구현·적용했다. Artifact ID와 물리 Payload를 1:1로 연결하는 내부 Catalog와 revision `20260809_0016`도 실제 사용자 DB에 적용했으며 row는 0개다. Catalog 조회·local Resolver·trusted ingestion과 Owner·retention·integrity read Gate, dry-run reconciliation, Artifact Metadata·content·download와 single-byte Range를 임시 root에서 검증했다. 실제 `D:/DohaArtifacts/music` 디렉터리, destructive reconciliation, Mix·Export Job과 Runtime 파일 이동은 아직 구현하지 않았다.

## Workspace 흐름

```text
Lyrics Asset
  ↓
Music Asset
  ↓
Vocal Asset
  ↓
Composition Snapshot
  ↓
Mix Asset
  ↓
Export Asset
  ↓
DohaArtifacts/music
```

각 단계는 이전 결과를 덮어쓰지 않는다. Provider 결과를 Workspace AssetVersion으로 등록하고, 사용자가 선택한 버전만 Snapshot과 후속 Job의 입력이 된다.

## Provider와 Workspace 경계

```mermaid
flowchart LR
  LM[DohaLM artifacts/lm] --> O[DohaMusic Orchestrator]
  A[DohaAudio artifacts/audio] --> O
  V[DohaVocal artifacts/vocal] --> O
  O --> S[Composition Snapshot]
  S --> M[DohaMusic Mix Job]
  M --> MA[Mix Asset artifacts/music/mixes]
  MA --> E[DohaMusic Export Job]
  E --> EA[Export Asset artifacts/music/exports]
```

- DohaLM은 가사 Provider의 모델·학습·평가·Runtime Artifact를 `lm`에 둔다.
- DohaAudio `[계획]`는 음악 생성·Instrumental·Stem·분석 Runtime Artifact를 `audio`에 둔다.
- DohaVocal `[계획]`는 AI Vocal·녹음 기술 처리·Voice Conversion·보정 Runtime Artifact를 `vocal`에 둔다.
- DohaMusic은 Provider 결과의 선택 관계, Snapshot, Mix, Preview와 Export를 `music`에 둔다.
- Provider는 서로 직접 호출하지 않고 DohaMusic Orchestrator를 통한다.

## Composition Snapshot 계약

Snapshot은 Asset의 최신값이 아니라 불변 `AssetVersion`을 참조한다. 이후 Asset에 새 버전이 추가돼도 기존 Mix와 Export가 같은 입력을 추적할 수 있어야 한다.

최소 계획 필드는 다음과 같다.

```text
lyrics_asset_version_id
music_asset_version_id
vocal_asset_version_id
stem_asset_version_ids
processing_chain
mix_settings
provider_versions
model_versions
created_at
```

Snapshot 자체는 Mix Asset이 아니다. 특정 조합과 설정을 재현하기 위한 불변 입력 계약이며, 변경할 때는 기존 Snapshot을 수정하지 않고 새 Snapshot을 만든다. 권위 있는 Snapshot 관계 데이터와 현재 선택 상태의 원장은 DB다. `DohaArtifacts/music/snapshots`에는 재현·교환·백업을 위한 직렬화 Artifact만 저장하며 이 파일을 DB 관계의 독립 원장으로 사용하지 않는다.

현재 Application 기반과 공식 API는 effective Owner·활성 Project, 같은 Workspace 또는 Owner 소유 Workspace 미지정 Asset과 활성 ProjectAsset 관계를 검증한다. 공개 role은 `lyrics`, `music`, `vocal`, `stem`, `mix`이며 정확한 AssetVersion만 고정한다. Snapshot+Item을 원자적으로 생성하고 Project별 version·Cursor·`Idempotency-Key` replay를 관리하며 목록·생성·상세 API 3개를 제공한다. Snapshot·SnapshotItem의 수정·삭제 API는 제공하지 않는다.

## Mix Asset 계약

Mix는 DohaMusic 책임이다. 선택한 Vocal·Music·Stem AssetVersion, processing chain과 mix settings를 결합하고 결과를 새 Mix AssetVersion으로 등록한다.

Mix 파일 Artifact의 목표 위치는 `D:/DohaArtifacts/music/mixes`다. Provider Runtime은 이 위치에 직접 결과를 기록하지 않는다.

## Export Asset 계약

WorkingComposition 경로의 Export는 immutable CompositionSnapshot을 WAV·MP3·FLAC 같은 전달 형식으로 변환한다. Project-owned Export Asset 아래 각 명시적 성공 결과를 별도 Export AssetVersion과 Artifact로 추적하며 목표 위치는 `D:/DohaArtifacts/music/exports`다. 상세 lineage는 [ADR-063](../11-decisions/ADR-063-export-asset-lineage-and-completion-authority.md)을 따른다.

현재 구현은 Pipeline의 WAV Export만 제공하며 MP3·FLAC와 독립 Export Asset은 `[계획]`이다.

## Preview와 실행 기록

- Preview는 낮은 용량의 빠른 재생 파일, 짧은 구간과 waveform cache를 포함할 수 있다.
- Preview는 원본 Mix·Export를 대체하지 않으며 source AssetVersion과 생성 규칙을 기록한다.
- `runs`는 Mix·Export Job 실행 로그, 설정 Snapshot과 오류·시간 같은 안전한 진단 metadata를 저장한다.
- 로그에는 비밀정보, 개인 음성 내용과 로컬 절대 경로를 기록하지 않는다.

## 현재 호환 경계

WorkingComposition Clip Waveform 선행 read는 Project와 exact AssetVersion을 입력으로 받아 기존 Artifact eligibility와 trusted `duration_us` 권위를 그대로 재사용한다. active Asset·ProjectAsset·effective Owner/Workspace를 확인한 뒤 eligible audio Artifact가 정확히 하나일 때만 opaque Artifact ID, media facts와 same-origin content endpoint를 반환한다. latest Version·첫 Artifact fallback, storage location·path·locator 공개와 payload probe는 금지한다. 기존 `ProjectAsset` 관계 인덱스와 `ix_artifacts_version_created`가 조회를 지원하므로 새 schema·migration은 없다.

현재 `PipelineExecutor`, `pipeline_jobs`, `pipeline_files`와 `AUDIO_STORAGE_ROOT`는 그대로 유지한다. 기존 `final.wav`, Preview 후보와 metadata를 즉시 이동하거나 새 Asset으로 backfill하지 않는다. Catalog·Resolver·trusted ingestion은 구현했지만 실제 파일 전환은 별도 Inventory·backup·rehearsal·승인과 rollback Gate를 통과한 작업으로 수행한다.

Workspace Job 결과는 Provider success만으로 등록하지 않는다. Job type별 필수 output이 모두 검증된 뒤 Artifact·Catalog·필요한 AssetVersion·JobOutput·ModelUsage와 `succeeded`를 completion Unit of Work에서 확정한다. 부분 출력은 staging 제거 또는 quarantine하며 정상 사용자 Artifact로 공개하지 않는다. 이 계약은 [Workspace Job Foundation](workspace-job-foundation.md)에 확정했지만 orchestration 구현과 Runtime 연결은 아직 미구현이다.
