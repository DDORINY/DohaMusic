"""AssetVersion 중심 Workspace 목표 Entity의 명시적 등록 지점."""

from backend.models.workspace.asset import (
    Artifact,
    Asset,
    AssetRelation,
    AssetVersion,
)
from backend.models.workspace.collaboration import (
    Approval,
    Comment,
    Favorite,
    History,
    RecordingEnrollment,
    Tag,
)
from backend.models.workspace.composition import (
    CompositionClip,
    CompositionSnapshot,
    CompositionSnapshotClip,
    CompositionSnapshotTrack,
    CompositionTrack,
    ProcessingChain,
    ProcessingStep,
    ProjectCompositionSelection,
    SnapshotItem,
    WorkingComposition,
)
from backend.models.workspace.composition_history import (
    WorkingCompositionHistoryEntry,
    WorkingCompositionHistoryState,
)
from backend.models.workspace.enums import AssetType, JobStatus
from backend.models.workspace.export import (
    ExportPublicationState,
    JobExportPublication,
    JobExportResult,
)
from backend.models.workspace.job import Job, JobInput, JobOutput, ModelUsage
from backend.models.workspace.payload_locator import PayloadLocator
from backend.models.workspace.preview import (
    WorkingPreviewAsset,
    WorkingPreviewRender,
    WorkingPreviewRenderClip,
    WorkingPreviewRenderTrack,
)
from backend.models.workspace.provider_job import ProviderJobBinding
from backend.models.workspace.storage import (
    ARTIFACT_STORAGE_ENTITY_CLASSES,
    ArtifactStorageLocation,
)
from backend.models.workspace.workspace import MusicProject, ProjectAsset, Workspace

WORKSPACE_ENTITY_CLASSES = (
    Workspace,
    MusicProject,
    ProjectAsset,
    Asset,
    AssetVersion,
    Artifact,
    AssetRelation,
    CompositionSnapshot,
    WorkingComposition,
    CompositionTrack,
    CompositionClip,
    CompositionSnapshotTrack,
    CompositionSnapshotClip,
    ProjectCompositionSelection,
    SnapshotItem,
    Job,
    JobInput,
    JobOutput,
    JobExportResult,
    JobExportPublication,
    ProcessingChain,
    ProcessingStep,
    ModelUsage,
    ProviderJobBinding,
    PayloadLocator,
    WorkingPreviewAsset,
    WorkingPreviewRender,
    WorkingPreviewRenderTrack,
    WorkingPreviewRenderClip,
    RecordingEnrollment,
    Tag,
    Comment,
    Favorite,
    History,
    Approval,
)

__all__ = [
    "ARTIFACT_STORAGE_ENTITY_CLASSES",
    "WORKSPACE_ENTITY_CLASSES",
    "Approval",
    "Artifact",
    "ArtifactStorageLocation",
    "Asset",
    "AssetRelation",
    "AssetType",
    "AssetVersion",
    "Comment",
    "CompositionClip",
    "CompositionSnapshot",
    "CompositionSnapshotClip",
    "CompositionSnapshotTrack",
    "CompositionTrack",
    "Favorite",
    "History",
    "Job",
    "JobInput",
    "JobOutput",
    "JobExportResult",
    "JobExportPublication",
    "ExportPublicationState",
    "JobStatus",
    "ModelUsage",
    "MusicProject",
    "ProcessingChain",
    "ProcessingStep",
    "ProjectAsset",
    "ProjectCompositionSelection",
    "ProviderJobBinding",
    "PayloadLocator",
    "RecordingEnrollment",
    "SnapshotItem",
    "Tag",
    "WorkingComposition",
    "WorkingCompositionHistoryEntry",
    "WorkingCompositionHistoryState",
    "WorkingPreviewAsset",
    "WorkingPreviewRender",
    "WorkingPreviewRenderClip",
    "WorkingPreviewRenderTrack",
    "Workspace",
]
