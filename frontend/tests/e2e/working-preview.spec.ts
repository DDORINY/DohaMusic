import { expect, test, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const projectId = "project-preview-1";
const secondProjectId = "project-preview-2";
const workingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const trackId = "11111111-1111-4111-8111-111111111111";
const clipId = "22222222-2222-4222-8222-222222222222";
const assetVersionId = "33333333-3333-4333-8333-333333333333";
const artifactId = "44444444-4444-4444-8444-444444444444";
const previewArtifactId = "55555555-5555-4555-8555-555555555555";
const silentWav = createSilentWav(2);

type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

class PreviewBackend {
  revision = 4;
  title = "Preview Track";
  previewPosts: Array<{ revision: number; key: string | null }> = [];
  jobReads = 0;
  loseFirstResponse = false;
  revisionConflict = false;
  terminal: JobStatus = "succeeded";
  holdJob = false;
  previewExpired = false;
  workspaceReads = 0;
  historyReads = 0;
  exportPosts: Array<{ key: string | null; snapshotId: string }> = [];
  exportJobReads = 0;
  private completionByKey = new Map<string, { jobId: string; revision: number }>();
  private histories = new Map<string, PersistentHistoryFixture>();

  async install(page: Page) {
    await page.route("**/backend/**", (route) => this.handle(route));
  }

  private async handle(route: Route) {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === "/backend/health") return this.ok(route, { status: "ok" });
    if (path === "/backend/api/v1/jobs" && method === "POST") {
      const body = request.postDataJSON() as { composition_snapshot_id: string; job_type: string };
      if (body.job_type !== "export") return this.error(route, 422, "INVALID_JOB_TYPE");
      this.exportPosts.push({
        key: request.headers()["idempotency-key"] ?? null,
        snapshotId: body.composition_snapshot_id,
      });
      return this.ok(route, { data: exportJob("queued") }, 201);
    }
    if (path === "/backend/api/v1/jobs/job-export") {
      this.exportJobReads += 1;
      const status: JobStatus = this.exportJobReads === 1 ? "queued" : this.exportJobReads === 2 ? "running" : "succeeded";
      return this.ok(route, { data: exportJob(status) });
    }
    if (path === "/backend/api/v1/artifacts/artifact-export/content") {
      return route.fulfill({
        status: 200,
        body: silentWav,
        contentType: "audio/wav",
        headers: { "Content-Disposition": "attachment; filename=export.wav" },
      });
    }
    const projectMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])$/);
    if (projectMatch) return this.ok(route, { data: { ...project(projectMatch[1]), project_id: projectMatch[1] } });
    const compositionMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])\/composition$/);
    if (compositionMatch) return this.ok(route, { data: composition(compositionMatch[1]) });
    const workingMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])\/working-composition$/);
    if (workingMatch && method === "GET") {
      this.workspaceReads += 1;
      return this.ok(route, { data: this.working(workingMatch[1]) });
    }
    if (workingMatch && method === "PATCH") return this.error(route, 405, "METHOD_NOT_ALLOWED");
    const historyMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])\/working-composition\/history(?:\/(undo|redo))?$/);
    if (historyMatch && method === "GET" && !historyMatch[2]) {
      this.historyReads += 1;
      return this.ok(route, { data: this.historyFor(historyMatch[1]).projection() });
    }
    if (historyMatch && method === "POST" && historyMatch[2]) {
      return this.historyFor(historyMatch[1]).mutate(route, historyMatch[2] as "undo" | "redo");
    }
    const mediaMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])\/asset-versions\/[^/]+\/media-source$/);
    if (mediaMatch) return this.ok(route, { data: mediaSource() });
    if (path === `/backend/api/v1/artifacts/${artifactId}/content`) {
      return route.fulfill({ status: 200, contentType: "audio/wav", body: silentWav });
    }
    if (path === `/backend/api/v1/artifacts/${previewArtifactId}/content`) {
      return this.previewExpired
        ? this.error(route, 410, "ARTIFACT_PAYLOAD_EXPIRED")
        : route.fulfill({ status: 200, contentType: "audio/wav", body: silentWav });
    }
    if (path.endsWith("/working-composition/preview") && method === "POST") {
      const body = request.postDataJSON() as { expected_revision: number };
      const key = request.headers()["idempotency-key"] ?? null;
      this.previewPosts.push({ revision: body.expected_revision, key });
      if (this.revisionConflict) {
        this.revisionConflict = false;
        this.revision += 1;
        return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      }
      const existing = key ? this.completionByKey.get(key) : undefined;
      const completion = existing ?? { jobId: crypto.randomUUID(), revision: body.expected_revision };
      if (key) this.completionByKey.set(key, completion);
      if (this.loseFirstResponse) {
        this.loseFirstResponse = false;
        return route.abort("connectionreset");
      }
      return this.ok(route, { data: {
        job_id: completion.jobId,
        preview_render_id: crypto.randomUUID(),
        working_composition_id: workingId,
        rendered_revision: completion.revision,
        status: "queued",
        replayed: Boolean(existing),
      } }, 202);
    }
    const jobMatch = path.match(/^\/backend\/api\/v1\/jobs\/([^/]+)$/);
    if (jobMatch) {
      this.jobReads += 1;
      const status: JobStatus = this.holdJob
        ? "queued"
        : this.jobReads === 1
          ? "queued"
          : this.jobReads === 2
            ? "running"
            : this.terminal;
      const keyCompletion = [...this.completionByKey.values()].find((item) => item.jobId === decodeURIComponent(jobMatch[1]));
      return this.ok(route, { data: job(decodeURIComponent(jobMatch[1]), status, keyCompletion?.revision ?? this.revision) });
    }
    const renameMatch = path.match(/^\/backend\/api\/v1\/projects\/(project-preview-[12])\/working-composition\/tracks\/[^/]+$/);
    if (renameMatch && method === "PATCH") {
      const body = request.postDataJSON() as { name: string; expected_revision: number };
      if (body.expected_revision !== this.revision) return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      const before = this.title;
      const after = body.name;
      this.title = after;
      this.historyFor(renameMatch[1]).append({
        clipId,
        applyBefore: () => { this.title = before; },
        applyAfter: () => { this.title = after; },
      });
      this.revision += 1;
      return this.ok(route, { data: { track_id: trackId, completed_revision: this.revision, replayed: false } });
    }
    return this.error(route, 404, "TEST_ROUTE_NOT_FOUND");
  }

  private working(id: string) {
    return {
      working_composition_id: workingId,
      project_id: id,
      base_composition_snapshot_id: "snapshot-1",
      revision: this.revision,
      mix_settings: {},
      tracks: [{ track_id: trackId, track_type: "audio", name: this.title, track_order: 0 }],
      clips: [{ clip_id: clipId, track_id: trackId, source_asset_version_id: assetVersionId, timeline_start: "0.000", source_in: "0.000", source_out: "2.000", source_duration: "2.000", timeline_duration: "2.000", loop_enabled: false, loop_phase: "0", gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null }],
      timeline_duration: "2.000",
    };
  }

  historyFor(project: string) {
    let history = this.histories.get(project);
    if (!history) {
      history = new PersistentHistoryFixture(workingId, {
        get: () => this.revision,
        increment: () => ++this.revision,
      });
      this.histories.set(project, history);
    }
    return history;
  }

  private ok(route: Route, json: unknown, status = 200) { return route.fulfill({ status, json }); }
  private error(route: Route, status: number, code: string) { return route.fulfill({ status, json: { error: { code, message: code } } }); }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("doha-studio-settings", JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }));
    (window as Window & { __previewPlayCalls?: number }).__previewPlayCalls = 0;
    HTMLMediaElement.prototype.play = async function () {
      (window as Window & { __previewPlayCalls?: number }).__previewPlayCalls = ((window as Window & { __previewPlayCalls?: number }).__previewPlayCalls ?? 0) + 1;
      this.dispatchEvent(new Event("play"));
    };
    HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
  });
});

test("explicit Preview, polling, Global Player, stale, rerender와 refresh authority", async ({ page }) => {
  test.setTimeout(60_000);
  const backend = new PreviewBackend();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);

  const previewButton = page.getByRole("button", { name: "Working Preview 만들기" });
  await expect(previewButton).toBeVisible();
  await previewButton.click();
  await expect(page.getByText("대기 중", { exact: true })).toBeVisible();
  await expect(page.getByText("렌더링 중", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("재생 준비됨", { exact: true })).toBeVisible({ timeout: 10_000 });
  expect(backend.previewPosts).toHaveLength(1);
  expect(backend.previewPosts[0]).toMatchObject({ revision: 4 });
  expect(backend.previewPosts[0].key).toBeTruthy();
  await expect(page.locator("audio")).toHaveCount(1);
  expect(await page.evaluate(() => (window as Window & { __previewPlayCalls?: number }).__previewPlayCalls)).toBe(0);

  await page.getByRole("button", { name: "Working Preview revision 4 재생" }).click();
  await expect(page.locator("audio")).toHaveAttribute("src", `/backend/api/v1/artifacts/${previewArtifactId}/content`);
  expect(await page.evaluate(() => (window as Window & { __previewPlayCalls?: number }).__previewPlayCalls)).toBe(1);

  const trackName = page.getByLabel("Preview Track Track 이름");
  await trackName.fill("Edited Preview Track");
  await trackName.blur();
  await expect(page.getByText(/revision 5/).first()).toBeVisible();
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Working Preview revision 4 재생" })).toBeEnabled();
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(page.getByLabel("Preview Track Track 이름")).toBeVisible();
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(page.getByLabel("Edited Preview Track Track 이름")).toBeVisible();
  await page.getByRole("button", { name: "Preview 다시 만들기" }).click();
  await expect(page.getByText("재생 준비됨", { exact: true })).toBeVisible({ timeout: 10_000 });
  expect(backend.previewPosts).toHaveLength(2);
  expect(backend.previewPosts[1].revision).toBe(7);
  expect(backend.previewPosts[1].key).not.toBe(backend.previewPosts[0].key);
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toHaveCount(0);

  await page.reload();
  await expect(page.getByLabel("현재 편집본 미리듣기").getByText("준비됨", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Working Preview 만들기" })).toBeEnabled();
  expect(backend.previewPosts).toHaveLength(2);
  expect(pageErrors).toEqual([]);
});

test("response-loss same-key, revision conflict, failed/cancelled, expiry와 Project reset", async ({ page }) => {
  test.setTimeout(75_000);
  const backend = new PreviewBackend();
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);

  backend.loseFirstResponse = true;
  await page.getByRole("button", { name: "Working Preview 만들기" }).click();
  await expect(page.getByText("재생 준비됨", { exact: true })).toBeVisible({ timeout: 15_000 });
  expect(backend.previewPosts).toHaveLength(2);
  expect(backend.previewPosts[0].key).toBe(backend.previewPosts[1].key);

  backend.revisionConflict = true;
  const workspaceReadsBeforeConflict = backend.workspaceReads;
  const historyReadsBeforeConflict = backend.historyReads;
  await page.getByRole("button", { name: "Preview 다시 만들기" }).click();
  await expect(page.getByText(/최신 revision을 확인한 뒤/)).toBeVisible();
  await expect(page.getByText(/revision 5/).first()).toBeVisible();
  expect(backend.workspaceReads).toBeGreaterThan(workspaceReadsBeforeConflict);
  expect(backend.historyReads).toBeGreaterThan(historyReadsBeforeConflict);
  expect(backend.previewPosts).toHaveLength(3);

  backend.terminal = "failed";
  backend.jobReads = 2;
  await page.getByRole("button", { name: "Preview 다시 만들기" }).click();
  await expect(page.getByText("실패", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/원본 오디오를 사용할 수 없습니다/)).toBeVisible();
  await expect(page.getByText("raw path must not render")).toHaveCount(0);

  backend.terminal = "cancelled";
  backend.jobReads = 2;
  await page.getByRole("button", { name: "Preview 다시 만들기" }).click();
  await expect(page.getByText("취소됨", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Working Preview revision 4 재생" })).toBeEnabled();

  backend.previewExpired = true;
  await page.getByRole("button", { name: "Working Preview revision 4 재생" }).click();
  await page.locator("audio").dispatchEvent("error");
  await expect(page.getByText(/만료되었거나 사용할 수 없습니다/)).toBeVisible();

  backend.holdJob = true;
  await page.getByRole("button", { name: "Preview 다시 만들기" }).click();
  await expect(page.getByText("대기 중", { exact: true })).toBeVisible();
  await page.goto(`/projects/${secondProjectId}`);
  await expect(page.getByLabel("현재 편집본 미리듣기").getByText("준비됨", { exact: true })).toBeVisible();
  await expect(page.getByText("대기 중", { exact: true })).toHaveCount(0);
});

test("DAW Export WAV action은 public Job polling 후 Artifact를 다운로드한다", async ({ page }) => {
  test.setTimeout(60_000);
  const backend = new PreviewBackend();
  await backend.install(page);
  await page.route("**/backend/api/v1/artifacts/artifact-export/content", (route) => route.fulfill({
    status: 200,
    body: silentWav,
    contentType: "audio/wav",
    headers: { "Content-Disposition": "attachment; filename=export.wav" },
  }));
  await page.goto(`/projects/${projectId}`);
  const exportAction = page.getByRole("button", { name: "Export WAV" });
  await expect(exportAction).toBeVisible();
  await exportAction.dblclick();
  await expect(page.getByText("대기 중", { exact: true })).toBeVisible();
  await expect(page.getByText("WAV 렌더링 중", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("완료", { exact: true })).toBeVisible({ timeout: 10_000 });
  expect(backend.exportPosts).toEqual([{ key: expect.any(String), snapshotId: "snapshot-1" }]);
  await expect(page.getByRole("link", { name: "Download exported WAV" }))
    .toHaveAttribute("href", "/backend/api/v1/artifacts/artifact-export/content");
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
});

function project(id: string) {
  return { id, title: `Working Preview ${id.at(-1)}`, description: "stateful Preview fixture", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z", job_count: 0, jobs: [] };
}

function composition(id: string) {
  return {
    state: "ready",
    project: { project_id: id, workspace_id: "workspace-1", title: `Working Preview ${id.at(-1)}`, lifecycle_status: "active", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
    selection: { selected_snapshot_id: "snapshot-1", resolved_snapshot_id: "snapshot-1", resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: "snapshot-1", project_id: id, snapshot_version: 1, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-08-29T00:00:00Z" },
    items: [{ snapshot_item_id: "item-1", item_role: "mix", sort_order: 0, asset_version: { asset_version_id: assetVersionId, asset_id: "asset-1", version_number: 1, version_origin: "provider_output", parent_asset_version_id: null, processing_chain_id: null, provider_id: "fixture", model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-29T00:00:00Z" }, artifacts: [{ artifact_id: artifactId, asset_version_id: assetVersionId, artifact_kind: "audio", media_type: "audio/wav", size_bytes: silentWav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture", producer_type: "test", producer_id: "fixture", run_id: null, retention_status: "active", created_at: "2026-08-29T00:00:00Z", content_url: `/api/v1/artifacts/${artifactId}/content`, download_url: null }] }],
    track_projections: [{ projection_id: "item-1", identity_scope: "snapshot", snapshot_item_id: "item-1", item_role: "mix", sort_order: 0, asset_id: "asset-1", asset_version_id: assetVersionId }],
    section_projection: { availability: "not_available", items: [] }, mix_settings_snapshot: {}, lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function mediaSource() {
  return { asset_version_id: assetVersionId, artifact_id: artifactId, media_type: "audio/wav", size_bytes: silentWav.length, artifact_checksum: "fixture", duration_seconds: "2", content_url: `/api/v1/artifacts/${artifactId}/content` };
}

function job(jobId: string, status: JobStatus, revision: number) {
  return {
    job_id: jobId, project_id: projectId, composition_snapshot_id: null, job_type: "working_preview", status,
    provider_id: null, model_manifest_id: null, progress_percent: status === "running" ? 50 : null,
    stage: status === "running" ? "render" : null, retry_of_job_id: null, created_at: "2026-08-29T00:00:00Z",
    started_at: status === "queued" ? null : "2026-08-29T00:00:01Z", completed_at: ["succeeded", "failed", "cancelled"].includes(status) ? "2026-08-29T00:00:02Z" : null,
    inputs: [], outputs: status === "succeeded" ? [{ output_role: "working_preview", output_order: 0, asset_version_id: null, artifact_id: previewArtifactId }] : [], model_usages: [],
    error_code: status === "failed" ? "WORKING_PREVIEW_SOURCE_UNAVAILABLE" : null,
    error_message: status === "failed" ? "raw path must not render" : null, error_retryable: status === "failed", error_details_id: null,
    rendered_revision: revision,
  };
}

function exportJob(status: JobStatus) {
  return {
    job_id: "job-export", project_id: projectId, composition_snapshot_id: "snapshot-1",
    job_type: "export", status, provider_id: null, model_manifest_id: null,
    progress_percent: null, stage: status === "running" ? "render" : null, retry_of_job_id: null,
    created_at: "2026-09-09T00:00:00Z", started_at: status === "queued" ? null : "2026-09-09T00:00:01Z",
    completed_at: status === "succeeded" ? "2026-09-09T00:00:02Z" : null, inputs: [],
    outputs: status === "succeeded" ? [{ output_role: "export", output_order: 0, asset_version_id: "version-export", artifact_id: "artifact-export" }] : [],
    model_usages: [], error_code: null, error_message: null, error_retryable: null, error_details_id: null,
  };
}

function createSilentWav(durationSeconds: number) {
  const sampleRate = 8_000;
  const dataSize = sampleRate * durationSeconds;
  const wav = Buffer.alloc(44 + dataSize, 128);
  wav.write("RIFF", 0, "ascii"); wav.writeUInt32LE(36 + dataSize, 4); wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii"); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate, 28); wav.writeUInt16LE(1, 32); wav.writeUInt16LE(8, 34);
  wav.write("data", 36, "ascii"); wav.writeUInt32LE(dataSize, 40);
  return wav;
}
