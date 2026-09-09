import { apiRequest } from "./api-client";
import type {
  AssetVersionMediaSourceDto,
  LyricsCreateDto,
  LyricsDocumentDto,
  LyricsValidationDto,
  PipelineCreateDto,
  PipelineFileDto,
  PipelineJobDto,
  PipelineCancelDto,
  PipelineRetryDto,
  VoiceProfileDto,
  HistoryDetailDto,
  HistoryItemDto,
  ProjectDetailDto,
  ProjectDto,
  CompositionSelectionDto,
  CompositionSnapshotSummaryDto,
  CompositionWorkspaceDto,
  WorkingCheckoutResultDto,
  WorkingCommitResultDto,
  WorkingClipResultDto,
  WorkingCompositionDto,
  WorkingInitializeResultDto,
  WorkingReorderResultDto,
  WorkingSplitResultDto,
  WorkingTrackResultDto,
  WorkingPreviewCreateResultDto,
  WorkingHistoryDetailDto,
  WorkspaceJobDetailDto,
  WorkspaceExportJobCreateDto,
} from "@/types/api";
import type {
  VoiceEnrollmentCreateRequest,
  VoiceEnrollmentSubmitRequest,
  VoiceSampleSourceType,
} from "@/features/voice/voice-enrollment-types";
import {
  mapVoiceEnrollmentDto,
  mapVoiceSampleDto,
} from "@/features/voice/voice-enrollment-utils";

export const dohaApi = {
  createWorkspaceExportJob: (data: WorkspaceExportJobCreateDto, key: string, signal?: AbortSignal) =>
    workspaceData<WorkspaceJobDetailDto>("/api/v1/jobs", { ...mutationInit("POST", data, key), signal }),
  cancelWorkspaceJob: (jobId: string, signal?: AbortSignal) =>
    workspaceData<WorkspaceJobDetailDto>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", signal }),
  health: () => apiRequest<{ status: string }>("/health"),
  createLyrics: (data: LyricsCreateDto) =>
    apiRequest<LyricsDocumentDto>("/api/lyrics", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getLyrics: (id: string) => apiRequest<LyricsDocumentDto>(`/api/lyrics/${id}`),
  reviseLyrics: (id: string, instruction: string) =>
    apiRequest<LyricsDocumentDto>(`/api/lyrics/${id}/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction, preserve_structure: true }),
    }),
  validateLyrics: (raw_lyrics: string) =>
    apiRequest<LyricsValidationDto>("/api/lyrics/validate", {
      method: "POST",
      body: JSON.stringify({ raw_lyrics, language: "ko" }),
    }),
  deleteLyrics: (id: string) =>
    apiRequest<void>(`/api/lyrics/${id}`, { method: "DELETE" }),
  createVoiceProfile: (data: {
    name: string;
    reference_file_path: string;
    consent_confirmed: true;
  }) =>
    apiRequest<VoiceProfileDto>("/api/voice-profiles", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  uploadVoiceProfile: (data: {
    file: File;
    name: string;
    consentTextVersion: string;
  }) => {
    const body = new FormData();
    body.set("file", data.file);
    body.set("name", data.name);
    body.set("consent_confirmed", "true");
    body.set("consent_text_version", data.consentTextVersion);
    return apiRequest<VoiceProfileDto>(
      "/api/voice-profiles/upload",
      { method: "POST", body },
      60_000,
    );
  },
  listVoiceProfiles: () =>
    apiRequest<VoiceProfileDto[]>("/api/voice-profiles"),
  getVoiceProfile: (id: string) =>
    apiRequest<VoiceProfileDto>(
      `/api/voice-profiles/${encodeURIComponent(id)}`,
    ),
  deleteVoiceProfile: (id: string) =>
    apiRequest<void>(`/api/voice-profiles/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  createVoiceEnrollment: (data: VoiceEnrollmentCreateRequest, idempotencyKey: string) =>
    apiRequest<unknown>("/api/voice-enrollments", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(data),
    }).then(mapVoiceEnrollmentDto),
  getVoiceEnrollment: (id: string, signal?: AbortSignal) =>
    apiRequest<unknown>(`/api/voice-enrollments/${encodeURIComponent(id)}`, { signal })
      .then(mapVoiceEnrollmentDto),
  uploadVoiceEnrollmentSample: (data: {
    enrollmentId: string;
    file: File | Blob;
    sourceType: VoiceSampleSourceType;
    category: string;
    promptId?: string;
    idempotencyKey: string;
    signal?: AbortSignal;
  }) => {
    const body = new FormData();
    body.set("file", data.file, data.file instanceof File ? data.file.name : `recording.${data.file.type.includes("ogg") ? "ogg" : data.file.type.includes("wav") ? "wav" : "webm"}`);
    body.set("source_type", data.sourceType);
    body.set("category", data.category);
    if (data.promptId) body.set("prompt_id", data.promptId);
    return apiRequest<unknown>(
      `/api/voice-enrollments/${encodeURIComponent(data.enrollmentId)}/samples`,
      {
        method: "POST",
        headers: { "Idempotency-Key": data.idempotencyKey },
        body,
        signal: data.signal,
      },
      90_000,
    ).then(mapVoiceSampleDto);
  },
  getVoiceEnrollmentSample: (enrollmentId: string, sampleId: string, signal?: AbortSignal) =>
    apiRequest<unknown>(
      `/api/voice-enrollments/${encodeURIComponent(enrollmentId)}/samples/${encodeURIComponent(sampleId)}`,
      { signal },
    ).then(mapVoiceSampleDto),
  deleteVoiceEnrollmentSample: (enrollmentId: string, sampleId: string) =>
    apiRequest<unknown>(
      `/api/voice-enrollments/${encodeURIComponent(enrollmentId)}/samples/${encodeURIComponent(sampleId)}`,
      { method: "DELETE" },
    ).then(mapVoiceSampleDto),
  submitVoiceEnrollment: (
    enrollmentId: string,
    data: VoiceEnrollmentSubmitRequest,
    idempotencyKey: string,
  ) => apiRequest<unknown>(
    `/api/voice-enrollments/${encodeURIComponent(enrollmentId)}/submit`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(data),
    },
    90_000,
  ).then(mapVoiceEnrollmentDto),
  cancelVoiceEnrollment: (enrollmentId: string) =>
    apiRequest<unknown>(
      `/api/voice-enrollments/${encodeURIComponent(enrollmentId)}/cancel`,
      { method: "POST" },
      30_000,
    ).then(mapVoiceEnrollmentDto),
  createPipeline: (data: PipelineCreateDto) =>
    apiRequest<PipelineJobDto>("/api/pipelines", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getPipeline: (id: string, signal?: AbortSignal) =>
    apiRequest<PipelineJobDto>(`/api/pipelines/${id}`, { signal }),
  cancelPipelineJob: (id: string, signal?: AbortSignal) =>
    apiRequest<PipelineCancelDto>(`/api/pipelines/${encodeURIComponent(id)}/cancel`, { method: "POST", signal }),
  retryPipelineJob: (id: string, signal?: AbortSignal) =>
    apiRequest<PipelineRetryDto>(`/api/pipelines/${encodeURIComponent(id)}/retry`, { method: "POST", signal }),
  getPipelineFiles: (id: string) =>
    apiRequest<PipelineFileDto[]>(`/api/pipelines/${id}/files`),
  getHistory: (options: { limit?: number; offset?: number; status?: string; q?: string } = {}) => {
    const params = new URLSearchParams();
    if (options.limit) params.set("limit", String(options.limit));
    if (options.offset) params.set("offset", String(options.offset));
    if (options.status) params.set("status", options.status);
    if (options.q) params.set("q", options.q);
    const suffix = params.size ? `?${params}` : "";
    return apiRequest<HistoryItemDto[]>(`/api/history${suffix}`);
  },
  getHistoryDetail: (id: string) =>
    apiRequest<HistoryDetailDto>(`/api/history/${encodeURIComponent(id)}`),
  getProjects: () => apiRequest<ProjectDto[]>("/api/projects"),
  getProject: (id: string) =>
    workspaceData<{
      project_id: string;
      title: string;
      description: string | null;
      created_at: string;
      updated_at: string;
    }>(`/api/v1/projects/${encodeURIComponent(id)}`).then((project) => ({
      id: project.project_id,
      title: project.title,
      description: project.description,
      created_at: project.created_at,
      updated_at: project.updated_at,
      job_count: 0,
      jobs: [] as ProjectDetailDto["jobs"],
    } satisfies ProjectDetailDto)),
  createProject: (data: { title: string; description?: string }) =>
    apiRequest<ProjectDto>("/api/projects", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateProject: (id: string, data: { title?: string; description?: string | null }) =>
    apiRequest<ProjectDto>(`/api/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteProject: (id: string) =>
    apiRequest<void>(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getProjectComposition: (projectId: string, signal?: AbortSignal) =>
    apiRequest<{ data: CompositionWorkspaceDto }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/composition`,
      { signal },
    ).then((response) => response.data),
  listProjectCompositionSnapshots: (projectId: string, signal?: AbortSignal) =>
    apiRequest<{ data: CompositionSnapshotSummaryDto[] }>(
      `/api/v1/snapshots?project_id=${encodeURIComponent(projectId)}&limit=100`,
      { signal },
    ).then((response) => response.data),
  selectProjectComposition: (projectId: string, snapshotId: string) =>
    apiRequest<{ data: CompositionSelectionDto }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/composition-selection`,
      {
        method: "PATCH",
        body: JSON.stringify({ selected_snapshot_id: snapshotId }),
      },
    ).then((response) => response.data),
  getWorkingComposition: (projectId: string, signal?: AbortSignal) =>
    workspaceData<WorkingCompositionDto>(workingPath(projectId), { signal }),
  getWorkingCompositionHistory: (projectId: string, workingCompositionId: string, signal?: AbortSignal) =>
    workspaceData<WorkingHistoryDetailDto>(
      `${workingPath(projectId)}/history?working_composition_id=${encodeURIComponent(workingCompositionId)}`,
      { signal },
    ),
  undoWorkingCompositionHistory: (projectId: string, body: WorkingBase, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/history/undo`, mutationInit("POST", body, idempotencyKey)),
  redoWorkingCompositionHistory: (projectId: string, body: WorkingBase, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/history/redo`, mutationInit("POST", body, idempotencyKey)),
  createWorkingPreview: (
    projectId: string,
    expectedRevision: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ) => workspaceData<WorkingPreviewCreateResultDto>(`${workingPath(projectId)}/preview`, {
    method: "POST",
    headers: idempotencyHeader(idempotencyKey),
    body: JSON.stringify({ expected_revision: expectedRevision }),
    signal,
  }),
  getWorkspaceJob: (jobId: string, signal?: AbortSignal) =>
    workspaceData<WorkspaceJobDetailDto>(
      `/api/v1/jobs/${encodeURIComponent(jobId)}`,
      { signal },
    ),
  resolveAssetVersionMediaSource: (
    projectId: string,
    assetVersionId: string,
    signal?: AbortSignal,
  ) => workspaceData<AssetVersionMediaSourceDto>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/asset-versions/${encodeURIComponent(assetVersionId)}/media-source`,
    { signal },
  ),
  initializeWorkingComposition: (projectId: string, idempotencyKey: string) =>
    workspaceData<WorkingInitializeResultDto>(`${workingPath(projectId)}/initialize`, {
      method: "POST",
      headers: idempotencyHeader(idempotencyKey),
      body: "{}",
    }),
  checkoutWorkingComposition: (projectId: string, body: WorkingBase & { composition_snapshot_id: string }, idempotencyKey: string) =>
    workspaceData<WorkingCheckoutResultDto>(`${workingPath(projectId)}/checkout`, mutationInit("POST", body, idempotencyKey)),
  commitWorkingComposition: (projectId: string, expectedRevision: number, idempotencyKey: string) =>
    workspaceData<WorkingCommitResultDto>(`${workingPath(projectId)}/commit`, mutationInit(
      "POST",
      { expected_revision: expectedRevision },
      idempotencyKey,
    )),
  createWorkingTrack: (projectId: string, body: WorkingBase & { name: string }, idempotencyKey: string) =>
    workspaceData<WorkingTrackResultDto>(`${workingPath(projectId)}/tracks`, mutationInit("POST", body, idempotencyKey)),
  renameWorkingTrack: (projectId: string, trackId: string, body: WorkingBase & { name: string }) =>
    workspaceData<WorkingTrackResultDto>(`${workingPath(projectId)}/tracks/${encodeURIComponent(trackId)}`, mutationInit("PATCH", body)),
  reorderWorkingTracks: (projectId: string, body: WorkingBase & { ordered_track_ids: string[] }) =>
    workspaceData<WorkingReorderResultDto>(`${workingPath(projectId)}/tracks/reorder`, mutationInit("PATCH", body)),
  updateWorkingTrackMixer: (projectId: string, trackId: string, body: WorkingBase & { gain_db: number; pan: number; muted: boolean; solo: boolean }, idempotencyKey: string) =>
    workspaceData<WorkingTrackResultDto>(`${workingPath(projectId)}/tracks/${encodeURIComponent(trackId)}/mixer`, mutationInit("PATCH", body, idempotencyKey)),
  updateWorkingMasterGain: (projectId: string, body: WorkingBase & { master_gain_db: number }, idempotencyKey: string) =>
    workspaceData<WorkingInitializeResultDto>(`${workingPath(projectId)}/master-gain`, mutationInit("PATCH", body, idempotencyKey)),
  deleteWorkingTrack: (projectId: string, trackId: string, body: WorkingBase, idempotencyKey: string) =>
    workspaceData<WorkingTrackResultDto>(`${workingPath(projectId)}/tracks/${encodeURIComponent(trackId)}`, mutationInit("DELETE", body, idempotencyKey)),
  restoreWorkingTrack: (projectId: string, trackId: string, body: WorkingBase & { target_track_order: number }, idempotencyKey: string) =>
    workspaceData<WorkingTrackResultDto>(`${workingPath(projectId)}/tracks/${encodeURIComponent(trackId)}/restore`, mutationInit("POST", body, idempotencyKey)),
  createWorkingClip: (projectId: string, body: WorkingBase & { track_id: string; source_asset_version_id: string; timeline_start: string; source_in: string; source_out: string }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips`, mutationInit("POST", body, idempotencyKey)),
  copyWorkingClip: (projectId: string, clipId: string, body: WorkingBase & { target_track_id: string; target_timeline_start: string }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/copy`, mutationInit("POST", body, idempotencyKey)),
  moveWorkingClip: (projectId: string, clipId: string, body: WorkingBase & { timeline_start: string }) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/move`, mutationInit("PATCH", body)),
  updateWorkingClipGain: (projectId: string, clipId: string, body: WorkingBase & { gain_db: number }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/gain`, mutationInit("PATCH", body, idempotencyKey)),
  updateWorkingClipFade: (projectId: string, clipId: string, body: WorkingBase & { fade_in: number; fade_out: number }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/fade`, mutationInit("PATCH", body, idempotencyKey)),
  updateWorkingClipLoop: (projectId: string, clipId: string, body: WorkingBase & { loop_enabled: boolean; timeline_duration: number }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/loop`, mutationInit("PATCH", body, idempotencyKey)),
  restoreWorkingClipLoop: (projectId: string, clipId: string, body: WorkingBase & { loop_enabled: boolean; timeline_duration: number; loop_phase: number }, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/loop/restore`, mutationInit("POST", body, idempotencyKey)),
  trimWorkingClipStart: (projectId: string, clipId: string, body: WorkingBase & { timeline_start: string; source_in: string }) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/trim-start`, mutationInit("PATCH", body)),
  trimWorkingClipEnd: (projectId: string, clipId: string, body: WorkingBase & { source_out: string }) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/trim-end`, mutationInit("PATCH", body)),
  splitWorkingClip: (projectId: string, clipId: string, body: WorkingBase & { split_at: string }, idempotencyKey: string) =>
    workspaceData<WorkingSplitResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/split`, mutationInit("POST", body, idempotencyKey)),
  deleteWorkingClip: (projectId: string, clipId: string, body: WorkingBase, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}`, mutationInit("DELETE", body, idempotencyKey)),
  restoreWorkingClip: (projectId: string, clipId: string, body: WorkingBase, idempotencyKey: string) =>
    workspaceData<WorkingClipResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(clipId)}/restore`, mutationInit("POST", body, idempotencyKey)),
  unsplitWorkingClip: (projectId: string, originalClipId: string, body: WorkingBase & SplitChildren, idempotencyKey: string) =>
    workspaceData<WorkingSplitResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(originalClipId)}/unsplit`, mutationInit("POST", body, idempotencyKey)),
  resplitWorkingClip: (projectId: string, originalClipId: string, body: WorkingBase & SplitChildren, idempotencyKey: string) =>
    workspaceData<WorkingSplitResultDto>(`${workingPath(projectId)}/clips/${encodeURIComponent(originalClipId)}/resplit`, mutationInit("POST", body, idempotencyKey)),
};

interface WorkingBase {
  working_composition_id: string;
  expected_revision: number;
}

interface SplitChildren {
  left_clip_id: string;
  right_clip_id: string;
}

function workingPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/working-composition`;
}

function idempotencyHeader(key: string): Record<string, string> {
  return { "Idempotency-Key": key };
}

function mutationInit(method: string, body: object, idempotencyKey?: string): RequestInit {
  return {
    method,
    headers: idempotencyKey ? idempotencyHeader(idempotencyKey) : undefined,
    body: JSON.stringify(body),
  };
}

function workspaceData<T>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<{ data: T }>(path, init).then((response) => response.data);
}

export function getPipelineFileContentUrl(jobId: string, fileId: string) {
  return `/backend/api/pipelines/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fileId)}/content`;
}

export function getPipelineFileDownloadUrl(jobId: string, fileId: string) {
  return `/backend/api/pipelines/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fileId)}/download`;
}

export function toBackendPublicUrl(url: string | null): string | undefined {
  return url?.startsWith("/api/") ? `/backend${url}` : undefined;
}

export function getArtifactContentUrl(artifactId: string): string {
  return `/backend/api/v1/artifacts/${encodeURIComponent(artifactId)}/content`;
}
