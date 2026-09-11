# ADR-068: Canonical Multi-Format Export Encoding

- 상태: 승인
- 작성일: 2026-09-10
- 관련 ADR: [ADR-067](ADR-067-trusted-export-delivery-validation.md)

## 결정

모든 Export는 frozen Snapshot을 48 kHz, stereo, PCM16 WAV로 한 번 렌더링한다. WAV는 이 결과를 그대로 전달하고, MP3와 FLAC만 동일한 canonical intermediate에서 정적 FFmpeg 인자로 인코딩한다.

- MP3: `libmp3lame`, CBR 320 kbps, 48 kHz stereo, metadata 제거
- FLAC: `flac`, compression level 8, 48 kHz stereo PCM16, metadata 제거
- 사용자 codec 또는 FFmpeg 인자: 허용하지 않음

delivery payload는 ADR-067 validator를 통과한 뒤에만 SHA-256과 크기를 ledger에 고정하고 publish-or-adopt한다. deterministic publication identity의 확장자는 ledger format과 일치해야 하며 mismatch는 덮어쓰기 없이 거부한다.

FLAC은 decoded PCM byte equality로 무손실을 증명한다. MP3는 encoder delay/padding을 정렬하고 양끝 1 Layer III frame을 제외한 뒤 SNR과 normalized correlation으로 손실 품질을 검증한다. 320 kbps deterministic fixture의 측정값은 SNR 20.703 dB, correlation 0.995747이며 회귀 경계는 각각 20 dB와 0.995다. WAV, MP3, FLAC은 별도 Worker나 completion service를 만들지 않고 동일 publication, trusted registration, completion UoW를 사용한다.
