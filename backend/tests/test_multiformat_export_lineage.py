"""Shared-fixture multi-format lineage and explicit re-export proofs."""

from pathlib import Path

import pytest

from backend.models.workspace import (
    Artifact,
    AssetVersion,
    ExportPublicationState,
    JobExportPublication,
    JobExportResult,
    JobStatus,
)
from backend.tests.support.multiformat_export_fixture import (
    MultiFormatExportIntegrationFixture,
)


def test_same_snapshot_three_format_lineage_and_cardinality(tmp_path: Path) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        completed = [fixture.execute(item, f"three-{item}") for item in ("wav", "mp3", "flac")]
        assert all(item[0].aggregate.job.status is JobStatus.QUEUED for item in completed)
        counts = fixture.export_counts()
        assert counts == {
            "assets": 1,
            "memberships": 1,
            "versions": 3,
            "artifacts": 3,
            "locations": 3,
            "outputs": 3,
            "results": 3,
            "publications": 3,
            "physical": 3,
        }
        job_ids = [item[0].aggregate.job.job_id for item in completed]
        assert {item.status for item in fixture.completed_jobs(job_ids)} == {JobStatus.SUCCEEDED}
        with fixture.base.factory() as session:
            results = session.query(JobExportResult).all()
            publications = session.query(JobExportPublication).all()
            versions = (
                session.query(AssetVersion)
                .filter(
                    AssetVersion.asset_version_id.in_(
                        [item.exported_asset_version_id for item in results]
                    )
                )
                .all()
            )
            artifacts = (
                session.query(Artifact)
                .filter(Artifact.artifact_id.in_([item.exported_artifact_id for item in results]))
                .all()
            )
            assert {item.export_format for item in results} == {"wav", "mp3", "flac"}
            assert {item.export_format for item in publications} == {"wav", "mp3", "flac"}
            assert {item.state for item in publications} == {ExportPublicationState.COMPLETED}
            assert len({item.asset_id for item in versions}) == 1
            assert {item.storage_key.rsplit(".", 1)[-1] for item in publications} == {
                "wav",
                "mp3",
                "flac",
            }
            assert {item.media_type for item in artifacts} == {
                "audio/wav",
                "audio/mpeg",
                "audio/flac",
            }
    finally:
        fixture.close()


@pytest.mark.parametrize("export_format", ["mp3", "flac"])
def test_explicit_reexport_creates_new_version_on_same_export_asset(
    tmp_path: Path, export_format: str
) -> None:
    fixture = MultiFormatExportIntegrationFixture(tmp_path)
    try:
        first, first_result = fixture.execute(export_format, "first")
        replay = fixture.create_job(export_format, "first")
        second, second_result = fixture.execute(export_format, "second")
        assert replay.replayed
        assert replay.aggregate.job.job_id == first.aggregate.job.job_id
        assert second.aggregate.job.job_id != first.aggregate.job.job_id
        assert second_result.export_result.exported_asset_version_id != (
            first_result.export_result.exported_asset_version_id
        )
        assert fixture.export_counts() == {
            "assets": 1,
            "memberships": 1,
            "versions": 2,
            "artifacts": 2,
            "locations": 2,
            "outputs": 2,
            "results": 2,
            "publications": 2,
            "physical": 2,
        }
    finally:
        fixture.close()
