import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const IDS = {
  working: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  track: "11111111-1111-4111-8111-111111111111",
  first: "22222222-2222-4222-8222-222222222222",
  second: "33333333-3333-4333-8333-333333333333",
  left: "44444444-4444-4444-8444-444444444444",
  right: "55555555-5555-4555-8555-555555555555",
  assetVersion: "66666666-6666-4666-8666-666666666666",
  artifact: "77777777-7777-4777-8777-777777777777",
} as const;

const projectId = "project-waveform";
const snapshotId = "snapshot-waveform";
const workingBase = `/backend/api/v1/projects/${projectId}/working-composition`;
const resolverPath = `/backend/api/v1/projects/${projectId}/asset-versions/${IDS.assetVersion}/media-source`;
const contentPath = `/backend/api/v1/artifacts/${IDS.artifact}/content`;
const segmentedWav = createSegmentedWav();

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

class WaveformBackend {
  revision = 4;
  mode: "ready" | "resolver-fail" | "decode-fail" = "ready";
  resolverRequests = 0;
  contentRequests = 0;
  mutationRequests = 0;
  clips: Clip[] = [
    clip(IDS.first, "0.000", "0.000", "4.000"),
    clip(IDS.second, "5.000", "4.000", "8.000"),
  ];
  tombstones = new Map<string, Clip>();
  lineage: { original: Clip; left: Clip; right: Clip } | null = null;
  history = new PersistentHistoryFixture(IDS.working, {
    get: () => this.revision,
    increment: () => ++this.revision,
  });

  async install(page: Page) {
    await page.route("**/backend/**", (route) => this.handle(route));
  }

  private async handle(route: Route) {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : {};

    if (path === "/backend/health") return this.data(route, { status: "ok" });
    if (path === `/backend/api/v1/projects/${projectId}`) {
      return this.data(route, { data: {
        project_id: projectId,
        title: "Track Clip Waveform E2E",
        description: "repository-owned synthetic media",
        created_at: "2026-08-28T00:00:00Z",
        updated_at: "2026-08-28T00:00:00Z",
        job_count: 0,
        jobs: [],
      } });
    }
    if (path === `/backend/api/v1/projects/${projectId}/composition`) {
      return this.data(route, { data: compositionWorkspace() });
    }
    if (path === "/backend/api/v1/artifacts/artifact-mix/content") {
      return route.fulfill({
        status: 200,
        headers: audioHeaders(segmentedWav.length),
        body: segmentedWav,
      });
    }
    if (path === resolverPath && method === "GET") {
      this.resolverRequests += 1;
      if (this.mode === "resolver-fail") return this.error(route, 409, "SOURCE_ASSET_UNAVAILABLE");
      const size = this.mode === "decode-fail" ? 8 : segmentedWav.length;
      return this.data(route, { data: {
        asset_version_id: IDS.assetVersion,
        artifact_id: IDS.artifact,
        media_type: "audio/wav",
        size_bytes: size,
        artifact_checksum: "a".repeat(64),
        duration_seconds: "8",
        content_url: `/api/v1/artifacts/${IDS.artifact}/content`,
      } });
    }
    if (path === contentPath && method === "GET") {
      this.contentRequests += 1;
      const bytes = this.mode === "decode-fail" ? Buffer.from("not-wave") : segmentedWav;
      return route.fulfill({ status: 200, headers: audioHeaders(bytes.length), body: bytes });
    }
    if (!path.startsWith(workingBase)) {
      return route.fulfill({ status: 404, json: { error: { code: "TEST_ROUTE_NOT_FOUND", message: path } } });
    }
    const relative = path.slice(workingBase.length);
    if (method === "GET" && relative === "") return this.data(route, { data: this.snapshot() });
    if (method === "GET" && relative === "/history") return this.data(route, { data: this.history.projection() });
    if (method === "POST" && relative === "/history/undo") return this.history.mutate(route, "undo");
    if (method === "POST" && relative === "/history/redo") return this.history.mutate(route, "redo");
    if (Number(body.expected_revision) !== this.revision) {
      return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    }
    this.mutationRequests += 1;

    const match = relative.match(/^\/clips\/([^/]+)(?:\/(move|trim-start|trim-end|split|restore|unsplit|resplit))?$/);
    if (!match) return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
    const clipId = decodeURIComponent(match[1]);
    const operation = match[2];
    if (operation === "move" && method === "PATCH") {
      const current = this.requiredClip(clipId);
      const before = { ...current };
      current.timeline_start = decimal(body.timeline_start);
      this.appendHistory(clipId, before, { ...current });
      return this.mutated(route, { clip_id: clipId });
    }
    if (operation === "trim-start" && method === "PATCH") {
      const current = this.requiredClip(clipId);
      const before = { ...current };
      current.timeline_start = decimal(body.timeline_start);
      current.source_in = decimal(body.source_in);
      current.timeline_duration = decimal(Number(current.source_out) - Number(current.source_in));
      this.appendHistory(clipId, before, { ...current });
      return this.mutated(route, { clip_id: clipId });
    }
    if (operation === "trim-end" && method === "PATCH") {
      const current = this.requiredClip(clipId);
      const before = { ...current };
      current.source_out = decimal(body.source_out);
      current.timeline_duration = decimal(Number(current.source_out) - Number(current.source_in));
      this.appendHistory(clipId, before, { ...current });
      return this.mutated(route, { clip_id: clipId });
    }
    if (operation === "split" && method === "POST") {
      const original = { ...this.requiredClip(clipId) };
      const splitAt = Number(body.split_at);
      const left = { ...original, clip_id: IDS.left, source_out: decimal(splitAt), timeline_duration: decimal(splitAt - Number(original.source_in)), split_from_clip_id: original.clip_id };
      const right = {
        ...original,
        clip_id: IDS.right,
        timeline_start: decimal(Number(original.timeline_start) + splitAt - Number(original.source_in)),
        source_in: decimal(splitAt),
        timeline_duration: decimal(Number(original.source_out) - splitAt),
        split_from_clip_id: original.clip_id,
      };
      this.clips = this.clips.filter((item) => item.clip_id !== clipId).concat(left, right);
      this.lineage = { original, left: { ...left }, right: { ...right } };
      this.history.barrier();
      return this.mutated(route, { original_clip_id: clipId, left_clip_id: IDS.left, right_clip_id: IDS.right });
    }
    if (operation === "unsplit" && method === "POST" && this.lineage) {
      this.clips = this.clips
        .filter((item) => ![this.lineage!.left.clip_id, this.lineage!.right.clip_id].includes(item.clip_id))
        .concat({ ...this.lineage.original });
      this.history.barrier();
      return this.mutated(route, { original_clip_id: clipId, left_clip_id: IDS.left, right_clip_id: IDS.right });
    }
    if (operation === "resplit" && method === "POST" && this.lineage) {
      this.clips = this.clips
        .filter((item) => item.clip_id !== clipId)
        .concat({ ...this.lineage.left }, { ...this.lineage.right });
      this.history.barrier();
      return this.mutated(route, { original_clip_id: clipId, left_clip_id: IDS.left, right_clip_id: IDS.right });
    }
    if (operation === "restore" && method === "POST") {
      const restored = this.tombstones.get(clipId);
      if (!restored) return this.error(route, 409, "CLIP_RESTORE_CONFLICT");
      this.clips.push({ ...restored });
      this.tombstones.delete(clipId);
      this.history.barrier();
      return this.mutated(route, { clip_id: clipId });
    }
    if (!operation && method === "DELETE") {
      const removed = { ...this.requiredClip(clipId) };
      this.clips = this.clips.filter((item) => item.clip_id !== clipId);
      this.tombstones.set(clipId, removed);
      this.history.barrier();
      return this.mutated(route, { clip_id: clipId });
    }
    return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
  }

  private snapshot() {
    return {
      working_composition_id: IDS.working,
      project_id: projectId,
      base_composition_snapshot_id: snapshotId,
      revision: this.revision,
      mix_settings: {},
      tracks: [{ track_id: IDS.track, track_type: "audio", name: "Waveform Track", track_order: 0 }],
      clips: this.clips.map((item) => ({ ...item })),
      timeline_duration: decimal(this.clips.reduce(
        (maximum, item) => Math.max(maximum, Number(item.timeline_start) + Number(item.source_out) - Number(item.source_in)),
        0,
      )),
    };
  }

  private requiredClip(id: string) {
    const found = this.clips.find((item) => item.clip_id === id);
    if (!found) throw new Error(`Missing Clip ${id}`);
    return found;
  }

  private mutated(route: Route, result: Record<string, unknown>) {
    this.revision += 1;
    return this.data(route, { data: { ...result, completed_revision: this.revision, replayed: false } });
  }

  private appendHistory(clipId: string, before: Clip, after: Clip) {
    this.history.append({
      clipId,
      applyBefore: () => Object.assign(this.requiredClip(clipId), before),
      applyAfter: () => Object.assign(this.requiredClip(clipId), after),
    });
  }

  private data(route: Route, json: unknown) { return route.fulfill({ status: 200, json }); }
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
    HTMLMediaElement.prototype.play = async function () { this.dispatchEvent(new Event("play")); };
    HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
  });
});

test("exact AssetVersion Clip waveform projection과 editing history를 실제 media route로 검증한다", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const backend = new WaveformBackend();
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await backend.install(page);

  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("heading", { name: "Track Clip Waveform E2E" })).toBeVisible();
  const firstWaveform = waveform(page, IDS.first);
  const secondWaveform = waveform(page, IDS.second);
  await expect(firstWaveform).toHaveAttribute("data-waveform-status", "ready");
  await expect(secondWaveform).toHaveAttribute("data-waveform-status", "ready");
  expect(await firstWaveform.locator("svg").getAttribute("aria-hidden")).toBeNull();
  await expect(firstWaveform).toHaveAttribute("aria-hidden", "true");
  expect(await signature(firstWaveform)).not.toBe(await signature(secondWaveform));
  expect(backend.resolverRequests).toBe(1);
  expect(backend.contentRequests).toBe(1);

  if (testInfo.project.name !== "chromium") {
    await responsiveWaveformSmoke(page, backend);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
    return;
  }

  const secondClip = clipButton(page, IDS.second);
  const moveSignature = await signature(secondWaveform);
  const initialSourceWindow = await sourceWindow(secondWaveform);
  const moveLeft = await secondClip.evaluate((element) => (element as HTMLElement).style.left);
  await drag(page, secondClip, 64);
  await expectRevision(page, 5);
  expect(await secondClip.evaluate((element) => (element as HTMLElement).style.left)).not.toBe(moveLeft);
  expect(await signature(waveform(page, IDS.second))).toBe(moveSignature);

  const startHandle = page.getByLabel(`Clip ${IDS.second.slice(0, 8)} 시작 Trim`);
  await drag(page, startHandle, 64);
  await expectRevision(page, 6);
  const afterStartWaveform = waveform(page, IDS.second);
  await expect(afterStartWaveform).not.toHaveAttribute("data-source-window", initialSourceWindow);
  const afterStartSourceWindow = await sourceWindow(afterStartWaveform);
  const afterStartSignature = await signature(afterStartWaveform);
  expect(afterStartSignature).not.toBe(moveSignature);

  const endHandle = page.getByLabel(`Clip ${IDS.second.slice(0, 8)} 끝 Trim`);
  await drag(page, endHandle, -64);
  await expectRevision(page, 7);
  const afterEndWaveform = waveform(page, IDS.second);
  await expect(afterEndWaveform).not.toHaveAttribute("data-source-window", afterStartSourceWindow);
  const afterEndSourceWindow = await sourceWindow(afterEndWaveform);
  const afterEndSignature = await signature(afterEndWaveform);
  expect(afterEndSignature).not.toBe(afterStartSignature);
  await undo(page);
  await expectRevision(page, 8);
  await expect(waveform(page, IDS.second)).toHaveAttribute("data-source-window", afterStartSourceWindow);
  expect(await signature(waveform(page, IDS.second))).toBe(afterStartSignature);
  await redo(page);
  await expectRevision(page, 9);
  await expect(waveform(page, IDS.second)).toHaveAttribute("data-source-window", afterEndSourceWindow);
  expect(await signature(waveform(page, IDS.second))).toBe(afterEndSignature);

  await clipButton(page, IDS.first).click();
  await setPlayhead(page, 2);
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  await expectRevision(page, 10);
  await expect(waveform(page, IDS.first)).toHaveCount(0);
  await expect(waveform(page, IDS.left)).toHaveAttribute("data-waveform-status", "ready");
  await expect(waveform(page, IDS.right)).toHaveAttribute("data-waveform-status", "ready");
  const leftSignature = await signature(waveform(page, IDS.left));
  const rightSignature = await signature(waveform(page, IDS.right));
  expect(leftSignature).not.toBe(rightSignature);

  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(await signature(waveform(page, IDS.left))).toBe(leftSignature);
  expect(await signature(waveform(page, IDS.right))).toBe(rightSignature);

  await clipButton(page, IDS.left).click();
  await page.getByRole("button", { name: /Clip 삭제/ }).click();
  await expectRevision(page, 11);
  await expect(waveform(page, IDS.left)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();

  const fetchesBeforeGeometryOnlyChanges = backend.contentRequests;
  const resolverBeforeGeometryOnlyChanges = backend.resolverRequests;
  const right = clipButton(page, IDS.right);
  const widthBeforeZoom = await right.evaluate((element) => (element as HTMLElement).style.width);
  await page.getByRole("button", { name: "Clip Timeline 확대" }).click();
  expect(await right.evaluate((element) => (element as HTMLElement).style.width)).not.toBe(widthBeforeZoom);
  await page.getByRole("button", { name: "Clip Timeline 축소" }).click();
  const scroll = page.getByTestId("working-clip-scroll");
  const clipLeftBeforeScroll = await right.evaluate((element) => (element as HTMLElement).offsetLeft);
  const playheadLeftBeforeScroll = await page.locator(".working-clip-playhead").evaluate((element) => (element as HTMLElement).offsetLeft);
  await scroll.evaluate((element) => { element.scrollLeft = 96; element.dispatchEvent(new Event("scroll")); });
  expect(await right.evaluate((element) => (element as HTMLElement).offsetLeft)).toBe(clipLeftBeforeScroll);
  expect(await page.locator(".working-clip-playhead").evaluate((element) => (element as HTMLElement).offsetLeft)).toBe(playheadLeftBeforeScroll);
  expect(backend.contentRequests).toBe(fetchesBeforeGeometryOnlyChanges);
  expect(backend.resolverRequests).toBe(resolverBeforeGeometryOnlyChanges);

  const requestsAfterReadySession = { resolver: backend.resolverRequests, content: backend.contentRequests };
  backend.mode = "resolver-fail";
  await page.reload();
  const failedResolverWaveform = waveform(page, IDS.second);
  await expect(failedResolverWaveform).toHaveAttribute("data-waveform-status", "unavailable");
  expect(backend.resolverRequests - requestsAfterReadySession.resolver).toBe(1);
  expect(backend.contentRequests - requestsAfterReadySession.content).toBe(0);
  await expect(clipButton(page, IDS.second)).toBeEnabled();
  const mutationBeforeFailureMove = backend.mutationRequests;
  await drag(page, clipButton(page, IDS.second), 32);
  expect(backend.mutationRequests).toBe(mutationBeforeFailureMove + 1);

  const requestsAfterResolverFailure = { resolver: backend.resolverRequests, content: backend.contentRequests };
  backend.mode = "decode-fail";
  await page.reload();
  await expect(waveform(page, IDS.second)).toHaveAttribute("data-waveform-status", "unavailable");
  expect(backend.resolverRequests - requestsAfterResolverFailure.resolver).toBe(1);
  expect(backend.contentRequests - requestsAfterResolverFailure.content).toBe(1);
  await expect(clipButton(page, IDS.second)).toBeEnabled();

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((message) => !/409 \(Conflict\)/.test(message))).toEqual([]);
});

async function responsiveWaveformSmoke(page: Page, backend: WaveformBackend) {
  const first = clipButton(page, IDS.first);
  await first.click();
  await expect(first).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Clip Timeline 확대" })).toBeVisible();
  await setPlayhead(page, 2);
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  await expectRevision(page, 5);
  await expect(waveform(page, IDS.left)).toHaveAttribute("data-waveform-status", "ready");
  await clipButton(page, IDS.left).click();
  await page.getByRole("button", { name: /Clip 삭제/ }).click();
  await expectRevision(page, 6);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(backend.resolverRequests).toBe(1);
  expect(backend.contentRequests).toBe(1);
}

function waveform(page: Page, id: string) { return page.getByTestId(`clip-waveform-${id}`); }
function clipButton(page: Page, id: string) { return page.getByRole("button", { name: new RegExp(`Clip ${id.slice(0, 8)} 선택 및 이동`) }); }
async function signature(locator: Locator) { return locator.getAttribute("data-waveform-signature"); }
async function sourceWindow(locator: Locator) {
  const value = await locator.getAttribute("data-source-window");
  if (!value) throw new Error("Waveform source window is unavailable");
  return value;
}
async function undo(page: Page) { await page.getByRole("button", { name: "편집 실행 취소" }).click(); }
async function redo(page: Page) { await page.getByRole("button", { name: "편집 다시 실행" }).click(); }
async function expectRevision(page: Page, revision: number) { await expect(page.getByText(new RegExp(`revision ${revision} ·`))).toBeVisible(); }

async function drag(page: Page, locator: Locator, deltaX: number) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("Drag target has no bounding box");
  const x = box.x + Math.max(2, Math.min(box.width / 2, box.width - 2));
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + deltaX / 2, y);
  await page.mouse.move(x + deltaX, y);
  await page.mouse.up();
}

async function setPlayhead(page: Page, seconds: number) {
  const audio = page.locator("audio");
  await expect(audio).toHaveCount(1);
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  await audio.evaluate((element: HTMLAudioElement, next) => {
    element.currentTime = next;
    element.dispatchEvent(new Event("timeupdate"));
  }, seconds);
  await expect.poll(async () => Number(await page.getByRole("slider", { name: "Timeline Playhead 재생 위치" }).getAttribute("aria-valuenow"))).toBeCloseTo(seconds, 1);
}

function clip(id: string, timelineStart: string, sourceIn: string, sourceOut: string): Clip {
  return {
    clip_id: id,
    track_id: IDS.track,
    source_asset_version_id: IDS.assetVersion,
    timeline_start: timelineStart,
    source_in: sourceIn,
    source_out: sourceOut,
    source_duration: "8.000",
    timeline_duration: decimal(Number(sourceOut) - Number(sourceIn)),
    loop_enabled: false,
    loop_phase: "0",
    gain_db: "0.00",
    fade_in: "0",
    fade_out: "0",
    split_from_clip_id: null,
  };
}

function compositionWorkspace() {
  return {
    state: "ready",
    project: { project_id: projectId, workspace_id: "workspace-1", title: "Track Clip Waveform E2E", lifecycle_status: "active", created_at: "2026-08-28T00:00:00Z", updated_at: "2026-08-28T00:00:00Z" },
    selection: { selected_snapshot_id: snapshotId, resolved_snapshot_id: snapshotId, resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: snapshotId, project_id: projectId, snapshot_version: 1, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-08-28T00:00:00Z" },
    items: [{
      snapshot_item_id: "snapshot-item-1", item_role: "mix", sort_order: 0,
      asset_version: { asset_version_id: IDS.assetVersion, asset_id: "asset-1", version_number: 1, version_origin: "provider_output", parent_asset_version_id: null, processing_chain_id: null, provider_id: "fixture", model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-28T00:00:00Z" },
      artifacts: [{ artifact_id: "artifact-mix", asset_version_id: IDS.assetVersion, artifact_kind: "audio", media_type: "audio/wav", size_bytes: segmentedWav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture-checksum", producer_type: "test", producer_id: "fixture", run_id: null, retention_status: "active", created_at: "2026-08-28T00:00:00Z", content_url: "/api/v1/artifacts/artifact-mix/content", download_url: null }],
    }],
    track_projections: [{ projection_id: "snapshot-item-1", identity_scope: "snapshot", snapshot_item_id: "snapshot-item-1", item_role: "mix", sort_order: 0, asset_id: "asset-1", asset_version_id: IDS.assetVersion }],
    section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {},
    lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function audioHeaders(size: number) {
  return { "Content-Type": "audio/wav", "Content-Length": String(size) };
}

function decimal(value: unknown) { return Number(value).toFixed(3); }

function createSegmentedWav() {
  const sampleRate = 8_000;
  const durationSeconds = 8;
  const dataSize = sampleRate * durationSeconds;
  const wav = Buffer.alloc(44 + dataSize);
  wav.write("RIFF", 0, "ascii");
  wav.writeUInt32LE(36 + dataSize, 4);
  wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii");
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate, 28);
  wav.writeUInt16LE(1, 32);
  wav.writeUInt16LE(8, 34);
  wav.write("data", 36, "ascii");
  wav.writeUInt32LE(dataSize, 40);
  const amplitudes = [12, 36, 96, 60];
  const frequencies = [110, 220, 440, 880];
  for (let index = 0; index < dataSize; index += 1) {
    const segment = Math.min(3, Math.floor(index / (sampleRate * 2)));
    const sample = 128 + Math.round(
      Math.sin((2 * Math.PI * frequencies[segment] * index) / sampleRate) * amplitudes[segment],
    );
    wav[44 + index] = Math.max(0, Math.min(255, sample));
  }
  return wav;
}
