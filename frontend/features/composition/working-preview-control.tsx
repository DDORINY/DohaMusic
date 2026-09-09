"use client";

import { useQuery } from "@tanstack/react-query";
import { Play, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui";
import { ApiError } from "@/services/api-client";
import { dohaApi, getArtifactContentUrl } from "@/services/doha-api";
import { usePlayerStore } from "@/stores/player-store";
import type { WorkspaceJobDetailDto } from "@/types/api";
import type { SafePipelineFile } from "@/types/domain";
import { newIdempotencyKey } from "./working-composition-history";
import {
  getWorkingPreviewPollingInterval,
  isWorkingPreviewStale,
  selectWorkingPreviewOutput,
  workingPreviewErrorMessage,
  type WorkingPreviewPhase,
  type WorkingPreviewRequestState,
} from "./working-preview";

interface SuccessfulPreview {
  artifactId: string;
  jobId: string;
  renderedRevision: number;
  source: SafePipelineFile;
}

export function WorkingPreviewControl({
  projectId,
  workingCompositionId,
  currentRevision,
  clipCount,
  disabled = false,
  onRevisionConflict,
}: {
  projectId: string;
  workingCompositionId: string;
  currentRevision: number;
  clipCount: number;
  disabled?: boolean;
  onRevisionConflict: () => Promise<void>;
}) {
  const requestGeneration = useRef(0);
  const [request, setRequest] = useState<WorkingPreviewRequestState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [lastSuccessful, setLastSuccessful] = useState<SuccessfulPreview | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const currentFile = usePlayerStore((state) => state.currentFile);
  const playerError = usePlayerStore((state) => state.error);
  const play = usePlayerStore((state) => state.play);

  const job = useQuery({
    queryKey: ["workspace-job", projectId, request?.jobId],
    queryFn: ({ signal }) => dohaApi.getWorkspaceJob(request!.jobId, signal),
    enabled: Boolean(request?.jobId),
    retry: false,
    refetchInterval: (query) => getWorkingPreviewPollingInterval({
      job: query.state.data,
      successCount: query.state.dataUpdateCount,
      consecutiveErrors: query.state.fetchFailureCount,
      hidden: typeof document !== "undefined" && document.hidden,
    }),
    refetchIntervalInBackground: true,
  });

  let currentSuccessful: SuccessfulPreview | null = null;
  let outputInvalid = false;
  if (job.data?.status === "succeeded" && request && job.data.job_id === request.jobId) {
    try {
      currentSuccessful = successfulPreview(job.data, request.renderedRevision);
    } catch {
      outputInvalid = true;
    }
  }
  const visibleSuccessful = currentSuccessful ?? lastSuccessful;
  const playbackUnavailable = Boolean(
    visibleSuccessful
    && currentFile?.id === visibleSuccessful.artifactId
    && playerError,
  );
  const activeJobStatus = job.data?.status ?? request?.phase;
  const requestInProgress = activeJobStatus === "queued" || activeJobStatus === "running";

  async function createPreview() {
    if (submitting || requestInProgress) return;
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const idempotencyKey = newIdempotencyKey();
    if (currentSuccessful) setLastSuccessful(currentSuccessful);
    setSubmitting(true);
    setRequestError(null);
    try {
      const response = await retryPreviewPost(() => dohaApi.createWorkingPreview(
        projectId,
        currentRevision,
        idempotencyKey,
      ));
      if (requestGeneration.current !== generation) return;
      if (response.working_composition_id !== workingCompositionId) {
        throw new Error("WORKING_PREVIEW_COMPOSITION_MISMATCH");
      }
      setRequest({
        jobId: response.job_id,
        previewRenderId: response.preview_render_id,
        renderedRevision: response.rendered_revision,
        phase: response.status,
      });
    } catch (error) {
      if (requestGeneration.current !== generation) return;
      if (error instanceof ApiError && error.code === "WORKING_COMPOSITION_REVISION_CONFLICT") {
        await onRevisionConflict();
      }
      setRequestError(workingPreviewErrorMessage(error instanceof ApiError ? error.code : null));
    } finally {
      if (requestGeneration.current === generation) setSubmitting(false);
    }
  }

  const staleRevision = visibleSuccessful?.renderedRevision ?? request?.renderedRevision;
  const stale = staleRevision !== undefined && isWorkingPreviewStale(staleRevision, currentRevision);
  const phase: WorkingPreviewPhase = submitting
    ? "submitting"
    : requestInProgress
      ? activeJobStatus!
      : playbackUnavailable
        ? "unavailable"
        : outputInvalid
          ? "failed"
          : job.data?.status === "succeeded"
            ? "ready"
            : (job.data?.status ?? request?.phase ?? "idle");
  const requestPending = submitting || requestInProgress;
  const canCreate = clipCount > 0 && !requestPending && !disabled;

  return (
    <section className="working-preview" aria-labelledby="working-preview-title">
      <div className="working-preview-heading">
        <div><p className="eyebrow">WORKING PREVIEW</p><h5 id="working-preview-title">현재 편집본 미리듣기</h5></div>
        <Button type="button" disabled={!canCreate} aria-label={stale || visibleSuccessful ? "Preview 다시 만들기" : "Working Preview 만들기"} onClick={() => void createPreview()}>
          <RefreshCw aria-hidden="true" /> {stale || visibleSuccessful ? "Preview 다시 만들기" : "Preview 만들기"}
        </Button>
      </div>
      <div className={`working-preview-status ${phase}`} aria-live="polite" role="status">
        <strong>{previewStatusLabel(phase)}</strong>
        <span>{previewStatusDescription(phase, request?.renderedRevision)}</span>
      </div>
      {clipCount === 0 && <p className="working-preview-help">활성 Clip을 배치하면 Preview를 만들 수 있습니다.</p>}
      {disabled && <p className="working-preview-help" role="status">Mixer 변경을 저장한 뒤 Preview를 만들 수 있습니다.</p>}
      {stale && <p className="working-preview-stale" role="status"><strong>Preview가 최신 편집본과 다릅니다.</strong><span>Preview 이후 변경 사항이 있습니다. 다시 만들어 확인해 주세요.</span></p>}
      {(requestError || outputInvalid || job.data?.status === "failed") && <p className="working-preview-error" role="alert">{requestError ?? workingPreviewErrorMessage(outputInvalid ? "WORKING_PREVIEW_OUTPUT_INVALID" : job.data?.error_code)}</p>}
      {job.error && requestPending && <p className="working-preview-error" role="alert">Preview 상태 확인이 지연되고 있습니다. 상태 새로고침을 사용해 주세요.</p>}
      <div className="working-preview-actions">
        {visibleSuccessful && <Button type="button" disabled={playbackUnavailable} aria-label={`Working Preview revision ${visibleSuccessful.renderedRevision} 재생`} onClick={() => play(visibleSuccessful.source)}><Play aria-hidden="true" /> Preview 재생</Button>}
        {requestPending && request?.jobId && <Button type="button" className="secondary" disabled={job.isFetching} onClick={() => void job.refetch()}>상태 새로고침</Button>}
      </div>
      {playbackUnavailable && <p className="working-preview-error" role="alert">Preview 파일이 만료되었거나 사용할 수 없습니다. Preview를 다시 만들어 주세요.</p>}
    </section>
  );
}

async function retryPreviewPost<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); } catch (error) {
    if (error instanceof ApiError && (error.code === "NETWORK_ERROR" || error.code === "REQUEST_TIMEOUT")) return operation();
    throw error;
  }
}

function successfulPreview(
  job: WorkspaceJobDetailDto,
  renderedRevision: number,
): SuccessfulPreview {
  const output = selectWorkingPreviewOutput(job);
  const artifactId = output.artifact_id!;
  return {
    artifactId,
    jobId: job.job_id,
    renderedRevision,
    source: {
      id: artifactId,
      jobId: job.job_id,
      fileType: `Working Preview · revision ${renderedRevision}`,
      mimeType: "audio/wav",
      createdAt: job.completed_at ?? job.created_at,
      contentAvailable: true,
      downloadAvailable: false,
      contentUrl: getArtifactContentUrl(artifactId),
    },
  };
}

function previewStatusLabel(phase: WorkingPreviewPhase): string {
  const labels: Record<WorkingPreviewPhase, string> = {
    idle: "준비됨", submitting: "Preview 요청 중", queued: "대기 중", running: "렌더링 중",
    succeeded: "완료", ready: "재생 준비됨", failed: "실패", cancelled: "취소됨", unavailable: "사용할 수 없음",
  };
  return labels[phase];
}

function previewStatusDescription(phase: WorkingPreviewPhase, renderedRevision?: number): string {
  if (phase === "idle") return "Preview는 편집을 자동으로 실행하지 않습니다.";
  if (phase === "submitting") return "현재 revision으로 렌더링을 요청하고 있습니다.";
  if (phase === "queued") return `revision ${renderedRevision} 작업이 대기 중입니다.`;
  if (phase === "running") return `revision ${renderedRevision} 오디오를 렌더링하고 있습니다.`;
  if (phase === "ready" || phase === "succeeded") return `revision ${renderedRevision} 결과를 재생할 수 있습니다.`;
  if (phase === "failed") return "작업을 완료하지 못했습니다.";
  if (phase === "cancelled") return "현재 Preview 요청이 취소되었습니다.";
  return "Preview 파일을 다시 만들어야 합니다.";
}
