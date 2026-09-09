# ADR-066: Silent Export Quality Representation

## Status

Accepted.

## Decision

Canonical WAV Export treats exact digital silence and finite low loudness as valid user-authored
audio. Loudness target and true-peak flags are diagnostic mastering observations and do not gate Job
completion. Missing, empty, corrupt, non-canonical, or undecodable WAV data remains fail-closed.

Exact digital silence is proven from decoded PCM samples. Its mathematical integrated loudness and
true peak are negative infinity, so `JobExportResult.integrated_loudness_lufs` and
`true_peak_dbtp` are stored as `NULL`; no finite sentinel or synthetic noise is permitted. A completed
result row and its completion authority distinguish analyzed silence from analysis not performed.

Revision `20260908_0032` changes only these two columns to nullable. Downgrade to 0031 succeeds when
no NULL metrics exist and fails closed with `SILENT_EXPORT_QUALITY_DOWNGRADE_BLOCKED` otherwise.

## Consequences

ADR-058's silence and loudness-target failure rule is superseded by this decision. Its canonical WAV,
Mixer ordering, no-normalization, and no-limiter decisions remain authoritative.
