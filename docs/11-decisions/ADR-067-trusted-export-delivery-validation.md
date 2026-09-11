# ADR-067: Trusted Export Delivery Validation

- 상태: 승인
- 작성일: 2026-09-10
- 최종 수정일: 2026-09-10
- 관련 기능: Multi-format Export delivery
- 관련 ADR: [ADR-060](ADR-060-deterministic-artifact-publish-or-adopt.md), [ADR-066](ADR-066-silent-export-quality-representation.md)

## 배경

Generic Artifact media 검증은 MP3의 ID3 경계와 첫 MPEG frame을 확인하지만 전체 stream의 decode 가능성은 증명하지 않는다. 이 verdict를 durable Export publication authority로 사용하면 앞부분만 유효한 truncated payload를 채택할 수 있다.

## 결정

Trusted Export delivery는 generic media sniffing과 별도의 `ExportDeliveryValidator`를 통과해야 한다. Validator는 configured FFmpeg와 paired FFprobe를 `shell=False`로 실행한다.

검증 순서는 다음과 같다.

```text
structural media validation
-> FFprobe codec/container/sample metadata
-> canonical rate/channel/bit-depth/duration validation
-> FFmpeg full decode with fatal decoder errors enabled
-> integrity freeze
-> publish-or-adopt
```

지원 format은 `wav`, `mp3`, `flac`으로 닫는다. WAV와 FLAC은 48 kHz stereo 16-bit를 요구한다. MP3는 codec `mp3`, 48 kHz stereo를 요구하며 실제 MPEG frame count로 duration을 교차 검증한다. MP3 duration tolerance는 48 kHz MPEG Layer III frame 두 개, 즉 48 ms를 초과할 수 없다.

## 이유

- 확장자와 선언 MIME은 content authority가 아니다.
- FFprobe metadata만으로 trailing corruption을 증명할 수 없다.
- Generic ingestion에 FFmpeg dependency와 비용을 추가하지 않는다.
- process restart 후 durable payload를 재검증할 수 있어 process-local verdict가 필요 없다.

## 영향

- exact digital silence와 낮은 loudness는 기술적으로 유효하면 허용한다.
- LUFS/peak quality 판단은 `CanonicalWavExportAnalyzer`가 계속 담당한다.
- validation failure는 raw tool output을 노출하지 않는 typed internal error로 닫힌다.
- recovery는 별도 validation evidence column 없이 full validation을 반복한다.
- migration과 public API 변경은 없다.

## 재검토 조건

- canonical sample rate/channel/bit depth 변경
- FFmpeg 이외 decoder authority 도입
- durable validation evidence persistence 도입
- MP3 codec 또는 frame structure 변경
