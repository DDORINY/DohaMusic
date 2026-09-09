# ADR-058 - Mixer and Export Rendering Authority

> 상태: [승인]
> 작성일: 2026-09-06
> 최종 수정일: 2026-09-06
> 관련 기능: AI-native DAW D4 Mixer와 Export Foundation
> 관련 ADR: [ADR-040](ADR-040-canonical-track-clip-working-composition-authority.md), [ADR-052](ADR-052-working-composition-preview-render-authority.md), [ADR-056](ADR-056-persistent-working-composition-history.md), [ADR-057](ADR-057-working-composition-multi-user-conflict-recovery-authority.md), [ADR-059](ADR-059-typed-persistent-history-target-authority.md), [ADR-063](ADR-063-export-asset-lineage-and-completion-authority.md)

## 배경

Clip Gain, Fade, Loop, Preview, immutable Composition Snapshot과 persistent history는 구현됐지만 Track/Master Mixer와 최종 delivery Export의 canonical owner, DSP 순서와 품질 Gate는 확정되지 않았다. Preview와 Export가 서로 다른 DSP를 사용하거나 mutable WorkingComposition을 암묵 Export하면 재현성과 provenance가 깨진다.

## 결정

Mixer canonical owner는 Backend `WorkingComposition` aggregate다. `CompositionTrack`은 typed `gain_db`, `pan`, `muted`, `solo`를, `WorkingComposition`은 typed `master_gain_db`를 소유한다. 기존 `mix_settings` JSON은 이전 의미와 데이터를 보존하되 새 Mixer authority로 사용하거나 typed 값을 중복 저장하지 않는다.

Mixer persistent history target, typed dispatcher와 Clip compatibility는 ADR-059를 따른다.

기본값은 Track `0.00 dB`, pan `0.00`, unmuted, unsolo와 Master `0.00 dB`다. Track/Master mutation은 absolute state, 기존 aggregate revision CAS, Idempotency-Key와 Backend persistent journal/cursor를 사용한다. 성공은 revision을 1 증가시키며 stale request는 fail-closed한다. 별도 Mixer revision, Frontend inverse authority, 자동 replay와 hidden/per-field merge는 없다.

Mixer state는 Snapshot에 exact freeze되고 Checkout에서 exact restore된다. Preview manifest에도 freeze하며 legacy manifest는 unity/center/unmuted/unsolo와 unity Master로 해석한다. Mixer mutation은 기존 Preview를 stale로 만든다.

## DSP 순서

Preview와 Export는 다음 canonical render core를 공유한다.

```text
decode
-> source window
-> loop expansion
-> Clip Gain
-> full-timeline Clip Fade
-> timeline placement
-> per-Track sum
-> Solo/Mute gate
-> Track Gain
-> Track Pan
-> master sum
-> Master Gain
-> output
```

Solo가 하나라도 있으면 `effective_audible = !muted && solo`, 없으면 `effective_audible = !muted`다. Mute가 Solo보다 우선하고 multiple Solo는 개별 mute되지 않은 모든 Solo Track을 재생한다.

## Pan 계약

Pan은 finite Decimal `-1.00..1.00`이며 clamp하지 않는다. `-1`은 full left, `0`은 center, `+1`은 full right다. Web Audio `StereoPannerNode`의 deterministic equal-power 알고리즘을 사용한다.

Mono에서 `x=(pan+1)/2`, `gainL=cos(x*pi/2)`, `gainR=sin(x*pi/2)`이고 `outL=in*gainL`, `outR=in*gainR`다.

Stereo에서는 pan이 0 이하면 `x=pan+1`, `outL=inL+inR*cos(x*pi/2)`, `outR=inR*sin(x*pi/2)`다. pan이 0보다 크면 `x=pan`, `outL=inL*cos(x*pi/2)`, `outR=inR+inL*sin(x*pi/2)`다. Center는 원 stereo channel을 그대로 보존하며 mono downmix, ad-hoc balance와 hidden gain correction은 없다. Frontend Web Audio와 Backend renderer는 같은 식을 검증한다.

## Export authority

Export Asset/AssetVersion lineage, `JobOutput`과 `JobExportResult`의 역할, final DB completion transaction ownership은 ADR-063이 구체화한다.

Export source는 요청에서 명시한 immutable Composition Snapshot만 허용한다. latest Snapshot 추측과 WorkingComposition direct/implicit-commit Export는 금지한다. Export 시작 이후 WorkingComposition 변경은 source audio, geometry, Mixer와 provenance에 영향을 주지 않는다.

기존 Workspace Job framework에 typed Export kind/result를 추가한다. 결과는 기존 source Asset을 덮지 않는 format별 새 exported audio Asset, 최초 immutable AssetVersion과 Artifact다. 동일 Snapshot의 WAV/MP3/FLAC은 별도 deliverable lineage다. Foundation은 WAV를 실행 가능하게 구현하고 enum과 fingerprint는 MP3/FLAC 확장을 수용하되 미구현 format은 fail-closed한다.

Export execution result의 canonical owner는 Workspace Job과 PK/FK를 공유하는 `job_export_results` typed 1:1 table이다. Export가 완료되지 않은 Job에는 result row가 없고, service는 Export job type에만 result 생성을 허용한다. `settings_snapshot`은 requested format, canonical render/encoding settings와 quality policy reference를 고정하는 request authority이며 측정 결과를 저장하지 않는다. `JobOutput`은 AssetVersion/Artifact role과 order를 연결할 뿐 Export metadata authority가 아니다.

`JobExportResult`는 source Snapshot, format, Backend render fingerprint, exported Asset/AssetVersion/Artifact references, LUFS-I와 True Peak 측정값, 적용 threshold와 pass flags, analyzer identity/version을 typed column으로 보존한다. SHA-256, byte size, sample rate, channel count, duration과 encoding 같은 binary/media metadata의 canonical owner는 기존 Artifact/AssetVersion authority이며 result는 해당 resource를 참조한다. arbitrary result JSON은 사용하지 않는다.

Canonical render, quality PASS, Asset/AssetVersion/Artifact와 JobOutput 확보, JobExportResult 생성, Job COMPLETED 전이는 하나의 fail-closed completion transaction에서 성립해야 한다. Quality/render/analyzer/persistence 실패에는 completed result row와 completed Artifact가 없다. same-key response-loss replay는 기존 Job, result와 resource identity를 그대로 반환하며 새 render 또는 row를 만들지 않는다.

Fingerprint는 Snapshot ID, format과 canonical render/encoding settings를 포함한다. same key와 same fingerprint는 같은 Job/AssetVersion/Artifact를 replay하고 다른 fingerprint는 기존 idempotency conflict다. raw path를 반환하지 않고 기존 Owner/Workspace/Project/Snapshot authorization과 trusted Artifact content/download authority를 재사용한다.

## Export 품질 Gate

Quality authority는 final 48 kHz stereo PCM 전체 program을 FFmpeg EBU R128/동등한 deterministic analyzer로 측정한 Integrated Loudness와 모든 channel의 최대 True Peak다. 결과는 0.01 LU/dB precision Decimal로 정규화한다.

- target: `-14.00 LUFS-I`
- 허용: `-15.00..-13.00 LUFS-I` inclusive
- maximum True Peak: `-1.00 dBTP` inclusive
- RMS, short-term/momentary loudness와 sample peak는 pass/fail authority가 아니다.

범위 밖 loudness, threshold 초과 True Peak, silence/`-inf`, non-finite/parse/analyzer failure는 fail-closed한다. `LOUDNESS_GATE_FAILED`, `TRUE_PEAK_GATE_FAILED`, `EXPORT_QUALITY_ANALYSIS_FAILED` typed failure를 사용하고 completed Artifact를 만들지 않는다. 자동 normalization, limiter, compressor와 peak normalization은 0이다. 짧은 audio도 유효 측정이면 같은 Gate를 적용하고 길이로 면제하지 않는다.

Job typed Export result가 operational quality result authority이며 Artifact provenance는 immutable delivery copy/reference다. provenance는 Workspace, Project, Snapshot, Job, format, render fingerprint, Asset/Version/Artifact identity, SHA-256, byte size, media metadata, analyzer identity/version, 측정값, threshold와 각 Gate 결과를 추적한다. raw FFmpeg stderr는 사용자에게 노출하지 않는다.

## 실패와 transaction

Render, analyzer, encoding 또는 persistence 실패 시 Job은 FAILED이며 completed Artifact와 false success는 없다. 임시 파일은 authority가 아니며 publish와 completion은 검증된 결과에만 수행한다. 동일 failed Snapshot을 몰래 보정하지 않고 Mixer 수정, 새 Snapshot commit과 새 Export request를 요구한다.

## 제외 범위

대규모 Mixer/Export Frontend UI, automation, EQ, bus/send, normalization/limiting, MP3/FLAC encoder, soft-warning Export, format별 품질 목표와 실제 사용자 DB migration은 이번 Foundation 범위가 아니다.

## 재검토 조건

다중 channel output, format별 mastering policy, automation, soft warning delivery 또는 별도 distributed Export worker가 필요해지면 새 ADR로 재검토한다.
