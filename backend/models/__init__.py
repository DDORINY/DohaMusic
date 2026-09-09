"""Database model exports used by Alembic metadata discovery."""

from backend.models.generated_file import GeneratedFile
from backend.models.generation_job import GenerationJob
from backend.models.idempotency_record import IdempotencyRecord
from backend.models.lyrics_document import LyricsDocument
from backend.models.pipeline_file import PipelineFile
from backend.models.pipeline_job import PipelineJob
from backend.models.project import Project
from backend.models.stem_file import StemFile
from backend.models.stem_job import StemJob
from backend.models.voice_conversion_file import VoiceConversionFile
from backend.models.voice_conversion_job import VoiceConversionJob
from backend.models.voice_enrollment import VoiceEnrollment
from backend.models.voice_profile import VoiceProfile
from backend.models.voice_sample import VoiceSample
from backend.models.workspace import (
    ARTIFACT_STORAGE_ENTITY_CLASSES,
    WORKSPACE_ENTITY_CLASSES,
    Approval,
    Artifact,
    ArtifactStorageLocation,
    Asset,
    AssetRelation,
    AssetVersion,
    Comment,
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    Favorite,
    History,
    Job,
    JobExportPublication,
    JobExportResult,
    JobInput,
    JobOutput,
    ModelUsage,
    MusicProject,
    PayloadLocator,
    ProcessingChain,
    ProcessingStep,
    ProjectAsset,
    ProjectCompositionSelection,
    ProviderJobBinding,
    RecordingEnrollment,
    SnapshotItem,
    Tag,
    WorkingComposition,
    WorkingCompositionHistoryEntry,
    WorkingCompositionHistoryState,
    Workspace,
)

__all__ = [
    "ARTIFACT_STORAGE_ENTITY_CLASSES",
    "WORKSPACE_ENTITY_CLASSES",
    "Approval",
    "Artifact",
    "ArtifactStorageLocation",
    "Asset",
    "AssetRelation",
    "AssetVersion",
    "Comment",
    "CompositionClip",
    "CompositionSnapshot",
    "CompositionSnapshotClip",
    "CompositionSnapshotTrack",
    "CompositionTrack",
    "Favorite",
    "GeneratedFile",
    "GenerationJob",
    "History",
    "IdempotencyRecord",
    "Job",
    "JobExportPublication",
    "JobInput",
    "JobOutput",
    "JobExportResult",
    "LyricsDocument",
    "ModelUsage",
    "MusicProject",
    "PipelineFile",
    "PipelineJob",
    "ProcessingChain",
    "ProcessingStep",
    "Project",
    "ProjectAsset",
    "ProjectCompositionSelection",
    "ProviderJobBinding",
    "PayloadLocator",
    "RecordingEnrollment",
    "SnapshotItem",
    "StemFile",
    "StemJob",
    "Tag",
    "VoiceConversionFile",
    "VoiceConversionJob",
    "VoiceEnrollment",
    "VoiceProfile",
    "VoiceSample",
    "WorkingComposition",
    "WorkingCompositionHistoryEntry",
    "WorkingCompositionHistoryState",
    "Workspace",
]
