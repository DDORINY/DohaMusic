export type PipelineStatus =
  | "PENDING"
  | "VALIDATING"
  | "GENERATING"
  | "STEM_SEPARATING"
  | "VOICE_CONVERTING"
  | "MIXING"
  | "EXPORTING"
  | "CANCEL_REQUESTED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface LyricsSectionDto {
  section_type: string;
  lines: string[];
}
export interface LyricsValidationDto {
  valid: boolean;
  normalized_lyrics: string;
  sections: LyricsSectionDto[];
  warnings: string[];
  errors: string[];
  character_count: number;
  line_count: number;
  section_count: number;
  repetition_ratio: number;
}
export interface LyricsDocumentDto {
  id: string;
  parent_id: string | null;
  version: number;
  revision_instruction: string | null;
  source_hash: string | null;
  result_hash: string | null;
  title: string | null;
  language: string;
  topic: string;
  genre: string | null;
  mood: string | null;
  keywords: string[];
  structure: string[];
  sections: LyricsSectionDto[];
  full_text: string;
  provider: string;
  model_name: string;
  model_version: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface LyricsCreateDto {
  topic: string;
  genre?: string;
  mood?: string;
  language: string;
  keywords: string[];
  structure: string[];
  target_duration_seconds?: number;
  additional_instructions?: string;
  allow_template_fallback: boolean;
  generation_options?: KPopGenerationOptionsDto;
}
export interface VoiceProfileDto {
  id: string;
  name: string;
  display_filename: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  duration_seconds: number | null;
  sample_rate: number | null;
  channels: number | null;
  consent_confirmed: boolean;
  consent_text_version: string | null;
  status: "READY" | "INVALID" | "DELETED";
  quality_warnings: string[];
  created_at: string;
  updated_at: string;
}
export interface PipelineCreateDto {
  prompt: string;
  lyrics?: string;
  genre?: string;
  duration_seconds: number;
  seed?: number;
  voice_profile_id: string;
  project_id?: string;
  generation_options?: KPopGenerationOptionsDto;
}
export interface KPopGenerationOptionsDto {
  preset_id: "kpop_dance" | "kpop_easy_listening" | "kpop_performance";
  requested_bpm?: number;
  language_ratio?: { ko: number; en: number };
  hook?: {
    phrase: string;
    style: "title_repeat" | "chant";
    repeat_count: number;
  };
  include_post_chorus?: boolean;
  include_dance_break?: boolean;
  vocal_energy?: "low" | "medium" | "high";
  concept?: string;
}
export type AudioAnalysisStatusDto =
  | "NOT_REQUESTED"
  | "PENDING"
  | "COMPLETED"
  | "PARTIAL"
  | "FAILED"
  | "UNSUPPORTED";
export interface AudioQualityMetricsDto {
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  sample_peak_dbfs: number | null;
  clipping_detected: boolean;
  clipping_sample_count: number;
  clipping_ratio: number;
  integrated_lufs: number | null;
}
export interface TempoAnalysisDto {
  version: string;
  status: AudioAnalysisStatusDto;
  requested_bpm: number | null;
  detected_bpm: number | null;
  confidence: number | null;
  bpm_error: number | null;
  absolute_bpm_error: number | null;
  half_time_candidate: boolean;
  double_time_candidate: boolean;
}
export type HookSelectionStrategyDto =
  | "energy_repetition"
  | "energy_peak"
  | "fallback_middle"
  | "unavailable";
export interface HookCandidateDto {
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  confidence: number;
  selection_strategy: HookSelectionStrategyDto;
}
export interface HookAnalysisDto {
  hook_analysis_version: string;
  status: AudioAnalysisStatusDto;
  candidate: HookCandidateDto | null;
}
export interface AudioAnalysisDto {
  audio_analysis_version: string;
  analysis_status: AudioAnalysisStatusDto;
  quality: AudioQualityMetricsDto | null;
  tempo?: TempoAnalysisDto | null;
  hook?: HookAnalysisDto | null;
  warnings: string[];
}
export interface PipelineJobDto {
  id: string;
  project_id: string | null;
  voice_profile_id: string;
  status: PipelineStatus;
  current_step: string;
  progress_percent: number;
  prompt: string;
  lyrics: string | null;
  genre: string | null;
  duration_seconds: number;
  seed: number | null;
  pipeline_version: string;
  result_metadata: Record<string, unknown>;
  failed_step: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  cancel_requested_at: string | null;
  cancelled_at: string | null;
  retry_of_job_id: string | null;
  can_cancel: boolean;
  can_retry: boolean;
  generation_options?: KPopGenerationOptionsDto | null;
  kpop_prompt_compiler_version?: string | null;
  audio_analysis?: AudioAnalysisDto | null;
}
export interface PipelineCancelDto {
  job_id: string;
  status: PipelineStatus;
  cancel_requested_at: string | null;
  cancelled_at: string | null;
  message: string;
}
export interface PipelineRetryDto { source_job_id: string; job: PipelineJobDto; }
export interface HistoryItemDto {
  job_id: string;
  project_id: string | null;
  title: string;
  status: PipelineStatus;
  created_at: string;
  duration: number;
  voice_profile_name: string;
  has_audio: boolean;
  can_cancel: boolean;
  can_retry: boolean;
  retry_of_job_id: string | null;
  generation_options?: KPopGenerationOptionsDto | null;
  kpop_prompt_compiler_version?: string | null;
  audio_analysis?: AudioAnalysisDto | null;
}
export interface HistoryDetailDto extends HistoryItemDto {
  prompt: string;
  genre: string | null;
  completed_at: string | null;
}
export interface ProjectDto {
  id: string;
  title: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  job_count: number;
}
export interface ProjectDetailDto extends ProjectDto {
  jobs: HistoryItemDto[];
}
export interface PipelineFileDto {
  id: string;
  job_id: string;
  file_type: string;
  mime_type: string;
  created_at: string;
  content_available: boolean;
  download_available: boolean;
  content_url: string | null;
  download_url: string | null;
}

export interface WorkspaceProjectDto {
  project_id: string;
  workspace_id: string;
  title: string;
  lifecycle_status: string;
  created_at: string;
  updated_at: string;
}

export interface CompositionSnapshotSummaryDto {
  composition_snapshot_id: string;
  project_id: string;
  snapshot_version: number;
  created_at: string;
  processing_chain_id?: string | null;
  provider_versions?: Record<string, string>;
  model_manifest_ids?: Record<string, string>;
}

export interface CompositionAssetVersionDto {
  asset_version_id: string;
  asset_id: string;
  version_number: number;
  version_origin: string;
  parent_asset_version_id: string | null;
  processing_chain_id: string | null;
  provider_id: string | null;
  model_manifest_id: string | null;
  settings_snapshot: Record<string, unknown>;
  created_at: string;
}

export interface CompositionArtifactDto {
  artifact_id: string;
  asset_version_id: string;
  artifact_kind: string;
  media_type: string;
  size_bytes: number;
  checksum_algorithm: string;
  artifact_checksum: string;
  producer_type: string;
  producer_id: string | null;
  run_id: string | null;
  retention_status: string;
  created_at: string;
  content_url: string | null;
  download_url: string | null;
}

export interface CompositionReadItemDto {
  snapshot_item_id: string;
  item_role: "lyrics" | "music" | "vocal" | "stem" | "mix";
  sort_order: number;
  asset_version: CompositionAssetVersionDto;
  artifacts: CompositionArtifactDto[];
}

export interface CompositionTrackProjectionDto {
  projection_id: string;
  identity_scope: "snapshot";
  snapshot_item_id: string;
  item_role: "music" | "vocal" | "stem" | "mix";
  sort_order: number;
  asset_id: string;
  asset_version_id: string;
}

export interface CompositionWorkspaceDto {
  state: "ready" | "empty" | "selection_required";
  project: WorkspaceProjectDto;
  selection: {
    selected_snapshot_id: string | null;
    resolved_snapshot_id: string | null;
    resolution: "selected" | "requested" | "none";
    is_current: boolean;
  };
  snapshot: CompositionSnapshotSummaryDto | null;
  items: CompositionReadItemDto[];
  track_projections: CompositionTrackProjectionDto[];
  section_projection: {
    availability: "not_available";
    items: [];
  };
  mix_settings_snapshot: Record<string, unknown>;
  lineage: {
    processing_chain_id: string | null;
    provider_versions: Record<string, string>;
    model_manifest_ids: Record<string, string>;
  };
}

export interface CompositionSelectionDto {
  project_id: string;
  selected_snapshot_id: string | null;
}

export interface WorkingTrackDto {
  track_id: string;
  track_type: string;
  name: string;
  track_order: number;
  gain_db?: string;
  pan?: string;
  muted?: boolean;
  solo?: boolean;
}

export interface WorkingClipDto {
  clip_id: string;
  track_id: string;
  source_asset_version_id: string;
  timeline_start: string;
  source_in: string;
  source_out: string;
  source_duration: string;
  timeline_duration: string;
  loop_enabled: boolean;
  loop_phase: string;
  gain_db: string;
  fade_in: string;
  fade_out: string;
  split_from_clip_id: string | null;
}

export interface AssetVersionMediaSourceDto {
  asset_version_id: string;
  artifact_id: string;
  media_type: "audio/wav" | "audio/flac" | "audio/mpeg";
  size_bytes: number;
  artifact_checksum: string;
  duration_seconds: string;
  content_url: string;
}

export interface WorkingCompositionDto {
  working_composition_id: string;
  project_id: string;
  base_composition_snapshot_id: string | null;
  revision: number;
  master_gain_db?: string;
  mix_settings: Record<string, unknown>;
  tracks: WorkingTrackDto[];
  clips: WorkingClipDto[];
  timeline_duration: string;
}

export interface WorkingHistoryDetailDto {
  working_composition_id: string;
  revision: number;
  cursor: number;
  command_count: number;
  can_undo: boolean;
  can_redo: boolean;
}

export interface WorkingMutationResultDto {
  completed_revision: number;
  replayed: boolean;
}

export interface WorkingInitializeResultDto extends WorkingMutationResultDto {
  working_composition_id: string;
}

export interface WorkingCheckoutResultDto extends WorkingMutationResultDto {
    working_composition_id: string;
    base_composition_snapshot_id: string;
}

export interface WorkingCommitResultDto extends WorkingMutationResultDto {
  working_composition_id: string;
  composition_snapshot_id: string;
}

export interface WorkingTrackResultDto extends WorkingMutationResultDto {
  track_id: string;
}

export interface WorkingReorderResultDto {
  working_composition_id: string;
  completed_revision: number;
}

export interface WorkingClipResultDto extends WorkingMutationResultDto {
  clip_id: string;
}

export interface WorkingSplitResultDto extends WorkingMutationResultDto {
  original_clip_id: string;
  left_clip_id: string;
  right_clip_id: string;
}

export type WorkspaceJobStatusDto =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface WorkingPreviewCreateResultDto {
  job_id: string;
  preview_render_id: string;
  working_composition_id: string;
  rendered_revision: number;
  status: WorkspaceJobStatusDto;
  replayed: boolean;
}

export interface WorkspaceJobOutputDto {
  output_role: string | null;
  output_order: number;
  asset_version_id: string | null;
  artifact_id: string | null;
}

export interface WorkspaceJobInputDto {
  input_role: string | null;
  input_order: number;
  asset_version_id: string | null;
  artifact_id: string | null;
}

export interface WorkspaceJobModelUsageDto {
  provider_id: string;
  model_manifest_id: string;
  model_id: string;
  model_version: string;
  checkpoint_version: string | null;
  api_contract_version: string;
  license_status: string;
  commercial_usage_status: string;
  asset_version_id: string | null;
}

export interface WorkspaceJobDetailDto {
  job_id: string;
  project_id: string;
  composition_snapshot_id: string | null;
  job_type: string;
  status: WorkspaceJobStatusDto;
  provider_id: string | null;
  model_manifest_id: string | null;
  progress_percent: string | number | null;
  stage: string | null;
  retry_of_job_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  inputs: WorkspaceJobInputDto[];
  outputs: WorkspaceJobOutputDto[];
  model_usages: WorkspaceJobModelUsageDto[];
  error_code: string | null;
  error_message: string | null;
  error_retryable: boolean | null;
  error_details_id: string | null;
}

export interface WorkspaceExportJobCreateDto {
  project_id: string;
  job_type: "export";
  composition_snapshot_id: string;
  inputs: [];
  settings_snapshot: { format: "wav" };
}
