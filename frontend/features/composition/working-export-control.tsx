"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, FileAudio, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { ApiError } from "@/services/api-client";
import { dohaApi, getArtifactContentUrl } from "@/services/doha-api";
import type { WorkspaceExportFormat, WorkspaceJobDetailDto, WorkspaceJobStatusDto } from "@/types/api";
import { newIdempotencyKey } from "./working-composition-history";

const EXPORT_POLL_INTERVAL_MS = 1_500;
const TERMINAL = new Set<WorkspaceJobStatusDto>(["succeeded", "failed", "cancelled"]);

export function WorkingExportControl({ projectId, snapshotId, disabled = false, disabledReason }: {
  projectId: string; snapshotId: string | null; disabled?: boolean; disabledReason?: string;
}) {
  const generation = useRef(0);
  const creating = useRef(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [creatingForProject, setCreatingForProject] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [selectedFormat, setSelectedFormat] = useState<WorkspaceExportFormat>("wav");
  const [activeExportFormat, setActiveExportFormat] = useState<WorkspaceExportFormat | null>(null);

  useEffect(() => () => { generation.current += 1; creating.current = false; }, [projectId]);
  const job = useQuery({
    queryKey: ["workspace-export-job", projectId, jobId],
    queryFn: ({ signal }) => dohaApi.getWorkspaceJob(jobId!, signal),
    enabled: Boolean(jobId), retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && TERMINAL.has(status) ? false : EXPORT_POLL_INTERVAL_MS;
    },
    refetchIntervalInBackground: true,
  });
  const status = job.data?.status ?? (isCreating && creatingForProject === projectId ? "creating" : "idle");
  const inProgress = status === "creating" || status === "queued" || status === "running";
  const artifactId = status === "succeeded" ? selectExportArtifact(job.data) : null;
  const canCreate = Boolean(snapshotId) && !disabled && !inProgress;
  const displayFormat = activeExportFormat ?? selectedFormat;
  const formatLabel = displayFormat.toUpperCase();

  async function createExport() {
    if (!canCreate || creating.current || !snapshotId) return;
    creating.current = true;
    setCreatingForProject(projectId);
    const currentGeneration = ++generation.current;
    const key = newIdempotencyKey();
    const submittedFormat = selectedFormat;
    setActiveExportFormat(submittedFormat);
    setIsCreating(true); setRequestError(null);
    try {
      const response = await retryExportPost(() => dohaApi.createWorkspaceExportJob({
        project_id: projectId, job_type: "export", composition_snapshot_id: snapshotId,
        inputs: [], settings_snapshot: { format: submittedFormat },
      }, key));
      if (generation.current === currentGeneration) setJobId(response.job_id);
    } catch (error) {
      if (generation.current === currentGeneration) setRequestError(exportErrorMessage(error));
    } finally {
      if (generation.current === currentGeneration) { creating.current = false; setIsCreating(false); }
    }
  }

  async function cancelExport() {
    if (!jobId || isCancelling || !["queued", "running"].includes(status)) return;
    const currentGeneration = generation.current;
    setIsCancelling(true); setRequestError(null);
    try {
      await dohaApi.cancelWorkspaceJob(jobId);
      if (generation.current === currentGeneration) await job.refetch();
    } catch (error) {
      if (generation.current === currentGeneration) setRequestError(exportErrorMessage(error));
    } finally {
      if (generation.current === currentGeneration) setIsCancelling(false);
    }
  }

  return <section className={`working-export ${status}`} aria-labelledby="working-export-title">
    <div className="working-export-copy"><FileAudio aria-hidden="true" /><div><p className="eyebrow">AUDIO EXPORT</p><h5 id="working-export-title">현재 Snapshot 내보내기</h5></div></div>
    <div className="working-export-status" role="status" aria-live="polite"><strong>{statusLabel(status, formatLabel)}</strong><span>{statusDescription(status, formatLabel)}</span></div>
    <div className="working-export-actions">
      <label className="working-export-format"><span>Format</span><select aria-label="Export format" value={selectedFormat} disabled={inProgress} onChange={(event) => setSelectedFormat(event.target.value as WorkspaceExportFormat)}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option></select></label>
      <Button type="button" aria-label={`Export ${selectedFormat.toUpperCase()}`} disabled={!canCreate} onClick={() => void createExport()}><FileAudio aria-hidden="true" />Export {selectedFormat.toUpperCase()}</Button>
      {(status === "queued" || status === "running") && <Button type="button" className="secondary" aria-label={`Cancel ${formatLabel} export`} disabled={isCancelling} onClick={() => void cancelExport()}><Square aria-hidden="true" />{isCancelling ? "취소 요청 중" : "내보내기 취소"}</Button>}
      {artifactId && <a className="button secondary" aria-label={`Download exported ${formatLabel}`} href={getArtifactContentUrl(artifactId)} download><Download aria-hidden="true" /> {formatLabel} 다운로드</a>}
    </div>
    {!snapshotId && <p className="working-export-help">내보낼 canonical Snapshot이 없습니다.</p>}
    {disabled && disabledReason && <p className="working-export-help">{disabledReason}</p>}
    {requestError && <p className="working-export-error" role="alert">{requestError}</p>}
    {job.error && inProgress && <p className="working-export-error" role="alert">내보내기 상태를 확인하지 못했습니다. 잠시 후 다시 확인합니다.</p>}
  </section>;
}

function selectExportArtifact(job?: WorkspaceJobDetailDto): string | null {
  if (job?.status !== "succeeded") return null;
  return job.outputs.find((item) => item.output_role === "export" && item.output_order === 0)?.artifact_id ?? null;
}

async function retryExportPost<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); } catch (error) {
    if (error instanceof ApiError && (error.code === "NETWORK_ERROR" || error.code === "REQUEST_TIMEOUT")) return operation();
    throw error;
  }
}

function exportErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "COMPOSITION_SNAPSHOT_NOT_FOUND") return "내보낼 Snapshot을 찾을 수 없습니다.";
  return "오디오 내보내기를 시작하거나 확인하지 못했습니다.";
}

function statusLabel(status: WorkspaceJobStatusDto | "creating" | "idle", format: string): string {
  return { idle: "준비됨", creating: "Export 준비 중", queued: "대기 중", running: `${format} 렌더링 중`, succeeded: "완료", failed: "실패", cancelled: "취소됨" }[status];
}

function statusDescription(status: WorkspaceJobStatusDto | "creating" | "idle", format: string): string {
  return { idle: "WAV · MP3 · FLAC", creating: `canonical Snapshot으로 ${format} Export Job을 만들고 있습니다.`, queued: `${format} Export Job이 production runner를 기다리고 있습니다.`, running: `frozen Snapshot을 ${format}로 렌더링하고 있습니다.`, succeeded: `완성된 ${format}를 Artifact 경로로 다운로드할 수 있습니다.`, failed: `${format} 내보내기를 완료하지 못했습니다. 새 요청으로 다시 시도할 수 있습니다.`, cancelled: `${format} 내보내기가 취소되었습니다.` }[status];
}
