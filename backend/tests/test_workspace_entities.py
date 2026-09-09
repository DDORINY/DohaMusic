"""AssetVersion 중심 Workspace 목표 Entity 계약 검증."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import configure_mappers

import backend.models  # noqa: F401
from backend.db.base import Base
from backend.models.workspace import (
    ARTIFACT_STORAGE_ENTITY_CLASSES,
    WORKSPACE_ENTITY_CLASSES,
    Artifact,
    Asset,
    AssetType,
    JobStatus,
)

EXPECTED_ENTITY_TABLES = {
    "Workspace": "workspaces",
    "MusicProject": "music_projects",
    "ProjectAsset": "project_assets",
    "Asset": "assets",
    "AssetVersion": "asset_versions",
    "Artifact": "artifacts",
    "AssetRelation": "asset_relations",
    "CompositionSnapshot": "composition_snapshots",
    "WorkingComposition": "working_compositions",
    "CompositionTrack": "composition_tracks",
    "CompositionClip": "composition_clips",
    "CompositionSnapshotTrack": "composition_snapshot_tracks",
    "CompositionSnapshotClip": "composition_snapshot_clips",
    "ProjectCompositionSelection": "project_composition_selections",
    "SnapshotItem": "snapshot_items",
    "Job": "jobs",
    "JobInput": "job_inputs",
    "JobOutput": "job_outputs",
    "JobExportResult": "job_export_results",
    "JobExportPublication": "job_export_publications",
    "ProcessingChain": "processing_chains",
    "ProcessingStep": "processing_steps",
    "ModelUsage": "model_usages",
    "ProviderJobBinding": "provider_job_bindings",
    "PayloadLocator": "payload_locators",
    "WorkingPreviewAsset": "working_preview_assets",
    "WorkingPreviewRender": "working_preview_renders",
    "WorkingPreviewRenderTrack": "working_preview_render_tracks",
    "WorkingPreviewRenderClip": "working_preview_render_clips",
    "RecordingEnrollment": "recording_enrollments",
    "Tag": "tags",
    "Comment": "comments",
    "Favorite": "favorites",
    "History": "history",
    "Approval": "approvals",
}

EXPECTED_COLUMNS = {
    "workspaces": {
        "workspace_id",
        "owner_id",
        "name",
        "lifecycle_status",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "music_projects": {
        "project_id",
        "workspace_id",
        "title",
        "description",
        "export_project_asset_id",
        "lifecycle_status",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "project_assets": {
        "project_asset_id",
        "project_id",
        "asset_id",
        "role",
        "display_order",
        "created_at",
        "deleted_at",
    },
    "assets": {
        "asset_id",
        "workspace_id",
        "owner_id",
        "asset_type",
        "selected_asset_version_id",
        "lifecycle_status",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "asset_versions": {
        "asset_version_id",
        "asset_id",
        "version_number",
        "version_origin",
        "parent_asset_version_id",
        "processing_chain_id",
        "provider_id",
        "model_manifest_id",
        "settings_snapshot",
        "created_by",
        "created_at",
    },
    "artifacts": {
        "artifact_id",
        "asset_version_id",
        "artifact_kind",
        "media_type",
        "size_bytes",
        "duration_us",
        "checksum_algorithm",
        "artifact_checksum",
        "producer_type",
        "producer_id",
        "run_id",
        "retention_status",
        "created_at",
    },
    "asset_relations": {
        "relation_id",
        "source_asset_id",
        "target_asset_id",
        "source_asset_version_id",
        "target_asset_version_id",
        "relation_type",
        "created_at",
    },
    "composition_snapshots": {
        "composition_snapshot_id",
        "project_id",
        "snapshot_version",
        "processing_chain_id",
        "mix_settings_snapshot",
        "master_gain_db",
        "provider_versions",
        "model_manifest_ids",
        "created_by",
        "created_at",
    },
    "working_compositions": {
        "working_composition_id",
        "project_id",
        "base_composition_snapshot_id",
        "mix_settings",
        "master_gain_db",
        "revision",
        "created_at",
        "updated_at",
    },
    "composition_tracks": {
        "track_id",
        "working_composition_id",
        "track_type",
        "name",
        "track_order",
        "gain_db",
        "pan",
        "muted",
        "solo",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "composition_clips": {
        "clip_id",
        "working_composition_id",
        "track_id",
        "source_asset_version_id",
        "timeline_start",
        "source_in",
        "source_out",
        "source_duration",
        "timeline_duration",
        "loop_enabled",
        "loop_phase",
        "gain_db",
        "fade_in",
        "fade_out",
        "split_from_clip_id",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "composition_snapshot_tracks": {
        "snapshot_track_id",
        "composition_snapshot_id",
        "canonical_track_id",
        "track_type",
        "name",
        "track_order",
    },
    "composition_snapshot_clips": {
        "snapshot_clip_id",
        "composition_snapshot_id",
        "snapshot_track_id",
        "canonical_clip_id",
        "source_asset_version_id",
        "timeline_start",
        "source_in",
        "source_out",
        "source_duration",
        "timeline_duration",
        "loop_enabled",
        "loop_phase",
        "gain_db",
        "fade_in",
        "fade_out",
        "split_from_clip_id",
    },
    "project_composition_selections": {
        "project_id",
        "selected_composition_snapshot_id",
        "created_at",
        "updated_at",
    },
    "snapshot_items": {
        "snapshot_item_id",
        "composition_snapshot_id",
        "asset_version_id",
        "item_role",
        "sort_order",
        "created_at",
    },
    "jobs": {
        "job_id",
        "project_id",
        "workspace_id",
        "composition_snapshot_id",
        "job_type",
        "status",
        "provider_id",
        "api_contract_version",
        "model_manifest_id",
        "progress_percent",
        "stage",
        "settings_snapshot",
        "retry_of_job_id",
        "error_code",
        "error_message",
        "error_retryable",
        "error_details_id",
        "requested_by",
        "created_at",
        "started_at",
        "completed_at",
        "cancel_requested_at",
        "claim_token",
        "claimed_by",
        "lease_expires_at",
        "heartbeat_at",
        "attempt",
    },
    "job_inputs": {
        "job_input_id",
        "job_id",
        "asset_version_id",
        "artifact_id",
        "input_role",
        "input_order",
        "created_at",
    },
    "job_outputs": {
        "job_output_id",
        "job_id",
        "asset_version_id",
        "artifact_id",
        "output_role",
        "output_order",
        "created_at",
    },
    "processing_chains": {
        "processing_chain_id",
        "name",
        "chain_version",
        "chain_checksum",
        "created_by",
        "created_at",
    },
    "processing_steps": {
        "processing_step_id",
        "processing_chain_id",
        "step_order",
        "step_type",
        "settings_snapshot",
        "created_at",
    },
    "model_usages": {
        "model_usage_id",
        "job_id",
        "asset_version_id",
        "provider_id",
        "model_manifest_id",
        "model_id",
        "model_version",
        "checkpoint_version",
        "api_contract_version",
        "license_status",
        "commercial_usage_status",
        "created_at",
    },
    "provider_job_bindings": {
        "provider_job_binding_id",
        "workspace_job_id",
        "provider_id",
        "provider_job_id",
        "retry_of_provider_job_id",
        "created_at",
    },
    "payload_locators": {
        "payload_locator_id",
        "workspace_job_id",
        "provider_job_binding_id",
        "payload_ordinal",
        "provider_artifact_id",
        "role",
        "source_kind",
        "source_id",
        "artifact_kind",
        "expected_checksum_algorithm",
        "expected_payload_checksum",
        "expected_size_bytes",
        "expected_media_type",
        "source_available_until",
        "locator_expires_at",
        "staging_status",
        "staging_backend",
        "staging_key",
        "actual_checksum_algorithm",
        "actual_payload_checksum",
        "actual_size_bytes",
        "actual_media_type",
        "verified_at",
        "ingested_artifact_id",
        "ingested_at",
        "revoked_at",
        "revocation_reason",
        "cleanup_requested_at",
        "cleanup_completed_at",
        "lifecycle_revision",
        "created_at",
        "updated_at",
    },
    "working_preview_assets": {"project_id", "asset_id"},
    "working_preview_renders": {
        "preview_render_id",
        "project_id",
        "working_composition_id",
        "rendered_revision",
        "workspace_job_id",
        "preview_asset_id",
        "preview_asset_version_id",
        "payload_expires_at",
        "created_at",
    },
    "working_preview_render_tracks": {
        "preview_render_id",
        "track_id",
        "track_order",
    },
    "working_preview_render_clips": {
        "preview_render_id",
        "clip_id",
        "track_id",
        "canonical_order",
        "source_asset_version_id",
        "source_artifact_id",
        "source_in_us",
        "source_out_us",
        "source_duration_us",
        "timeline_start_us",
        "timeline_duration_us",
        "loop_enabled",
        "loop_phase_us",
        "gain_db",
        "fade_in_us",
        "fade_out_us",
    },
    "recording_enrollments": {
        "recording_enrollment_id",
        "workspace_id",
        "recording_asset_version_id",
        "status",
        "consent_policy_version",
        "consent_evidence_id",
        "created_by",
        "created_at",
        "completed_at",
        "deleted_at",
    },
    "tags": {
        "tag_id",
        "asset_id",
        "name",
        "created_by",
        "created_at",
        "deleted_at",
    },
    "comments": {
        "comment_id",
        "asset_version_id",
        "created_by",
        "body",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "favorites": {
        "favorite_id",
        "workspace_id",
        "asset_id",
        "created_at",
        "deleted_at",
    },
    "history": {
        "history_id",
        "workspace_id",
        "actor_id",
        "entity_type",
        "entity_id",
        "action",
        "before_snapshot",
        "after_snapshot",
        "created_at",
    },
    "approvals": {
        "approval_id",
        "asset_version_id",
        "recording_enrollment_id",
        "model_usage_id",
        "usage_purpose",
        "status",
        "approved_by",
        "evidence_id",
        "decided_at",
        "created_at",
    },
}

EXPECTED_COLUMNS["composition_snapshot_tracks"].update({"gain_db", "pan", "muted", "solo"})
EXPECTED_COLUMNS["working_preview_render_tracks"].update({"gain_db", "pan", "muted", "solo"})
EXPECTED_COLUMNS["working_preview_renders"].add("master_gain_db")
EXPECTED_COLUMNS["job_export_results"] = {
    "job_id",
    "composition_snapshot_id",
    "export_format",
    "render_fingerprint",
    "exported_asset_version_id",
    "exported_artifact_id",
    "integrated_loudness_lufs",
    "true_peak_dbtp",
    "target_lufs",
    "minimum_lufs",
    "maximum_lufs",
    "maximum_true_peak_dbtp",
    "loudness_passed",
    "true_peak_passed",
    "overall_pass",
    "analyzer_name",
    "analyzer_version",
    "created_at",
}
EXPECTED_COLUMNS["job_export_publications"] = {
    "job_id",
    "composition_snapshot_id",
    "export_format",
    "storage_domain",
    "storage_key",
    "state",
    "expected_sha256",
    "expected_size_bytes",
    "artifact_id",
    "version",
    "created_at",
    "updated_at",
}

LEGACY_TABLES = {
    "generated_files",
    "generation_jobs",
    "idempotency_records",
    "lyrics_documents",
    "pipeline_files",
    "pipeline_jobs",
    "projects",
    "stem_files",
    "stem_jobs",
    "voice_conversion_files",
    "voice_conversion_jobs",
    "voice_enrollments",
    "voice_profiles",
    "voice_samples",
}


def test_workspace_entity_and_table_names_are_exact() -> None:
    actual = {entity.__name__: entity.__tablename__ for entity in WORKSPACE_ENTITY_CLASSES}

    assert len(WORKSPACE_ENTITY_CLASSES) == 35
    assert actual == EXPECTED_ENTITY_TABLES
    assert len(set(actual.values())) == 35


def test_workspace_table_columns_match_documented_contract() -> None:
    actual = {
        entity.__tablename__: set(entity.__table__.columns.keys())
        for entity in WORKSPACE_ENTITY_CLASSES
    }

    assert actual == EXPECTED_COLUMNS
    assert "project_id" not in Asset.__table__.columns
    assert Asset.__table__.c.workspace_id.nullable is True
    assert not any("path" in column.name for column in Artifact.__table__.columns)


def test_workspace_enums_match_common_contract() -> None:
    assert {item.value for item in AssetType} == {
        "lyrics",
        "music",
        "vocal",
        "stem",
        "recording",
        "mix",
        "export",
    }
    assert {item.value for item in JobStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_workspace_metadata_coexists_with_legacy_tables() -> None:
    target_tables = set(EXPECTED_ENTITY_TABLES.values())
    storage_tables = {entity.__tablename__ for entity in ARTIFACT_STORAGE_ENTITY_CLASSES}

    assert target_tables.isdisjoint(LEGACY_TABLES)
    assert storage_tables == {"artifact_storage_locations"}
    history_tables = {
        "working_composition_history_entries",
        "working_composition_history_states",
    }
    assert set(Base.metadata.tables) == (
        target_tables | storage_tables | LEGACY_TABLES | history_tables
    )
    assert len(Base.metadata.tables) == 52


def test_workspace_foreign_keys_resolve_and_relationships_are_symmetric() -> None:
    configure_mappers()

    for entity in WORKSPACE_ENTITY_CLASSES:
        for foreign_key in entity.__table__.foreign_keys:
            assert foreign_key.column.table.name in Base.metadata.tables

        mapper = inspect(entity)
        for relation in mapper.relationships:
            assert relation.back_populates is not None
            reverse = relation.mapper.relationships[relation.back_populates]
            assert reverse.back_populates == relation.key


def test_all_metadata_can_create_in_memory_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables)
