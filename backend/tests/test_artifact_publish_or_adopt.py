"""Trusted deterministic Artifact publication and crash-recovery contract."""

from __future__ import annotations

import hashlib
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from backend.storage.artifact_publisher import (
    ArtifactPublishError,
    ArtifactPublishErrorCode,
    LocalArtifactPublisher,
    PublicationOutcome,
    TrustedPublicationIdentity,
)
from backend.storage.artifact_resolver import APPROVED_STORAGE_DOMAINS, ArtifactStorageRoots


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as payload:
        payload.setnchannels(2)
        payload.setsampwidth(2)
        payload.setframerate(48_000)
        payload.writeframes(b"\x00\x00\x00\x00" * 32)
    return output.getvalue()


def _publisher(tmp_path: Path) -> tuple[LocalArtifactPublisher, Path]:
    artifact_root = tmp_path / "artifacts"
    staging_root = tmp_path / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    for domain in APPROVED_STORAGE_DOMAINS:
        (artifact_root / domain).mkdir(parents=True, exist_ok=True)
    return LocalArtifactPublisher(
        ArtifactStorageRoots.from_base_root(artifact_root), staging_root
    ), staging_root


def _stage(root: Path, name: str, content: bytes) -> Path:
    path = root / name
    path.write_bytes(content)
    return path.resolve()


def _publish(publisher, source, identity, content):
    return publisher.publish_or_adopt(
        source,
        identity=identity,
        artifact_kind="audio",
        expected_media_type="audio/wav",
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size_bytes=len(content),
    )


def test_deterministic_identity_is_job_owned_and_rejects_forgery(tmp_path: Path) -> None:
    publisher, _ = _publisher(tmp_path)
    job_id = uuid4()
    identity = TrustedPublicationIdentity.for_wav_export(job_id)
    assert identity == TrustedPublicationIdentity.for_wav_export(job_id)
    assert identity.storage_key == f"exports/{job_id.hex[:2]}/{job_id}/result.wav"
    forged = TrustedPublicationIdentity(job_id, "music", "../escape.wav")
    with pytest.raises(ArtifactPublishError) as caught:
        publisher._publication_path(forged)
    assert caught.value.code is ArtifactPublishErrorCode.PUBLICATION_IDENTITY_INVALID


def test_publish_reopen_and_process_independent_exact_adoption(tmp_path: Path) -> None:
    publisher, staging = _publisher(tmp_path)
    content = _wav()
    identity = TrustedPublicationIdentity.for_wav_export(uuid4())
    created = _publish(publisher, _stage(staging, "first.wav", content), identity, content)
    assert created.outcome is PublicationOutcome.PUBLISHED_NEW

    replacement = LocalArtifactPublisher(publisher.artifact_roots, staging)
    adopted = _publish(replacement, _stage(staging, "second.wav", content), identity, content)
    assert adopted.outcome is PublicationOutcome.ADOPTED_EXISTING
    assert adopted.path == created.path
    assert len(list((publisher.artifact_roots.roots["music"] / "exports").rglob("*.wav"))) == 1
    with replacement.open_trusted_publication(
        identity,
        artifact_kind="audio",
        expected_media_type="audio/wav",
        expected_sha256=created.checksum,
        expected_size_bytes=created.size_bytes,
    ) as (verified, stream):
        assert verified.outcome is PublicationOutcome.ADOPTED_EXISTING
        assert stream.read() == content


def test_mismatch_tamper_missing_and_cleanup_are_fail_closed(tmp_path: Path) -> None:
    publisher, staging = _publisher(tmp_path)
    content = _wav()
    identity = TrustedPublicationIdentity.for_wav_export(uuid4())
    created = _publish(publisher, _stage(staging, "created.wav", content), identity, content)
    different = content[:-4] + b"\x01\x00\x01\x00"
    with pytest.raises(ArtifactPublishError) as mismatch:
        _publish(publisher, _stage(staging, "different.wav", different), identity, different)
    assert mismatch.value.code is ArtifactPublishErrorCode.PUBLICATION_INTEGRITY_MISMATCH
    assert created.path.read_bytes() == content

    adopted = _publish(publisher, _stage(staging, "adopt.wav", content), identity, content)
    assert publisher.compensate_publish_or_adopt(adopted) is False
    assert created.path.exists()
    assert publisher.compensate_publish_or_adopt(created) is True
    assert not created.path.exists()
    with pytest.raises(ArtifactPublishError) as missing:
        publisher.open_trusted_publication(
            identity,
            artifact_kind="audio",
            expected_media_type="audio/wav",
            expected_sha256=created.checksum,
            expected_size_bytes=created.size_bytes,
        ).__enter__()
    assert missing.value.code is ArtifactPublishErrorCode.PUBLICATION_NOT_FOUND


def test_concurrent_exact_converges_and_mismatch_never_overwrites(tmp_path: Path) -> None:
    publisher, staging = _publisher(tmp_path)
    content = _wav()
    identity = TrustedPublicationIdentity.for_wav_export(uuid4())
    paths = [_stage(staging, f"same-{index}.wav", content) for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: _publish(publisher, path, identity, content), paths))
    assert {item.outcome for item in results} == {
        PublicationOutcome.PUBLISHED_NEW,
        PublicationOutcome.ADOPTED_EXISTING,
    }
    assert results[0].path == results[1].path

    other = content[:-4] + b"\x02\x00\x02\x00"
    with pytest.raises(ArtifactPublishError) as caught:
        _publish(publisher, _stage(staging, "other.wav", other), identity, other)
    assert caught.value.code is ArtifactPublishErrorCode.PUBLICATION_INTEGRITY_MISMATCH
    assert results[0].path.read_bytes() == content
