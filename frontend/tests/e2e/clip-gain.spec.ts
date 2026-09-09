import { expect, test, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const projectId = "project-gain";
const snapshotId = "snapshot-gain";
const workingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const trackId = "11111111-1111-4111-8111-111111111111";
const originalId = "22222222-2222-4222-8222-222222222222";
const copiedId = "33333333-3333-4333-8333-333333333333";
const leftId = "44444444-4444-4444-8444-444444444444";
const rightId = "55555555-5555-4555-8555-555555555555";
const versionId = "66666666-6666-4666-8666-666666666666";
const artifactId = "77777777-7777-4777-8777-777777777777";
const workingBase = `/backend/api/v1/projects/${projectId}/working-composition`;
const mediaPath = `/backend/api/v1/projects/${projectId}/asset-versions/${versionId}/media-source`;
const contentPath = `/backend/api/v1/artifacts/${artifactId}/content`;
const wav = createSilentWav(8);

type Clip = {
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
};

type GainRequest = {
  clipId: string;
  body: Record<string, unknown>;
  key: string | null;
};

class GainBackend {
  revision = 2;
  clips: Clip[] = [clip(originalId, "0.000", "0.000", "2.000", "0.00")];
  tombstones = new Map<string, Clip>();
  lineage: { original: Clip; left: Clip; right: Clip } | null = null;
  completions = new Map<string, Record<string, unknown>>();
  gainRequests: GainRequest[] = [];
  previewPosts = 0;
  commitPosts = 0;
  resolverRequests = 0;
  contentRequests = 0;
  loseNextGainResponse = false;
  rejectNextGain = false;
  conflictNextGain: string | null = null;
  committedClips: Clip[] = [];
  historyUndoPosts = 0;
  historyRedoPosts = 0;
  history = new PersistentHistoryFixture(workingId, {
    get: () => this.revision,
    increment: () => ++this.revision,
  });

  async install(page: Page) {
    await page.route("**/backend/**", (route) => this.handle(route));
  }

  forceSplitDivergence() {
    const left = this.clips.find((item) => item.clip_id === leftId);
    if (!left) throw new Error("left child missing");
    left.gain_db = "1.00";
  }

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
    if (path === mediaPath) {
      this.resolverRequests += 1;
      return this.ok(route, { data: {
        asset_version_id: versionId, artifact_id: artifactId, media_type: "audio/wav",
        size_bytes: wav.length, artifact_checksum: "a".repeat(64), duration_seconds: "8.000",
        content_url: `/api/v1/artifacts/${artifactId}/content`,
      } });
    }
    if (path === contentPath) {
      this.contentRequests += 1;
      return route.fulfill({ status: 200, contentType: "audio/wav", body: wav });
    }
    if (path === "/backend/api/v1/jobs/job-gain-preview") return this.ok(route, { data: previewJob() });
    if (!path.startsWith(workingBase)) return route.fulfill({ status: 404, json: { error: { code: "TEST_ROUTE_NOT_FOUND", message: path } } });

    const relative = path.slice(workingBase.length);
    if (method === "GET" && relative === "") return this.ok(route, { data: this.snapshot() });
    if (method === "GET" && relative === "/history") return this.ok(route, { data: this.history.projection() });
    if (method === "POST" && relative === "/history/undo") {
      this.historyUndoPosts += 1;
      return this.history.mutate(route, "undo");
    }
    if (method === "POST" && relative === "/history/redo") {
      this.historyRedoPosts += 1;
      return this.history.mutate(route, "redo");
    }
    const gainAttempt = relative.match(/^\/clips\/([^/]+)\/gain$/);
    if (gainAttempt && method === "PATCH") {
      this.gainRequests.push({ clipId: decodeURIComponent(gainAttempt[1]), body, key });
    }
    if (key && this.completions.has(key)) {
      return this.ok(route, { data: { ...this.completions.get(key), replayed: true } });
    }
    if (relative === "/preview" && method === "POST") {
      this.previewPosts += 1;
      return this.ok(route, { data: {
        job_id: "job-gain-preview", preview_render_id: "render-gain-preview",
        working_composition_id: workingId, rendered_revision: this.revision,
        status: "queued", replayed: false,
      } });
    }
    if (relative === "/commit" && method === "POST") {
      this.commitPosts += 1;
      this.committedClips = this.clips.map((item) => ({ ...item }));
      this.history.barrier();
      return this.mutated(route, key, {
        working_composition_id: workingId, composition_snapshot_id: "snapshot-gain-2",
      });
    }
    if (relative === "/checkout" && method === "POST") {
      this.clips = this.committedClips.map((item) => ({ ...item }));
      this.history.barrier();
      return this.mutated(route, key, {
        working_composition_id: workingId, base_composition_snapshot_id: "snapshot-gain-2",
      });
    }

    if (body.expected_revision !== this.revision) return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    const match = relative.match(/^\/clips\/([^/]+)(?:\/(gain|copy|split|restore|unsplit|resplit))?$/);
    if (!match) return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
    const clipId = decodeURIComponent(match[1]);
    const operation = match[2];

    if (operation === "gain" && method === "PATCH") {
      if (this.rejectNextGain) {
        this.rejectNextGain = false;
        return this.error(route, 422, "CLIP_GAIN_OUT_OF_RANGE");
      }
      if (this.conflictNextGain !== null) {
        this.requiredClip(clipId).gain_db = this.conflictNextGain;
        this.conflictNextGain = null;
        this.revision += 1;
        return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      }
      const before = this.requiredClip(clipId).gain_db;
      const after = Number(body.gain_db).toFixed(2);
      this.requiredClip(clipId).gain_db = after;
      this.history.append({
        clipId,
        applyBefore: () => { this.requiredClip(clipId).gain_db = before; },
        applyAfter: () => { this.requiredClip(clipId).gain_db = after; },
      });
      const lose = this.loseNextGainResponse;
      this.loseNextGainResponse = false;
      return this.mutated(route, key, { clip_id: clipId }, lose);
    }
    if (operation === "copy" && method === "POST") {
      const source = this.requiredClip(clipId);
      this.clips.push({
        ...source,
        clip_id: copiedId,
        track_id: String(body.target_track_id),
        timeline_start: Number(body.target_timeline_start).toFixed(3),
        split_from_clip_id: null,
      });
      this.history.barrier();
      return this.mutated(route, key, { clip_id: copiedId });
    }
    if (operation === "split" && method === "POST") {
      const original = { ...this.requiredClip(clipId) };
      const splitAt = Number(body.split_at);
      const left = { ...original, clip_id: leftId, source_out: splitAt.toFixed(3), timeline_duration: (splitAt - Number(original.source_in)).toFixed(3), split_from_clip_id: original.clip_id };
      const right = {
        ...original,
        clip_id: rightId,
        timeline_start: (Number(original.timeline_start) + splitAt - Number(original.source_in)).toFixed(3),
        source_in: splitAt.toFixed(3),
        timeline_duration: (Number(original.source_out) - splitAt).toFixed(3),
        split_from_clip_id: original.clip_id,
      };
      this.lineage = { original, left: { ...left }, right: { ...right } };
      this.clips = this.clips.filter((item) => item.clip_id !== clipId).concat(left, right);
      this.history.barrier();
      return this.mutated(route, key, { original_clip_id: clipId, left_clip_id: leftId, right_clip_id: rightId });
    }
    if (operation === "unsplit" && method === "POST") {
      if (!this.lineage) return this.error(route, 409, "SPLIT_STRUCTURE_CONFLICT");
      const left = this.requiredClip(leftId);
      const right = this.requiredClip(rightId);
      if (left.gain_db !== this.lineage.original.gain_db || right.gain_db !== this.lineage.original.gain_db) {
        return this.error(route, 409, "SPLIT_STRUCTURE_CONFLICT");
      }
      this.clips = this.clips.filter((item) => ![leftId, rightId].includes(item.clip_id)).concat({ ...this.lineage.original });
      return this.mutated(route, key, { original_clip_id: clipId, left_clip_id: leftId, right_clip_id: rightId });
    }
    if (operation === "resplit" && method === "POST") {
      if (!this.lineage) return this.error(route, 409, "SPLIT_STRUCTURE_CONFLICT");
      this.clips = this.clips.filter((item) => item.clip_id !== clipId).concat({ ...this.lineage.left }, { ...this.lineage.right });
      return this.mutated(route, key, { original_clip_id: clipId, left_clip_id: leftId, right_clip_id: rightId });
    }
    if (operation === "restore" && method === "POST") {
      const restored = this.tombstones.get(clipId);
      if (!restored) return this.error(route, 409, "CLIP_RESTORE_CONFLICT");
      this.clips.push({ ...restored });
      this.tombstones.delete(clipId);
      this.history.barrier();
      return this.mutated(route, key, { clip_id: clipId });
    }
    if (!operation && method === "DELETE") {
      const removed = this.requiredClip(clipId);
      this.clips = this.clips.filter((item) => item.clip_id !== clipId);
      this.tombstones.set(clipId, { ...removed });
      this.history.barrier();
      return this.mutated(route, key, { clip_id: clipId });
    }
    return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
  }

  private snapshot() {
    return {
      working_composition_id: workingId,
      project_id: projectId,
      base_composition_snapshot_id: snapshotId,
      revision: this.revision,
      mix_settings: {},
      tracks: [{ track_id: trackId, track_type: "audio", name: "Gain Track", track_order: 0 }],
      clips: this.clips.map((item) => ({ ...item })),
      timeline_duration: "8.000",
    };
  }

  private requiredClip(id: string) {
    const value = this.clips.find((item) => item.clip_id === id);
    if (!value) throw new Error(`missing clip ${id}`);
    return value;
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
    const mediaTimes = new WeakMap<HTMLMediaElement, number>();
    localStorage.setItem("doha-studio-settings", JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }));
    Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
      configurable: true,
      get(this: HTMLMediaElement) { return mediaTimes.get(this) ?? 0; },
      set(this: HTMLMediaElement, value: number) { mediaTimes.set(this, Number(value)); },
    });
    Object.defineProperty(HTMLMediaElement.prototype, "readyState", { configurable: true, get: () => 4 });
    HTMLMediaElement.prototype.play = async function () { this.dispatchEvent(new Event("play")); };
    HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
  });
});

test("Clip Gain absolute mutation, history, identity, stale Preview와 fail-closed UX", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const backend = new GainBackend();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: /Clip 22222222 선택 및 이동/ }).click();
  const slider = page.getByRole("slider", { name: "Clip gain" });
  const input = page.getByLabel("Clip gain exact value");
  await expect(slider).toBeVisible();
  await expect(slider).toHaveAttribute("min", "-24");
  await expect(slider).toHaveAttribute("max", "24");
  await expect(slider).toHaveAttribute("step", "0.01");
  await expect(input).toHaveValue("0.00");
  await expect(page.getByRole("button", { name: "Clip gain을 0 dB로 재설정" })).toBeDisabled();

  if (testInfo.project.name !== "chromium") {
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect.poll(() => backend.gainRequests.length).toBe(1);
    await expect(input).toHaveValue("0.01");
    const viewport = page.viewportSize();
    const gainControl = page.locator(".working-clip-gain");
    await expect(gainControl).toBeVisible();
    await expect.poll(async () => {
      const box = await gainControl.boundingBox();
      return box !== null && box.x + box.width <= viewport!.width + 1;
    }).toBe(true);
    expect(pageErrors).toEqual([]);
    return;
  }

  await page.getByRole("button", { name: "Working Preview 만들기" }).click();
  await expect(page.getByRole("button", { name: "Working Preview revision 2 재생" })).toBeVisible();
  const audio = page.locator("audio");
  await expect(audio).toHaveCount(1);
  const audioSourceBefore = await audio.getAttribute("src");
  const resolverBefore = backend.resolverRequests;
  const contentBefore = backend.contentRequests;
  backend.loseNextGainResponse = true;
  await slider.evaluate((element: HTMLInputElement) => {
    for (const value of ["1.00", "2.00", "3.00"]) {
      element.value = value;
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  expect(backend.gainRequests).toHaveLength(0);
  await slider.dispatchEvent("pointerup", { pointerId: 1 });
  await expect.poll(() => backend.gainRequests.length).toBe(2);
  expect(backend.gainRequests[0].key).toBe(backend.gainRequests[1].key);
  expect(backend.gainRequests[0]).toMatchObject({ clipId: originalId, body: { expected_revision: 2, gain_db: 3 } });
  await expect(input).toHaveValue("3.00");
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
  expect(backend.previewPosts).toBe(1);
  expect(backend.resolverRequests).toBe(resolverBefore);
  expect(backend.contentRequests).toBe(contentBefore);
  await expect(audio).toHaveCount(1);
  expect(await audio.getAttribute("src")).toBe(audioSourceBefore);

  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expect(input).toHaveValue("0.00");
  expect(backend.historyUndoPosts).toBe(1);
  expect(backend.gainRequests).toHaveLength(2);
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
  await expect(input).toHaveValue("3.00");
  expect(backend.historyRedoPosts).toBe(1);
  expect(backend.gainRequests).toHaveLength(2);

  await page.getByRole("button", { name: "Clip gain을 0 dB로 재설정" }).click();
  await expect(input).toHaveValue("0.00");
  await exactGain(page, "-24");
  await expect(input).toHaveValue("-24.00");
  await exactGain(page, "24");
  await expect(input).toHaveValue("24.00");
  const requestsBeforeInvalid = backend.gainRequests.length;
  await exactGain(page, "24.001");
  await expect(page.getByText(/0.01 dB 단위로 입력/)).toBeVisible();
  expect(backend.gainRequests).toHaveLength(requestsBeforeInvalid);

  backend.rejectNextGain = true;
  await exactGain(page, "-3.25");
  await expect(page.locator(".alert-error")).toContainText(/-24.00 dB부터 \+24.00 dB/);
  await expect(input).toHaveValue("24.00");

  backend.conflictNextGain = "-2.00";
  const beforeConflict = backend.gainRequests.length;
  await exactGain(page, "1.00");
  await expect(input).toHaveValue("-2.00");
  expect(backend.gainRequests).toHaveLength(beforeConflict + 1);
  await expect(page.getByText(/최신 편집 상태와 Undo\/Redo 기록/)).toBeVisible();

  await page.getByLabel("Copy 대상 Track").selectOption(trackId);
  await page.getByLabel("Copy Timeline start").fill("3");
  await page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }).click();
  await page.getByRole("button", { name: /Clip 33333333 선택 및 이동/ }).click();
  await expect(page.getByLabel("Clip gain exact value")).toHaveValue("-2.00");
  expect(backend.clips.find((item) => item.clip_id === copiedId)?.gain_db).toBe("-2.00");
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();

  await setPlayhead(page, 4);
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  expect(backend.clips.find((item) => item.clip_id === leftId)?.gain_db).toBe("-2.00");
  expect(backend.clips.find((item) => item.clip_id === rightId)?.gain_db).toBe("-2.00");
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(backend.clips.find((item) => item.clip_id === rightId)?.gain_db).toBe("-2.00");

  await page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ }).click();
  await page.getByRole("button", { name: /Clip 삭제/ }).click();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  await page.getByRole("button", { name: /Clip 55555555 선택 및 이동/ }).click();

  await exactGain(page, "2.50");
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByText(/Undo\/Redo 기록이 초기화/)).toBeVisible();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(backend.committedClips.find((item) => item.clip_id === rightId)?.gain_db).toBe("2.50");
  await exactGain(page, "4.00");
  await page.getByRole("button", { name: "현재 Snapshot Checkout" }).click();
  await expect(page.getByLabel("Clip gain exact value")).toHaveValue("2.50");
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(backend.previewPosts).toBe(1);
  expect(pageErrors).toEqual([]);
});

async function exactGain(page: Page, value: string) {
  const input = page.getByLabel("Clip gain exact value");
  await input.fill(value);
  await input.blur();
}

async function setPlayhead(page: Page, seconds: number) {
  const audio = page.locator("audio");
  await expect(audio).toHaveCount(1);
  await audio.evaluate((element: HTMLAudioElement, next) => {
    element.currentTime = next;
    element.dispatchEvent(new Event("timeupdate"));
  }, seconds);
  await expect(page.getByRole("button", { name: /Playhead에서 Split/ })).toBeEnabled();
}

function clip(id: string, timeline: string, sourceIn: string, sourceOut: string, gainDb: string): Clip {
  return {
    clip_id: id, track_id: trackId, source_asset_version_id: versionId,
    timeline_start: timeline, source_in: sourceIn, source_out: sourceOut,
    source_duration: "8.000", timeline_duration: (Number(sourceOut) - Number(sourceIn)).toFixed(3), loop_enabled: false, loop_phase: "0",
    gain_db: gainDb, fade_in: "0", fade_out: "0", split_from_clip_id: null,
  };
}

function project() {
  return { id: projectId, title: "Clip Gain E2E", description: "stateful Gain fixture", created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z", job_count: 0, jobs: [] };
}

function composition() {
  return {
    state: "ready",
    project: { project_id: projectId, workspace_id: "workspace-1", title: "Clip Gain E2E", lifecycle_status: "active", created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z" },
    selection: { selected_snapshot_id: snapshotId, resolved_snapshot_id: snapshotId, resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: snapshotId, project_id: projectId, snapshot_version: 1, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-08-31T00:00:00Z" },
    items: [{ snapshot_item_id: "item-gain", item_role: "mix", sort_order: 0, asset_version: { asset_version_id: versionId, asset_id: "asset-gain", version_number: 1, version_origin: "fixture", parent_asset_version_id: null, processing_chain_id: null, provider_id: null, model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-31T00:00:00Z" }, artifacts: [{ artifact_id: artifactId, asset_version_id: versionId, artifact_kind: "audio", media_type: "audio/wav", size_bytes: wav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture", producer_type: "test", producer_id: null, run_id: null, retention_status: "active", created_at: "2026-08-31T00:00:00Z", content_url: `/api/v1/artifacts/${artifactId}/content`, download_url: null }] }],
    track_projections: [], section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {}, lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function previewJob() {
  return {
    job_id: "job-gain-preview", project_id: projectId, composition_snapshot_id: null,
    job_type: "working_preview_render", status: "succeeded", provider_id: null, model_manifest_id: null,
    progress_percent: 100, stage: null, retry_of_job_id: null, inputs: [],
    outputs: [{ output_role: "working_preview", output_order: 0, asset_version_id: null, artifact_id: "artifact-preview" }],
    model_usages: [], error_code: null, error_message: null, error_retryable: null, error_details_id: null,
    created_at: "2026-08-31T00:00:00Z", started_at: null, completed_at: "2026-08-31T00:00:01Z",
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
