import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkingExportControl } from "@/features/composition/working-export-control";
import { ApiError } from "@/services/api-client";
import { dohaApi } from "@/services/doha-api";
import type { WorkspaceJobDetailDto, WorkspaceJobStatusDto } from "@/types/api";

describe("WorkingExportControl", () => {
  beforeEach(() => {
    vi.spyOn(dohaApi, "createWorkspaceExportJob").mockResolvedValue(exportJob("queued"));
    vi.spyOn(dohaApi, "getWorkspaceJob").mockResolvedValue(exportJob("succeeded"));
    vi.spyOn(dohaApi, "cancelWorkspaceJob").mockResolvedValue(exportJob("cancelled"));
  });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("canonical Snapshot payload로 accidental double-click당 Job 하나만 생성한다", async () => {
    let resolve!: (job: WorkspaceJobDetailDto) => void;
    vi.mocked(dohaApi.createWorkspaceExportJob).mockReturnValue(new Promise((done) => { resolve = done; }));
    renderControl();
    const action = screen.getByRole("button", { name: "Export WAV" });
    fireEvent.click(action); fireEvent.click(action);
    expect(dohaApi.createWorkspaceExportJob).toHaveBeenCalledTimes(1);
    expect(dohaApi.createWorkspaceExportJob).toHaveBeenCalledWith({
      project_id: "project-1", job_type: "export", composition_snapshot_id: "snapshot-1",
      inputs: [], settings_snapshot: { format: "wav" },
    }, expect.any(String));
    await act(async () => resolve(exportJob("queued")));
  });

  it("Snapshot 부재와 pending mutation을 이유와 함께 차단한다", () => {
    const first = renderControl(null);
    expect(screen.getByRole("button", { name: "Export WAV" })).toBeDisabled();
    expect(screen.getByText(/canonical Snapshot이 없습니다/)).toBeVisible();
    first.unmount();
    renderControl("snapshot-1", true);
    expect(screen.getByRole("button", { name: "Export WAV" })).toBeDisabled();
    expect(screen.getByText(/Mixer 변경을 저장하는 동안/)).toBeVisible();
  });

  it.each([["queued", "대기 중"], ["running", "WAV 렌더링 중"], ["failed", "실패"], ["cancelled", "취소됨"]] as const)(
    "%s backend 상태를 canonical UI로 표시한다", async (status, label) => {
      vi.mocked(dohaApi.getWorkspaceJob).mockResolvedValue(exportJob(status));
      renderControl();
      await userEvent.click(screen.getByRole("button", { name: "Export WAV" }));
      expect(await screen.findByText(label, { exact: true })).toBeVisible();
    },
  );

  it("SUCCEEDED export output만 Artifact download로 노출하고 quality를 재해석하지 않는다", async () => {
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: "Export WAV" }));
    expect(await screen.findByRole("link", { name: "Download exported WAV" }))
      .toHaveAttribute("href", "/backend/api/v1/artifacts/artifact-export/content");
  });

  it("cancel 결과를 낙관하지 않고 Job authority를 다시 조회한다", async () => {
    vi.mocked(dohaApi.getWorkspaceJob).mockResolvedValueOnce(exportJob("running")).mockResolvedValue(exportJob("cancelled"));
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: "Export WAV" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel WAV export" }));
    expect(dohaApi.cancelWorkspaceJob).toHaveBeenCalledWith("job-export");
    expect(await screen.findByText("취소됨", { exact: true })).toBeVisible();
  });

  it("response loss retry가 동일 action idempotency key를 재사용한다", async () => {
    vi.mocked(dohaApi.createWorkspaceExportJob)
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce(exportJob("queued"));
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: "Export WAV" }));
    await waitFor(() => expect(dohaApi.createWorkspaceExportJob).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(dohaApi.createWorkspaceExportJob).mock.calls;
    expect(calls[0][1]).toBe(calls[1][1]);
  });

  it("project 변경 후 stale create 응답을 무시한다", async () => {
    let resolve!: (job: WorkspaceJobDetailDto) => void;
    vi.mocked(dohaApi.createWorkspaceExportJob).mockReturnValue(new Promise((done) => { resolve = done; }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><WorkingExportControl projectId="project-1" snapshotId="snapshot-1" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Export WAV" }));
    view.rerender(<QueryClientProvider client={client}><WorkingExportControl projectId="project-2" snapshotId="snapshot-2" /></QueryClientProvider>);
    await act(async () => resolve(exportJob("queued")));
    expect(screen.getByText("준비됨", { exact: true })).toBeVisible();
    expect(dohaApi.getWorkspaceJob).not.toHaveBeenCalled();
  });
});

describe("WorkingExportControl multi-format", () => {
beforeEach(() => {
  vi.spyOn(dohaApi, "createWorkspaceExportJob").mockResolvedValue(exportJob("queued"));
  vi.spyOn(dohaApi, "getWorkspaceJob").mockResolvedValue(exportJob("succeeded"));
  vi.spyOn(dohaApi, "cancelWorkspaceJob").mockResolvedValue(exportJob("cancelled"));
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it.each(["mp3", "flac"] as const)("%s selection drives exact Export request and identity", async (format) => {
  renderControl();
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Export format" }), format);
  await userEvent.click(screen.getByRole("button", { name: `Export ${format.toUpperCase()}` }));
  expect(dohaApi.createWorkspaceExportJob).toHaveBeenCalledWith({
    project_id: "project-1", job_type: "export", composition_snapshot_id: "snapshot-1",
    inputs: [], settings_snapshot: { format },
  }, expect.any(String));
  expect(await screen.findByRole("link", { name: `Download exported ${format.toUpperCase()}` }))
    .toHaveAttribute("href", "/backend/api/v1/artifacts/artifact-export/content");
  expect(screen.getByRole("combobox", { name: "Export format" })).toBeEnabled();
});

it("freezes active MP3 identity and disables format selection while running", async () => {
  vi.mocked(dohaApi.getWorkspaceJob).mockResolvedValue(exportJob("running"));
  renderControl();
  const selector = screen.getByRole("combobox", { name: "Export format" });
  await userEvent.selectOptions(selector, "mp3");
  await userEvent.click(screen.getByRole("button", { name: "Export MP3" }));
  expect(await screen.findByText("MP3 렌더링 중", { exact: true })).toBeVisible();
  expect(selector).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel MP3 export" })).toBeVisible();
});
});

function renderControl(snapshotId: string | null = "snapshot-1", disabled = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><WorkingExportControl
    projectId="project-1" snapshotId={snapshotId} disabled={disabled}
    disabledReason={disabled ? "Mixer 변경을 저장하는 동안에는 WAV를 내보낼 수 없습니다." : undefined}
  /></QueryClientProvider>);
}

function exportJob(status: WorkspaceJobStatusDto): WorkspaceJobDetailDto {
  return {
    job_id: "job-export", project_id: "project-1", composition_snapshot_id: "snapshot-1",
    job_type: "export", status, provider_id: null, model_manifest_id: null,
    progress_percent: null, stage: status === "running" ? "render" : null, retry_of_job_id: null,
    created_at: "2026-09-09T00:00:00Z", started_at: status === "queued" ? null : "2026-09-09T00:00:01Z",
    completed_at: ["succeeded", "failed", "cancelled"].includes(status) ? "2026-09-09T00:00:02Z" : null,
    inputs: [], outputs: status === "succeeded" ? [{ output_role: "export", output_order: 0, asset_version_id: "version-export", artifact_id: "artifact-export" }] : [],
    model_usages: [], error_code: status === "failed" ? "EXPORT_RENDER_FAILED" : null,
    error_message: status === "failed" ? "internal detail" : null, error_retryable: status === "failed", error_details_id: null,
  };
}
