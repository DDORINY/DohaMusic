"""승인된 staging Payload를 local Artifact root에 불변 publish한다."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from backend.storage.artifact_integrity import calculate_artifact_integrity
from backend.storage.artifact_media import (
    ArtifactMediaValidationError,
    ValidatedArtifactMedia,
    validate_artifact_media,
)
from backend.storage.artifact_resolver import (
    ArtifactStorageError,
    ArtifactStorageRoots,
    assert_safe_local_path,
    open_regular_local_file,
    validate_local_root,
)

COPY_CHUNK_SIZE = 1024 * 1024


class ArtifactPublishErrorCode(StrEnum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INVALID_STAGING_PAYLOAD = "INVALID_STAGING_PAYLOAD"
    MEDIA_VALIDATION_FAILED = "MEDIA_VALIDATION_FAILED"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    MEDIA_TYPE_MISMATCH = "MEDIA_TYPE_MISMATCH"
    PUBLISH_COLLISION = "PUBLISH_COLLISION"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PUBLICATION_NOT_FOUND = "PUBLICATION_NOT_FOUND"
    PUBLICATION_IDENTITY_INVALID = "PUBLICATION_IDENTITY_INVALID"
    PUBLICATION_INTEGRITY_MISMATCH = "PUBLICATION_INTEGRITY_MISMATCH"


class ArtifactPublishError(RuntimeError):
    def __init__(self, code: ArtifactPublishErrorCode) -> None:
        super().__init__("Artifact local publish failed.")
        self.code = code


@dataclass(frozen=True, slots=True)
class PublishedLocalPayload:
    path: Path
    storage_key: str
    size_bytes: int
    checksum: str
    media: ValidatedArtifactMedia
    file_identity: tuple[int, int]
    source_path: Path
    source_identity: tuple[int, int]


class PublicationOutcome(StrEnum):
    PUBLISHED_NEW = "published_new"
    ADOPTED_EXISTING = "adopted_existing"


@dataclass(frozen=True, slots=True)
class TrustedPublicationIdentity:
    """Internal Job-owned storage identity; never constructed from user path input."""

    job_id: UUID
    storage_domain: str
    storage_key: str

    @classmethod
    def for_export(cls, job_id: UUID, export_format: str) -> TrustedPublicationIdentity:
        if type(job_id) is not UUID:
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_IDENTITY_INVALID)
        normalized = export_format.strip().lower()
        if normalized not in {"wav", "mp3", "flac"}:
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_IDENTITY_INVALID)
        return cls(job_id, "music", f"exports/{job_id.hex[:2]}/{job_id}/result.{normalized}")

    @classmethod
    def for_wav_export(cls, job_id: UUID) -> TrustedPublicationIdentity:
        return cls.for_export(job_id, "wav")


@dataclass(frozen=True, slots=True)
class PublishOrAdoptResult:
    identity: TrustedPublicationIdentity
    outcome: PublicationOutcome
    path: Path
    size_bytes: int
    checksum: str
    media: ValidatedArtifactMedia
    file_identity: tuple[int, int]
    source_path: Path | None = None
    source_identity: tuple[int, int] | None = None


class LocalArtifactPublisher:
    """경로·bytes·MIME를 검증하고 overwrite 없는 local publish를 수행한다."""

    def __init__(
        self,
        artifact_roots: ArtifactStorageRoots,
        staging_root: Path | None,
    ) -> None:
        if staging_root is None:
            raise ArtifactPublishError(ArtifactPublishErrorCode.CONFIGURATION_ERROR)
        try:
            validated_staging_root = validate_local_root(staging_root)
        except ArtifactStorageError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.CONFIGURATION_ERROR) from None
        if any(
            _paths_overlap(validated_staging_root, root) for root in artifact_roots.roots.values()
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.CONFIGURATION_ERROR)
        self.artifact_roots = artifact_roots
        self.staging_root = validated_staging_root

    def publish(
        self,
        temporary_path: Path,
        *,
        artifact_id: UUID,
        artifact_kind: str,
        storage_domain: str,
        expected_media_type: str | None,
        expected_sha256: str | None,
    ) -> PublishedLocalPayload:
        source_path, source_identity = self._resolve_staging_payload(temporary_path)
        root = self.artifact_roots.roots[storage_domain]
        pending_dir = _ensure_directory(root, root / ".ingestion")
        pending_path = pending_dir / f"{artifact_id}.pending"
        checksum, size_bytes, pending_identity = _copy_exclusive(
            source_path,
            pending_path,
            staging_root=self.staging_root,
        )
        final_path: Path | None = None
        final_identity: tuple[int, int] | None = None
        try:
            media = validate_artifact_media(
                pending_path,
                artifact_kind=artifact_kind,
                size_bytes=size_bytes,
            )
            if expected_media_type is not None and media.media_type != expected_media_type:
                raise ArtifactPublishError(ArtifactPublishErrorCode.MEDIA_TYPE_MISMATCH)
            if expected_sha256 is not None and checksum != expected_sha256:
                raise ArtifactPublishError(ArtifactPublishErrorCode.CHECKSUM_MISMATCH)

            storage_key = _build_storage_key(
                artifact_id,
                artifact_kind=artifact_kind,
                storage_domain=storage_domain,
                extension=media.extension,
            )
            final_path = self.artifact_roots.candidate_path(storage_domain, storage_key)
            _ensure_directory(root, final_path.parent)
            try:
                os.link(pending_path, final_path)
            except FileExistsError:
                raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_COLLISION) from None
            except OSError:
                raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED) from None

            final_identity = pending_identity
            final_stat = final_path.stat(follow_symlinks=False)
            observed_final_identity = (final_stat.st_dev, final_stat.st_ino)
            if (
                not stat.S_ISREG(final_stat.st_mode)
                or observed_final_identity != pending_identity
                or final_stat.st_size != size_bytes
            ):
                raise ArtifactPublishError(ArtifactPublishErrorCode.VERIFICATION_FAILED)
            pending_path.unlink()
            _sync_directory(final_path.parent)
            return PublishedLocalPayload(
                path=final_path,
                storage_key=storage_key,
                size_bytes=size_bytes,
                checksum=checksum,
                media=media,
                file_identity=observed_final_identity,
                source_path=source_path,
                source_identity=source_identity,
            )
        except ArtifactMediaValidationError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.MEDIA_VALIDATION_FAILED) from None
        except ArtifactPublishError:
            if final_path is not None and final_identity is not None:
                _unlink_if_identity_matches(final_path, final_identity)
            raise
        finally:
            _unlink_if_identity_matches(pending_path, pending_identity)

    def publish_or_adopt(
        self,
        temporary_path: Path,
        *,
        identity: TrustedPublicationIdentity,
        artifact_kind: str,
        expected_media_type: str,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> PublishOrAdoptResult:
        """Publish once or adopt an exact existing internal publication."""
        final_path = self._publication_path(identity)
        source_path, source_identity = self._resolve_staging_payload(temporary_path)
        source_integrity = _integrity_from_regular_file(self.staging_root, source_path)
        if (
            source_integrity.checksum != expected_sha256
            or source_integrity.size_bytes != expected_size_bytes
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_INTEGRITY_MISMATCH)
        try:
            source_media = validate_artifact_media(
                source_path, artifact_kind=artifact_kind, size_bytes=expected_size_bytes
            )
        except ArtifactMediaValidationError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.MEDIA_VALIDATION_FAILED) from None
        if source_media.media_type != expected_media_type:
            raise ArtifactPublishError(ArtifactPublishErrorCode.MEDIA_TYPE_MISMATCH)

        root = self.artifact_roots.roots[identity.storage_domain]
        pending_dir = _ensure_directory(root, root / ".ingestion")
        pending_path = pending_dir / f"publication-{uuid4()}.pending"
        checksum, size_bytes, pending_identity = _copy_exclusive(
            source_path, pending_path, staging_root=self.staging_root
        )
        try:
            _ensure_directory(root, final_path.parent)
            try:
                os.link(pending_path, final_path)
                final_stat = final_path.stat(follow_symlinks=False)
                file_identity = (final_stat.st_dev, final_stat.st_ino)
                if (
                    not stat.S_ISREG(final_stat.st_mode)
                    or file_identity != pending_identity
                    or final_stat.st_size != size_bytes
                ):
                    _unlink_if_identity_matches(final_path, pending_identity)
                    raise ArtifactPublishError(ArtifactPublishErrorCode.VERIFICATION_FAILED)
                _sync_directory(final_path.parent)
                return PublishOrAdoptResult(
                    identity,
                    PublicationOutcome.PUBLISHED_NEW,
                    final_path,
                    size_bytes,
                    checksum,
                    source_media,
                    file_identity,
                    source_path,
                    source_identity,
                )
            except FileExistsError:
                return self._verify_existing_publication(
                    identity,
                    artifact_kind=artifact_kind,
                    expected_media_type=expected_media_type,
                    expected_sha256=expected_sha256,
                    expected_size_bytes=expected_size_bytes,
                )
            except OSError:
                raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED) from None
        finally:
            _unlink_if_identity_matches(pending_path, pending_identity)

    @contextmanager
    def open_trusted_publication(
        self,
        identity: TrustedPublicationIdentity,
        *,
        artifact_kind: str,
        expected_media_type: str,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> Iterator[tuple[PublishOrAdoptResult, BinaryIO]]:
        verified = self._verify_existing_publication(
            identity,
            artifact_kind=artifact_kind,
            expected_media_type=expected_media_type,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        root = self.artifact_roots.roots[identity.storage_domain]
        try:
            descriptor, observed = open_regular_local_file(root, verified.path)
        except ArtifactStorageError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.VERIFICATION_FAILED) from None
        if (observed.st_dev, observed.st_ino) != verified.file_identity:
            os.close(descriptor)
            raise ArtifactPublishError(ArtifactPublishErrorCode.VERIFICATION_FAILED)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            yield verified, stream

    def compensate_publish_or_adopt(self, result: PublishOrAdoptResult) -> bool:
        if result.outcome is PublicationOutcome.ADOPTED_EXISTING:
            return False
        removed = _unlink_if_identity_matches(result.path, result.file_identity)
        if removed:
            _sync_directory(result.path.parent)
        return removed

    def _publication_path(self, identity: TrustedPublicationIdentity) -> Path:
        if (
            not isinstance(identity, TrustedPublicationIdentity)
            or identity.storage_domain != "music"
            or identity.storage_key
            not in {
                f"exports/{identity.job_id.hex[:2]}/{identity.job_id}/result.{extension}"
                for extension in ("wav", "mp3", "flac")
            }
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_IDENTITY_INVALID)
        try:
            return self.artifact_roots.candidate_path(identity.storage_domain, identity.storage_key)
        except ArtifactStorageError:
            raise ArtifactPublishError(
                ArtifactPublishErrorCode.PUBLICATION_IDENTITY_INVALID
            ) from None

    def _verify_existing_publication(
        self,
        identity: TrustedPublicationIdentity,
        *,
        artifact_kind: str,
        expected_media_type: str,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> PublishOrAdoptResult:
        final_path = self._publication_path(identity)
        root = self.artifact_roots.roots[identity.storage_domain]
        if not final_path.exists():
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_NOT_FOUND)
        try:
            integrity = _integrity_from_regular_file(root, final_path)
            media = validate_artifact_media(
                final_path, artifact_kind=artifact_kind, size_bytes=integrity.size_bytes
            )
            payload_stat = final_path.stat(follow_symlinks=False)
        except ArtifactMediaValidationError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.MEDIA_VALIDATION_FAILED) from None
        except (ArtifactStorageError, OSError):
            raise ArtifactPublishError(ArtifactPublishErrorCode.VERIFICATION_FAILED) from None
        if (
            integrity.checksum != expected_sha256
            or integrity.size_bytes != expected_size_bytes
            or media.media_type != expected_media_type
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLICATION_INTEGRITY_MISMATCH)
        return PublishOrAdoptResult(
            identity,
            PublicationOutcome.ADOPTED_EXISTING,
            final_path,
            integrity.size_bytes,
            integrity.checksum,
            media,
            (payload_stat.st_dev, payload_stat.st_ino),
        )

    def compensate(self, published: PublishedLocalPayload) -> bool:
        removed = _unlink_if_identity_matches(published.path, published.file_identity)
        if removed:
            _sync_directory(published.path.parent)
        return removed

    def cleanup_staging(self, published: PublishedLocalPayload) -> bool:
        removed = _unlink_if_identity_matches(published.source_path, published.source_identity)
        if removed:
            _sync_directory(published.source_path.parent)
        return removed

    def discard_staging(self, temporary_path: Path) -> bool:
        """실패한 handoff의 staging payload만 identity 확인 후 제거한다."""

        try:
            source_path, source_identity = self._resolve_staging_payload(temporary_path)
        except ArtifactPublishError:
            return False
        removed = _unlink_if_identity_matches(source_path, source_identity)
        if removed:
            _sync_directory(source_path.parent)
        return removed

    def _resolve_staging_payload(self, requested_path: Path) -> tuple[Path, tuple[int, int]]:
        if not requested_path.is_absolute() or any(
            part in {".", ".."} for part in requested_path.parts
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.INVALID_STAGING_PAYLOAD)
        try:
            requested_path.relative_to(self.staging_root)
            assert_safe_local_path(self.staging_root, requested_path)
            resolved = requested_path.resolve(strict=True)
            resolved.relative_to(self.staging_root)
            descriptor, descriptor_stat = open_regular_local_file(self.staging_root, resolved)
        except (ArtifactStorageError, OSError, RuntimeError, ValueError):
            raise ArtifactPublishError(ArtifactPublishErrorCode.INVALID_STAGING_PAYLOAD) from None
        os.close(descriptor)
        return resolved, (descriptor_stat.st_dev, descriptor_stat.st_ino)


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _ensure_directory(root: Path, target: Path) -> Path:
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED) from None
    current = root
    for part in parts:
        current /= part
        with suppress(FileExistsError):
            current.mkdir()
        try:
            assert_safe_local_path(root, current)
        except ArtifactStorageError:
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED) from None
        if not current.is_dir():
            raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED)
    return target


def _copy_exclusive(
    source_path: Path,
    destination_path: Path,
    *,
    staging_root: Path,
) -> tuple[str, int, tuple[int, int]]:
    try:
        source_descriptor, source_stat = open_regular_local_file(staging_root, source_path)
    except ArtifactStorageError:
        raise ArtifactPublishError(ArtifactPublishErrorCode.INVALID_STAGING_PAYLOAD) from None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        destination_descriptor = os.open(destination_path, flags, 0o600)
    except FileExistsError:
        os.close(source_descriptor)
        raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_COLLISION) from None
    except OSError:
        os.close(source_descriptor)
        raise ArtifactPublishError(ArtifactPublishErrorCode.PUBLISH_FAILED) from None

    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with (
            os.fdopen(source_descriptor, "rb", closefd=True) as source,
            os.fdopen(destination_descriptor, "wb", closefd=True) as destination,
        ):
            while chunk := source.read(COPY_CHUNK_SIZE):
                destination.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        source_after = source_path.stat(follow_symlinks=False)
        if (
            (source_after.st_dev, source_after.st_ino) != (source_stat.st_dev, source_stat.st_ino)
            or source_after.st_size != source_stat.st_size
            or size_bytes != source_stat.st_size
        ):
            raise ArtifactPublishError(ArtifactPublishErrorCode.INVALID_STAGING_PAYLOAD)
        destination_stat = destination_path.stat(follow_symlinks=False)
        return (
            digest.hexdigest(),
            size_bytes,
            (destination_stat.st_dev, destination_stat.st_ino),
        )
    except Exception:
        _unlink_unchecked(destination_path)
        raise


def _build_storage_key(
    artifact_id: UUID,
    *,
    artifact_kind: str,
    storage_domain: str,
    extension: str,
) -> str:
    namespace = (
        "snapshots"
        if storage_domain == "music" and artifact_kind == "snapshot"
        else "runs"
        if storage_domain == "music"
        else f"payloads/{artifact_kind}"
    )
    return f"{namespace}/{artifact_id.hex[:2]}/{artifact_id}.{extension}"


def _integrity_from_regular_file(root: Path, path: Path):
    descriptor, _ = open_regular_local_file(root, path)
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        return calculate_artifact_integrity(stream)


def _unlink_if_identity_matches(path: Path, identity: tuple[int, int]) -> bool:
    try:
        payload_stat = path.stat(follow_symlinks=False)
        if (payload_stat.st_dev, payload_stat.st_ino) != identity:
            return False
        path.unlink()
        return True
    except OSError:
        return False


def _unlink_unchecked(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def _sync_directory(path: Path) -> bool:
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True
    except OSError:
        return False
