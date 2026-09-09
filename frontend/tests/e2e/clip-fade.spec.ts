import { expect, test, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const projectId = "project-fade";
const snapshotId = "snapshot-fade";
const workingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const trackId = "11111111-1111-4111-8111-111111111111";
const clipId = "22222222-2222-4222-8222-222222222222";
const versionId = "66666666-6666-4666-8666-666666666666";
const artifactId = "77777777-7777-4777-8777-777777777777";
const workingBase = `/backend/api/v1/projects/${projectId}/working-composition`;
const wav = createSilentWav(8);

class FadeBackend {
  revision = 2;
  clip = {
    clip_id: clipId, track_id: trackId, source_asset_version_id: versionId,
    timeline_start: "0.000", source_in: "0.000", source_out: "8.000",
    source_duration: "8.000", timeline_duration: "8.000", loop_enabled: false, loop_phase: "0",
    gain_db: "0.00", fade_in: "0.125", fade_out: "0.25",
    split_from_clip_id: null as string | null,
  };
  committedClip = { ...this.clip };
  completions = new Map<string, Record<string, unknown>>();
  fadeRequests: { body: Record<string, unknown>; key: string | null }[] = [];
  gainRequests: { body: Record<string, unknown>; key: string | null }[] = [];
  previewPosts = 0;
  resolverRequests = 0;
  contentRequests = 0;
  loseNextFadeResponse = false;
  history = new PersistentHistoryFixture(workingId, {
    get: () => this.revision,
    increment: () => ++this.revision,
  });

  install(page: Page) { return page.route("**/backend/**", (route) => this.handle(route)); }

  private async handle(route: Route) {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : {};
    const key = request.headers()["idempotency-key"] ?? null;
    if (path === "/backend/health") return this.ok(route, { status: "ok" });
    if (path === `/backend/api/v1/projects/${projectId}`) return this.ok(route, { data: { ...project(), project_id: projectId } });
    if (path === `/backend/api/v1/projects/${projectId}/composition`) return this.ok(route, { data: composition() });
    if (path === "/backend/api/v1/snapshots") return this.ok(route, { data: [] });
    if (path === `/backend/api/v1/projects/${projectId}/asset-versions/${versionId}/media-source`) {
      this.resolverRequests += 1;
      return this.ok(route, { data: {
        asset_version_id: versionId, artifact_id: artifactId, media_type: "audio/wav",
        size_bytes: wav.length, artifact_checksum: "a".repeat(64), duration_seconds: "8.000",
        content_url: `/api/v1/artifacts/${artifactId}/content`,
      } });
    }
    if (path === `/backend/api/v1/artifacts/${artifactId}/content`) {
      this.contentRequests += 1;
      return route.fulfill({ status: 200, contentType: "audio/wav", body: wav });
    }
    if (path === "/backend/api/v1/jobs/job-fade-preview") return this.ok(route, { data: previewJob() });
    if (!path.startsWith(workingBase)) return route.fulfill({ status: 404, json: { error: { code: "TEST_ROUTE_NOT_FOUND" } } });
    const relative = path.slice(workingBase.length);
    if (method === "GET" && relative === "") return this.ok(route, { data: this.snapshot() });
    if (method === "GET" && relative === "/history") return this.ok(route, { data: this.history.projection() });
    if (method === "POST" && relative === "/history/undo") return this.history.mutate(route, "undo");
    if (method === "POST" && relative === "/history/redo") return this.history.mutate(route, "redo");
    if (relative === "/preview" && method === "POST") {
      this.previewPosts += 1;
      return this.ok(route, { data: {
        job_id: "job-fade-preview", preview_render_id: "render-fade-preview",
        working_composition_id: workingId, rendered_revision: this.revision,
        status: "queued", replayed: false,
      } });
    }
    if (relative === "/commit" && method === "POST") {
      this.committedClip = { ...this.clip };
      this.history.barrier();
      return this.mutated(route, key, { working_composition_id: workingId, composition_snapshot_id: "snapshot-fade-2" });
    }
    if (relative === "/checkout" && method === "POST") {
      this.clip = { ...this.committedClip };
      this.history.barrier();
      return this.mutated(route, key, { working_composition_id: workingId, base_composition_snapshot_id: "snapshot-fade-2" });
    }
    const fadeAttempt = relative === `/clips/${clipId}/fade` && method === "PATCH";
    if (fadeAttempt) this.fadeRequests.push({ body, key });
    if (key && this.completions.has(key)) {
      return this.ok(route, { data: { ...this.completions.get(key), replayed: true } });
    }
    if (body.expected_revision !== this.revision) return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    if (fadeAttempt) {
      const before = { fade_in: this.clip.fade_in, fade_out: this.clip.fade_out };
      const after = { fade_in: String(body.fade_in), fade_out: String(body.fade_out) };
      this.clip.fade_in = String(body.fade_in);
      this.clip.fade_out = String(body.fade_out);
      this.history.append({
        clipId,
        applyBefore: () => Object.assign(this.clip, before),
        applyAfter: () => Object.assign(this.clip, after),
      });
      const lose = this.loseNextFadeResponse;
      this.loseNextFadeResponse = false;
      return this.mutated(route, key, { clip_id: clipId }, lose);
    }
    if (relative === `/clips/${clipId}/gain` && method === "PATCH") {
      this.gainRequests.push({ body, key });
      const before = this.clip.gain_db;
      const after = Number(body.gain_db).toFixed(2);
      this.clip.gain_db = after;
      this.history.append({
        clipId,
        applyBefore: () => { this.clip.gain_db = before; },
        applyAfter: () => { this.clip.gain_db = after; },
      });
      return this.mutated(route, key, { clip_id: clipId });
    }
    return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
  }

  private snapshot() {
    return {
      working_composition_id: workingId, project_id: projectId,
      base_composition_snapshot_id: snapshotId, revision: this.revision, mix_settings: {},
      tracks: [{ track_id: trackId, track_type: "audio", name: "Fade Track", track_order: 0 }],
      clips: [{ ...this.clip }], timeline_duration: "8.000",
    };
  }

  private mutated(route: Route, key: string | null, result: Record<string, unknown>, lose = false) {
    this.revision += 1;
    const completion = { ...result, completed_revision: this.revision, replayed: false };
    if (key) this.completions.set(key, completion);
    if (lose) return route.abort("failed");
    return this.ok(route, { data: completion });
  }
  private ok(route: Route, json: unknown) { return route.fulfill({ status: 200, json }); }
  private error(route: Route, status: number, code: string) {
    return route.fulfill({ status, json: { error: { code, message: code } } });
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("doha-studio-settings", JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }));
    Object.defineProperty(HTMLMediaElement.prototype, "readyState", { configurable: true, get: () => 4 });
    HTMLMediaElement.prototype.play = async function () { this.dispatchEvent(new Event("play")); };
    HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
  });
});

test("Clip Fade exact editing, history, Preview stale, Commit/Checkout과 responsive accessibility", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const backend = new FadeBackend();
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: /Clip 22222222 선택 및 이동/ }).click();
  const fadeIn = page.getByLabel("Fade In exact value");
  const fadeOut = page.getByLabel("Fade Out exact value");
  await expect(fadeIn).toHaveValue("0.125");
  await expect(fadeOut).toHaveValue("0.25");
  await expect(fadeIn).toHaveAttribute("min", "0");
  await expect(fadeIn).toHaveAttribute("step", "0.000001");
  await expect(fadeOut).toHaveAttribute("aria-describedby", /clip-fade-help/);
  const box = await page.locator(".working-clip-fade").boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1);

  if (testInfo.project.name !== "chromium") {
    await exactFadeIn(page, "0.123456");
    await expect(fadeIn).toHaveValue("0.123456");
    await expect(fadeOut).toHaveValue("0.25");
    expect(backend.fadeRequests).toHaveLength(1);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors.filter((item) => !item.includes("Failed to load resource"))).toEqual([]);
    return;
  }

  await page.getByRole("button", { name: "Working Preview 만들기" }).click();
  await expect(page.getByRole("button", { name: "Working Preview revision 2 재생" })).toBeVisible();
  const audio = page.locator("audio");
  const audioSource = await audio.getAttribute("src");
  const resolverBefore = backend.resolverRequests;
  const contentBefore = backend.contentRequests;

  await fadeIn.fill("7.9");
  await fadeIn.blur();
  await expect(page.locator(".working-clip-fade [role='alert']")).toContainText("합은 Clip 길이 8초 이하");
  expect(backend.fadeRequests).toHaveLength(0);

  backend.loseNextFadeResponse = true;
  await exactFadeIn(page, "0.123456");
  await expect.poll(() => backend.fadeRequests.length).toBe(2);
  expect(backend.fadeRequests[0].key).toBe(backend.fadeRequests[1].key);
  expect(backend.fadeRequests[0].body).toMatchObject({ expected_revision: 2, fade_in: 0.123456, fade_out: 0.25 });
  await exactFadeOut(page, "0.75");
  await expect.poll(() => backend.fadeRequests.length).toBe(3);
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
  expect(backend.previewPosts).toBe(1);
  expect(backend.resolverRequests).toBe(resolverBefore);
  expect(backend.contentRequests).toBe(contentBefore);
  expect(await audio.getAttribute("src")).toBe(audioSource);

  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(fadeIn).toHaveValue("0.123456");
  await expect(fadeOut).toHaveValue("0.25");
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(fadeIn).toHaveValue("0.125");
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(fadeIn).toHaveValue("0.123456");
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(fadeOut).toHaveValue("0.75");

  const gain = page.getByLabel("Clip gain exact value");
  await gain.fill("3");
  await gain.blur();
  await expect(gain).toHaveValue("3.00");
  await exactFadeIn(page, "0.5");
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(fadeIn).toHaveValue("0.123456");
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(gain).toHaveValue("0.00");
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(gain).toHaveValue("3.00");
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(fadeIn).toHaveValue("0.5");

  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByText(/Undo\/Redo 기록이 초기화/)).toBeVisible();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  await exactFadeIn(page, "1");
  await exactFadeOut(page, "1.25");
  await page.getByRole("button", { name: "현재 Snapshot Checkout" }).click();
  await expect(fadeIn).toHaveValue("0.5");
  await expect(fadeOut).toHaveValue("0.75");
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((item) => !item.includes("Failed to load resource"))).toEqual([]);
});

async function exactFadeIn(page: Page, value: string) {
  const input = page.getByLabel("Fade In exact value");
  await input.fill(value);
  await input.blur();
}
async function exactFadeOut(page: Page, value: string) {
  const input = page.getByLabel("Fade Out exact value");
  await input.fill(value);
  await input.blur();
}

function project() {
  return { id: projectId, title: "Clip Fade E2E", description: "stateful Fade fixture", created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z", job_count: 0, jobs: [] };
}
function composition() {
  return {
    state: "ready",
    project: { project_id: projectId, workspace_id: "workspace-1", title: "Clip Fade E2E", lifecycle_status: "active", created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z" },
    selection: { selected_snapshot_id: snapshotId, resolved_snapshot_id: snapshotId, resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: snapshotId, project_id: projectId, snapshot_version: 1, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-09-03T00:00:00Z" },
    items: [{ snapshot_item_id: "item-fade", item_role: "mix", sort_order: 0, asset_version: { asset_version_id: versionId, asset_id: "asset-fade", version_number: 1, version_origin: "fixture", parent_asset_version_id: null, processing_chain_id: null, provider_id: null, model_manifest_id: null, settings_snapshot: {}, created_at: "2026-09-03T00:00:00Z" }, artifacts: [{ artifact_id: artifactId, asset_version_id: versionId, artifact_kind: "audio", media_type: "audio/wav", size_bytes: wav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture", producer_type: "test", producer_id: null, run_id: null, retention_status: "active", created_at: "2026-09-03T00:00:00Z", content_url: `/api/v1/artifacts/${artifactId}/content`, download_url: null }] }],
    track_projections: [], section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {}, lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}
function previewJob() {
  return {
    job_id: "job-fade-preview", project_id: projectId, composition_snapshot_id: null,
    job_type: "working_preview_render", status: "succeeded", provider_id: null, model_manifest_id: null,
    progress_percent: 100, stage: null, retry_of_job_id: null, inputs: [],
    outputs: [{ output_role: "working_preview", output_order: 0, asset_version_id: null, artifact_id: artifactId }],
    model_usages: [], error_code: null, error_message: null, error_retryable: null, error_details_id: null,
    created_at: "2026-09-03T00:00:00Z", started_at: null, completed_at: "2026-09-03T00:00:01Z",
  };
}
function createSilentWav(seconds: number) {
  const sampleRate = 8_000;
  const samples = sampleRate * seconds;
  const dataSize = samples * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + dataSize, 4); buffer.write("WAVE", 8);
  buffer.write("fmt ", 12); buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22); buffer.writeUInt32LE(sampleRate, 24); buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34); buffer.write("data", 36); buffer.writeUInt32LE(dataSize, 40);
  return buffer;
}
