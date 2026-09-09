import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const IDS = {
  working: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  track1: "11111111-1111-4111-8111-111111111111",
  track2: "22222222-2222-4222-8222-222222222222",
  track3: "33333333-3333-4333-8333-333333333333",
  original: "44444444-4444-4444-8444-444444444444",
  independent: "55555555-5555-4555-8555-555555555555",
  left: "66666666-6666-4666-8666-666666666666",
  right: "77777777-7777-4777-8777-777777777777",
  assetVersion: "88888888-8888-4888-8888-888888888888",
} as const;

const projectId = "project-1";
const snapshotId = "snapshot-1";
const workingBase = `/backend/api/v1/projects/${projectId}/working-composition`;
const silentWav = createSilentWav(30);

type Track = { track_id: string; track_type: string; name: string; track_order: number; gain_db?: string; pan?: string; muted?: boolean; solo?: boolean };
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

type RequestRecord = {
  method: string;
  path: string;
  body: Record<string, unknown>;
  idempotencyKey: string | null;
};

class StatefulWorkingBackend {
  workingExists = false;
  revision = 0;
  tracks: Track[] = [];
  clips: Clip[] = [];
  masterGainDb = "0.00";
  tombstonedTracks = new Map<string, Track>();
  tombstonedClips = new Map<string, Clip>();
  splitLineage = new Map<string, { original: Clip; left: Clip; right: Clip }>();
  completions = new Map<string, Record<string, unknown>>();
  requests: RequestRecord[] = [];
  createTrackIndex = 0;
  createClipIndex = 0;
  loseNextClipCreateResponse = false;
  advanceBeforeNextMutation = false;
  expectedResponseLosses = 0;
  history = new PersistentHistoryFixture(IDS.working, {
    get: () => this.revision,
    increment: () => ++this.revision,
  });

  async install(page: Page) {
    await page.route("**/backend/**", (route) => this.handle(route));
  }

  count(suffix: string, method?: string) {
    return this.requests.filter((item) => item.path.endsWith(suffix) && (!method || item.method === method)).length;
  }

  requiredTrackForTest(id: string) {
    return this.requiredTrack(id);
  }

  requiredClipForTest(id: string) {
    return this.requiredClip(id);
  }

  private async handle(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : {};
    const idempotencyKey = request.headers()["idempotency-key"] ?? null;

    if (path === "/backend/health") return this.data(route, { status: "ok" });
    if (path === `/backend/api/v1/projects/${projectId}`) {
      return this.data(route, { data: {
        project_id: projectId,
        title: "WorkingComposition E2E",
        description: "isolated Playwright fixture",
        created_at: "2026-08-26T00:00:00Z",
        updated_at: "2026-08-26T00:00:00Z",
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
        contentType: "audio/wav",
        body: silentWav,
      });
    }
    if (!path.startsWith(workingBase)) return route.fulfill({ status: 404, json: { error: { code: "TEST_ROUTE_NOT_FOUND", message: path } } });

    const relative = path.slice(workingBase.length);
    this.requests.push({ method, path, body, idempotencyKey });

    if (method === "GET" && relative === "") {
      if (!this.workingExists) return this.error(route, 404, "WORKING_COMPOSITION_NOT_FOUND");
      return this.data(route, { data: this.snapshot() });
    }
    if (method === "GET" && relative === "/history") {
      return this.data(route, { data: this.history.projection() });
    }
    if (method === "POST" && relative === "/history/undo") return this.history.mutate(route, "undo");
    if (method === "POST" && relative === "/history/redo") return this.history.mutate(route, "redo");

    if (idempotencyKey && this.completions.has(idempotencyKey)) {
      return this.data(route, { data: { ...this.completions.get(idempotencyKey), replayed: true } });
    }

    if (relative === "/initialize" && method === "POST") {
      this.workingExists = true;
      this.revision = 0;
      this.history.barrier();
      const result = { working_composition_id: IDS.working, completed_revision: 0, replayed: false };
      this.remember(idempotencyKey, result);
      return this.data(route, { data: result });
    }

    if (!this.workingExists) return this.error(route, 404, "WORKING_COMPOSITION_NOT_FOUND");
    if (this.advanceBeforeNextMutation) {
      this.advanceBeforeNextMutation = false;
      this.revision += 1;
    }
    if (body.expected_revision !== this.revision) {
      return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    }

    if (relative === "/checkout" && method === "POST") {
      this.tracks = [];
      this.clips = [];
      this.history.barrier();
      return this.mutated(route, idempotencyKey, {
        working_composition_id: IDS.working,
        base_composition_snapshot_id: snapshotId,
      });
    }

    if (relative === "/commit" && method === "POST") {
      this.history.barrier();
      return this.mutated(route, idempotencyKey, {
        working_composition_id: IDS.working,
        composition_snapshot_id: "snapshot-persistent-history",
      });
    }

    if (relative === "/tracks" && method === "POST") {
      const trackId = [IDS.track1, IDS.track2, IDS.track3][this.createTrackIndex++] ?? crypto.randomUUID();
      this.tracks.push({ track_id: trackId, track_type: "audio", name: String(body.name), track_order: this.tracks.length, gain_db: "0.00", pan: "0.00", muted: false, solo: false });
      this.history.barrier();
      return this.mutated(route, idempotencyKey, { track_id: trackId });
    }
    if (relative === "/tracks/reorder" && method === "PATCH") {
      const ordered = body.ordered_track_ids as string[];
      this.tracks = ordered.map((id, index) => ({ ...this.requiredTrack(id), track_order: index }));
      this.history.barrier();
      return this.mutated(route, idempotencyKey, { working_composition_id: IDS.working });
    }

    const mixerMatch = relative.match(/^\/tracks\/([^/]+)\/mixer$/);
    if (mixerMatch && method === "PATCH") {
      const trackId = decodeURIComponent(mixerMatch[1]);
      const track = this.requiredTrack(trackId);
      const before = { gain_db: track.gain_db, pan: track.pan, muted: track.muted, solo: track.solo };
      const after = { gain_db: Number(body.gain_db).toFixed(2), pan: Number(body.pan).toFixed(2), muted: Boolean(body.muted), solo: Boolean(body.solo) };
      Object.assign(track, after);
      this.history.append({
        clipId: IDS.original,
        applyBefore: () => Object.assign(this.requiredTrack(trackId), before),
        applyAfter: () => Object.assign(this.requiredTrack(trackId), after),
      });
      return this.mutated(route, idempotencyKey, { track_id: trackId });
    }
    if (relative === "/master-gain" && method === "PATCH") {
      const before = this.masterGainDb;
      const after = Number(body.master_gain_db).toFixed(2);
      this.masterGainDb = after;
      this.history.append({ clipId: IDS.original, applyBefore: () => { this.masterGainDb = before; }, applyAfter: () => { this.masterGainDb = after; } });
      return this.mutated(route, idempotencyKey, { working_composition_id: IDS.working });
    }

    const trackMatch = relative.match(/^\/tracks\/([^/]+)(\/restore)?$/);
    if (trackMatch) {
      const trackId = decodeURIComponent(trackMatch[1]);
      if (trackMatch[2] && method === "POST") {
        const restored = this.tombstonedTracks.get(trackId);
        if (!restored) return this.error(route, 409, "TRACK_RESTORE_CONFLICT");
        const target = Number(body.target_track_order);
        this.tracks.splice(target, 0, { ...restored });
        this.reindexTracks();
        this.tombstonedTracks.delete(trackId);
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { track_id: trackId });
      }
      if (method === "PATCH") {
        const track = this.requiredTrack(trackId);
        const before = track.name;
        const after = String(body.name);
        track.name = after;
        this.history.append({
          clipId: IDS.original,
          applyBefore: () => { this.requiredTrack(trackId).name = before; },
          applyAfter: () => { this.requiredTrack(trackId).name = after; },
        });
        return this.mutated(route, idempotencyKey, { track_id: trackId });
      }
      if (method === "DELETE") {
        const index = this.tracks.findIndex((track) => track.track_id === trackId);
        const [removed] = this.tracks.splice(index, 1);
        this.tombstonedTracks.set(trackId, { ...removed });
        this.reindexTracks();
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { track_id: trackId });
      }
    }

    if (relative === "/clips" && method === "POST") {
      const clipId = [IDS.original, IDS.independent][this.createClipIndex++] ?? crypto.randomUUID();
      const clip: Clip = {
        clip_id: clipId,
        track_id: String(body.track_id),
        source_asset_version_id: String(body.source_asset_version_id),
        timeline_start: decimal(body.timeline_start),
        source_in: decimal(body.source_in),
        source_out: decimal(body.source_out),
        source_duration: "30.000",
        timeline_duration: decimal(Number(body.source_out) - Number(body.source_in)),
        loop_enabled: false,
        loop_phase: "0",
        gain_db: "0.00",
        fade_in: "0",
        fade_out: "0",
        split_from_clip_id: null,
      };
      this.clips.push(clip);
      this.history.barrier();
      const loseResponse = this.loseNextClipCreateResponse;
      this.loseNextClipCreateResponse = false;
      return this.mutated(route, idempotencyKey, { clip_id: clipId }, loseResponse);
    }

    const clipMatch = relative.match(/^\/clips\/([^/]+)(?:\/(move|trim-start|trim-end|split|restore|unsplit|resplit))?$/);
    if (clipMatch) {
      const clipId = decodeURIComponent(clipMatch[1]);
      const operation = clipMatch[2];
      if (operation === "move" && method === "PATCH") {
        const clip = this.requiredClip(clipId);
        const before = { ...clip };
        clip.timeline_start = decimal(body.timeline_start);
        const after = { ...clip };
        this.appendClipHistory(clipId, before, after);
        return this.mutated(route, idempotencyKey, { clip_id: clipId });
      }
      if (operation === "trim-start" && method === "PATCH") {
        const clip = this.requiredClip(clipId);
        const before = { ...clip };
        clip.timeline_start = decimal(body.timeline_start);
        clip.source_in = decimal(body.source_in);
        clip.timeline_duration = decimal(Number(clip.source_out) - Number(clip.source_in));
        this.appendClipHistory(clipId, before, { ...clip });
        return this.mutated(route, idempotencyKey, { clip_id: clipId });
      }
      if (operation === "trim-end" && method === "PATCH") {
        const clip = this.requiredClip(clipId);
        const before = { ...clip };
        clip.source_out = decimal(body.source_out);
        clip.timeline_duration = decimal(Number(clip.source_out) - Number(clip.source_in));
        this.appendClipHistory(clipId, before, { ...clip });
        return this.mutated(route, idempotencyKey, { clip_id: clipId });
      }
      if (operation === "split" && method === "POST") {
        const original = { ...this.requiredClip(clipId) };
        const splitAt = Number(body.split_at);
        const left: Clip = { ...original, clip_id: IDS.left, source_out: decimal(splitAt), timeline_duration: decimal(splitAt - Number(original.source_in)), split_from_clip_id: original.clip_id };
        const right: Clip = {
          ...original,
          clip_id: IDS.right,
          timeline_start: decimal(Number(original.timeline_start) + splitAt - Number(original.source_in)),
          source_in: decimal(splitAt),
          timeline_duration: decimal(Number(original.source_out) - splitAt),
          split_from_clip_id: original.clip_id,
        };
        this.clips = this.clips.filter((clip) => clip.clip_id !== clipId);
        this.clips.push(left, right);
        this.splitLineage.set(clipId, { original, left: { ...left }, right: { ...right } });
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { original_clip_id: clipId, left_clip_id: IDS.left, right_clip_id: IDS.right });
      }
      if (operation === "unsplit" && method === "POST") {
        const lineage = this.requiredLineage(clipId);
        expect(body.left_clip_id).toBe(lineage.left.clip_id);
        expect(body.right_clip_id).toBe(lineage.right.clip_id);
        this.clips = this.clips.filter((clip) => ![lineage.left.clip_id, lineage.right.clip_id].includes(clip.clip_id));
        this.clips.push({ ...lineage.original });
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { original_clip_id: clipId, left_clip_id: lineage.left.clip_id, right_clip_id: lineage.right.clip_id });
      }
      if (operation === "resplit" && method === "POST") {
        const lineage = this.requiredLineage(clipId);
        expect(body.left_clip_id).toBe(lineage.left.clip_id);
        expect(body.right_clip_id).toBe(lineage.right.clip_id);
        this.clips = this.clips.filter((clip) => clip.clip_id !== clipId);
        this.clips.push({ ...lineage.left }, { ...lineage.right });
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { original_clip_id: clipId, left_clip_id: lineage.left.clip_id, right_clip_id: lineage.right.clip_id });
      }
      if (operation === "restore" && method === "POST") {
        const restored = this.tombstonedClips.get(clipId);
        if (!restored) return this.error(route, 409, "CLIP_RESTORE_CONFLICT");
        this.clips.push({ ...restored });
        this.tombstonedClips.delete(clipId);
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { clip_id: clipId });
      }
      if (!operation && method === "DELETE") {
        const removed = this.requiredClip(clipId);
        this.clips = this.clips.filter((clip) => clip.clip_id !== clipId);
        this.tombstonedClips.set(clipId, { ...removed });
        this.history.barrier();
        return this.mutated(route, idempotencyKey, { clip_id: clipId });
      }
    }

    const dspMatch = relative.match(/^\/clips\/([^/]+)\/(gain|fade|loop)$/);
    if (dspMatch && method === "PATCH") {
      const clipId = decodeURIComponent(dspMatch[1]);
      const clip = this.requiredClip(clipId);
      const before = { ...clip };
      if (dspMatch[2] === "gain") clip.gain_db = Number(body.gain_db).toFixed(2);
      if (dspMatch[2] === "fade") {
        clip.fade_in = String(body.fade_in);
        clip.fade_out = String(body.fade_out);
      }
      if (dspMatch[2] === "loop") {
        clip.loop_enabled = Boolean(body.loop_enabled);
        clip.timeline_duration = decimal(body.timeline_duration);
        clip.loop_phase = clip.loop_enabled ? clip.loop_phase : "0";
      }
      this.appendClipHistory(clipId, before, { ...clip });
      return this.mutated(route, idempotencyKey, { clip_id: clipId });
    }

    return this.error(route, 404, "TEST_OPERATION_NOT_IMPLEMENTED");
  }

  private snapshot() {
    const timelineDuration = this.clips.reduce((maximum, clip) => Math.max(maximum, Number(clip.timeline_start) + Number(clip.source_out) - Number(clip.source_in)), 0);
    return {
      working_composition_id: IDS.working,
      project_id: projectId,
      base_composition_snapshot_id: snapshotId,
      revision: this.revision,
      master_gain_db: this.masterGainDb,
      mix_settings: {},
      tracks: this.tracks.map((track) => ({ ...track })),
      clips: this.clips.map((clip) => ({ ...clip })),
      timeline_duration: decimal(timelineDuration),
    };
  }

  private mutated(route: Route, key: string | null, result: Record<string, unknown>, loseResponse = false) {
    this.revision += 1;
    const completion = { ...result, completed_revision: this.revision, replayed: false };
    this.remember(key, completion);
    if (loseResponse) {
      this.expectedResponseLosses += 1;
      return route.abort("failed");
    }
    return this.data(route, { data: completion });
  }

  private remember(key: string | null, result: Record<string, unknown>) {
    if (key) this.completions.set(key, { ...result });
  }

  private appendClipHistory(clipId: string, before: Clip, after: Clip) {
    this.history.append({
      clipId,
      applyBefore: () => Object.assign(this.requiredClip(clipId), before),
      applyAfter: () => Object.assign(this.requiredClip(clipId), after),
    });
  }

  private requiredTrack(id: string) {
    const track = this.tracks.find((item) => item.track_id === id);
    if (!track) throw new Error(`Missing track ${id}`);
    return track;
  }

  private requiredClip(id: string) {
    const clip = this.clips.find((item) => item.clip_id === id);
    if (!clip) throw new Error(`Missing clip ${id}`);
    return clip;
  }

  private requiredLineage(id: string) {
    const lineage = this.splitLineage.get(id);
    if (!lineage) throw new Error(`Missing split lineage ${id}`);
    return lineage;
  }

  private reindexTracks() {
    this.tracks = this.tracks.map((track, index) => ({ ...track, track_order: index }));
  }

  private data(route: Route, json: unknown) {
    return route.fulfill({ status: 200, json });
  }

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

test("WorkingComposition 48개 semantic scenario와 responsive control을 실제 Browser에서 검증한다", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const backend = new StatefulWorkingBackend();
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("requestfailed", (request) => failedRequests.push(request.url()));
  await backend.install(page);

  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("heading", { name: "WorkingComposition E2E" })).toBeVisible();
  await expect(page.getByText(/아직 WorkingComposition이 없습니다/)).toBeVisible();
  expect(backend.count("/initialize", "POST")).toBe(0);
  await page.getByRole("button", { name: "WorkingComposition 시작" }).click();
  await expectRevision(page, 0);
  expect(backend.workingExists).toBe(true);
  expect(backend.requests.filter((item) => item.path.endsWith("/initialize"))).toHaveLength(1);

  if (testInfo.project.name !== "chromium") {
    await responsiveSmoke(page, backend);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors.filter((message) => !isExpectedTransportConsoleError(message))).toEqual([]);
    expect(consoleErrors.some((message) => message.includes("404"))).toBe(true);
    expect(failedRequests).toEqual([]);
    return;
  }

  await addTrack(page, "Drums");
  await expectRevision(page, 1);
  await addTrack(page, "Bass");
  await expectRevision(page, 2);
  expect(backend.tracks.map((track) => track.track_id)).toEqual([IDS.track1, IDS.track2]);

  const drumsName = page.getByLabel("Drums Track 이름");
  await drumsName.fill("Lead");
  await drumsName.blur();
  await expectRevision(page, 3);
  expect(backend.requiredTrackForTest(IDS.track1).name).toBe("Lead");

  await page.locator(".working-track-row").filter({ has: page.getByLabel("Bass Track 이름") }).dragTo(
    page.locator(".working-track-row").filter({ has: page.getByLabel("Lead Track 이름") }),
  );
  await expectRevision(page, 4);
  expect(backend.tracks.map((track) => track.track_id)).toEqual([IDS.track2, IDS.track1]);

  await page.getByRole("button", { name: "Lead Track 삭제" }).click();
  await expectRevision(page, 5);
  await browserMutation(page, `${workingBase}/tracks/${IDS.track1}/restore`, "POST", {
    working_composition_id: IDS.working, expected_revision: 5, target_track_order: 1,
  });
  await expectRevision(page, 6);
  expect(backend.tracks.map((track) => track.track_id)).toEqual([IDS.track2, IDS.track1]);
  await browserMutation(page, `${workingBase}/tracks/${IDS.track1}`, "DELETE", {
    working_composition_id: IDS.working, expected_revision: 6,
  });
  await expectRevision(page, 7);
  expect(backend.tracks.some((track) => track.track_id === IDS.track1)).toBe(false);
  await browserMutation(page, `${workingBase}/tracks/${IDS.track1}/restore`, "POST", {
    working_composition_id: IDS.working, expected_revision: 7, target_track_order: 1,
  });
  await expectRevision(page, 8);
  expect(backend.tracks[1]).toMatchObject({ track_id: IDS.track1, track_order: 1 });
  await expectHistoryEmpty(page);

  await page.getByRole("button", { name: "Lead Track 선택" }).click();
  await expect(page.getByLabel("Clip source AssetVersion")).toHaveValue(IDS.assetVersion);
  backend.loseNextClipCreateResponse = true;
  await page.getByRole("button", { name: "Clip 배치" }).click();
  await expectRevision(page, 9);
  expect(backend.clips).toHaveLength(1);
  expect(backend.clips[0]).toMatchObject({ clip_id: IDS.original, source_asset_version_id: IDS.assetVersion, track_id: IDS.track1 });
  const clipCreateRequests = backend.requests.filter((item) => item.path.endsWith("/clips") && item.method === "POST");
  expect(clipCreateRequests).toHaveLength(2);
  expect(clipCreateRequests[0].idempotencyKey).toBe(clipCreateRequests[1].idempotencyKey);

  const originalButton = page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ });
  await originalButton.click();
  await drag(page, originalButton, 128, 4);
  await expectRevision(page, 10);
  expect(backend.count(`/${IDS.original}/move`, "PATCH")).toBe(1);

  const moveCount = backend.count(`/${IDS.original}/move`, "PATCH");
  await cancelDrag(page, originalButton, 64);
  expect(backend.count(`/${IDS.original}/move`, "PATCH")).toBe(moveCount);

  const startHandle = page.locator(`[aria-label="Clip 44444444 시작 Trim"]`);
  await drag(page, startHandle, 64, 3);
  await expectRevision(page, 11);
  expect(backend.count(`/${IDS.original}/trim-start`, "PATCH")).toBe(1);

  const endHandle = page.locator(`[aria-label="Clip 44444444 끝 Trim"]`);
  await drag(page, endHandle, -64, 3);
  await expectRevision(page, 12);
  expect(backend.count(`/${IDS.original}/trim-end`, "PATCH")).toBe(1);

  await setPlayhead(page, splitPlayhead(backend.requiredClipForTest(IDS.original)));
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  await expectRevision(page, 13);
  expect(backend.clips.map((clip) => clip.clip_id).sort()).toEqual([IDS.left, IDS.right].sort());
  await expectHistoryEmpty(page);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/unsplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 13,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 14);
  expect(backend.clips.map((clip) => clip.clip_id)).toEqual([IDS.original]);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/resplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 14,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 15);
  expect(backend.clips.map((clip) => clip.clip_id).sort()).toEqual([IDS.left, IDS.right].sort());

  await page.getByRole("button", { name: "Lead Track 선택" }).click();
  await page.getByRole("button", { name: /Clip 66666666 선택 및 이동/ }).click();
  await page.getByRole("button", { name: /Clip 삭제/ }).click();
  await expectRevision(page, 16);
  await expectHistoryEmpty(page);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}/restore`, "POST", {
    working_composition_id: IDS.working, expected_revision: 16,
  });
  await expectRevision(page, 17);
  expect(backend.clips.some((clip) => clip.clip_id === IDS.left)).toBe(true);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}`, "DELETE", {
    working_composition_id: IDS.working, expected_revision: 17,
  });
  await expectRevision(page, 18);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}/restore`, "POST", {
    working_composition_id: IDS.working, expected_revision: 18,
  });
  await expectRevision(page, 19);
  expect(backend.clips.filter((clip) => clip.clip_id === IDS.left)).toHaveLength(1);

  await browserMutation(page, `${workingBase}/clips/${IDS.original}/unsplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 19,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 20);
  expect(backend.clips.map((clip) => clip.clip_id)).toEqual([IDS.original]);
  await page.getByRole("button", { name: "Lead Track 선택" }).click();
  await page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ }).click();
  await setPlayhead(page, splitPlayhead(backend.requiredClipForTest(IDS.original)));
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  await expectRevision(page, 21);
  const leftButton = page.getByRole("button", { name: /Clip 66666666 선택 및 이동/ });
  await leftButton.click();
  await drag(page, leftButton, 64, 3);
  await expectRevision(page, 22);
  const leftStart = page.locator(`[aria-label="Clip 66666666 시작 Trim"]`);
  await drag(page, leftStart, 32, 2);
  await expectRevision(page, 23);
  const finalGeometry = { ...backend.clips.find((clip) => clip.clip_id === IDS.left)! };
  await undo(page);
  await expectRevision(page, 24);
  await undo(page);
  await expectRevision(page, 25);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/unsplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 25,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 26);
  expect(backend.clips.map((clip) => clip.clip_id)).toEqual([IDS.original]);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/resplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 26,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 27);
  await expectHistoryEmpty(page);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}/move`, "PATCH", {
    working_composition_id: IDS.working, expected_revision: 27,
    timeline_start: finalGeometry.timeline_start,
  });
  await expectRevision(page, 28);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}/trim-start`, "PATCH", {
    working_composition_id: IDS.working, expected_revision: 28,
    timeline_start: finalGeometry.timeline_start, source_in: finalGeometry.source_in,
  });
  await expectRevision(page, 29);
  expect(backend.clips.find((clip) => clip.clip_id === IDS.left)).toEqual(finalGeometry);

  const leadInput = page.getByLabel("Lead Track 이름");
  const renameRequestsBeforeIsolation = backend.requests.filter((item) => item.path.endsWith(`/tracks/${IDS.track1}`) && item.method === "PATCH").length;
  await leadInput.focus();
  await page.keyboard.press("Control+Z");
  expect(backend.requests.filter((item) => item.path.endsWith(`/tracks/${IDS.track1}`) && item.method === "PATCH")).toHaveLength(renameRequestsBeforeIsolation);

  await leadInput.fill("Lead Keyboard");
  await leadInput.blur();
  await expectRevision(page, 30);
  await expect(page.getByLabel("Lead Keyboard Track 이름")).toHaveValue("Lead Keyboard");
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  await page.keyboard.press("Control+Z");
  await expectRevision(page, 31);
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeEnabled();
  await page.keyboard.press("Control+Shift+Z");
  await expectRevision(page, 32);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  await page.keyboard.press("Control+Z");
  await expectRevision(page, 33);
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeEnabled();
  await page.keyboard.press("Control+Y");
  await expectRevision(page, 34);

  await page.evaluate(() => {
    const textarea = document.createElement("textarea");
    textarea.setAttribute("aria-label", "E2E textarea");
    document.body.append(textarea);
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    editable.setAttribute("aria-label", "E2E contenteditable");
    document.body.append(editable);
  });
  const historyRequestsBeforeEditable = backend.requests.length;
  await page.getByLabel("E2E textarea").focus();
  await page.keyboard.press("Control+Z");
  await page.getByLabel("E2E contenteditable").focus();
  await page.keyboard.press("Control+Y");
  expect(backend.requests).toHaveLength(historyRequestsBeforeEditable);

  await page.getByLabel("Lead Keyboard Track 이름").fill("Barrier Edit");
  await page.getByLabel("Lead Keyboard Track 이름").blur();
  await expectRevision(page, 35);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  await page.getByRole("button", { name: "현재 Snapshot Checkout" }).click();
  await expectRevision(page, 36);
  await expectHistoryEmpty(page);

  await addTrack(page, "Refresh Track");
  await expectRevision(page, 37);
  const refreshInput = page.getByLabel("Refresh Track Track 이름");
  await refreshInput.fill("Canonical After Refresh");
  await refreshInput.blur();
  await expectRevision(page, 38);
  await page.reload();
  await expectRevision(page, 38);
  await expect(page.getByLabel("Canonical After Refresh Track 이름")).toBeVisible();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();

  const canonicalInput = page.getByLabel("Canonical After Refresh Track 이름");
  await canonicalInput.fill("History Before Conflict");
  await canonicalInput.blur();
  await expectRevision(page, 39);
  const workspaceReadsBeforeConflict = backend.count("/working-composition", "GET");
  const historyReadsBeforeConflict = backend.count("/history", "GET");
  backend.advanceBeforeNextMutation = true;
  const conflictingInput = page.getByLabel("History Before Conflict Track 이름");
  await conflictingInput.fill("Stale Rename");
  await conflictingInput.blur();
  await expectRevision(page, 40);
  expect(backend.count("/working-composition", "GET")).toBeGreaterThan(workspaceReadsBeforeConflict);
  expect(backend.count("/history", "GET")).toBeGreaterThan(historyReadsBeforeConflict);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  expect(backend.requests.filter((item) => item.path.endsWith(`/tracks/${IDS.track3}`) && item.method === "PATCH" && item.body.name === "Stale Rename")).toHaveLength(1);

  expect(backend.clips.filter((clip) => clip.clip_id === IDS.left)).toHaveLength(0);
  expect(new Set(backend.completions.keys()).size).toBe(backend.completions.size);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((message) => !isExpectedTransportConsoleError(message))).toEqual([]);
  expect(consoleErrors.some((message) => message.includes("404"))).toBe(true);
  expect(consoleErrors.some((message) => message.includes("ERR_FAILED"))).toBe(true);
  expect(consoleErrors.some((message) => message.includes("409"))).toBe(true);
  expect(failedRequests).toHaveLength(backend.expectedResponseLosses);
});

test("Mixer canonical mutation, history, reload와 fixed workspace geometry를 검증한다", async ({ page }) => {
  const backend = new StatefulWorkingBackend();
  backend.workingExists = true;
  backend.revision = 2;
  backend.tracks = [
    { track_id: IDS.track1, track_type: "audio", name: "Lead", track_order: 0, gain_db: "0.00", pan: "0.00", muted: false, solo: false },
    { track_id: IDS.track2, track_type: "audio", name: "Bass", track_order: 1, gain_db: "0.00", pan: "0.00", muted: false, solo: false },
  ];
  backend.clips = [{ clip_id: IDS.original, track_id: IDS.track1, source_asset_version_id: IDS.assetVersion, timeline_start: "0.000", source_in: "0.000", source_out: "8.000", source_duration: "30.000", timeline_duration: "8.000", loop_enabled: false, loop_phase: "0", gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null }];
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);

  const gain = page.getByRole("slider", { name: "Track Lead gain" });
  await gain.fill("6");
  await gain.dispatchEvent("pointerup");
  await expectRevision(page, 3);
  const pan = page.getByRole("slider", { name: "Track Lead pan" });
  await pan.fill("-0.5");
  await pan.dispatchEvent("pointerup");
  await expectRevision(page, 4);
  await page.getByRole("button", { name: "Track Lead mute", exact: true }).click();
  await expectRevision(page, 5);
  await page.getByRole("button", { name: "Track Lead solo", exact: true }).click();
  await expectRevision(page, 6);
  const master = page.getByRole("slider", { name: "Master gain" });
  await master.fill("-3");
  await master.dispatchEvent("pointerup");
  await expectRevision(page, 7);
  expect(backend.requiredTrackForTest(IDS.track1)).toMatchObject({ gain_db: "6.00", pan: "-0.50", muted: true, solo: true });
  expect(backend.masterGainDb).toBe("-3.00");
  await page.reload();
  await expect(page.getByRole("slider", { name: "Track Lead gain" })).toHaveValue("6");
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
  await expectRevision(page, 8);
  expect(backend.masterGainDb).toBe("0.00");

  const geometry = await page.evaluate(() => ({
    documentVertical: document.scrollingElement!.scrollHeight - document.scrollingElement!.clientHeight,
    documentHorizontal: document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
    mixerScrollable: Array.from(document.querySelectorAll(".mixer-track-strips")).every((element) => element.scrollWidth >= element.clientWidth),
  }));
  expect(geometry.documentVertical).toBeLessThanOrEqual(1);
  expect(geometry.documentHorizontal).toBeLessThanOrEqual(1);
  expect(geometry.mixerScrollable).toBe(true);
  await expect(page.getByLabel("Master mixer strip")).toBeVisible();
});

test("Gain, Fade, Loop persistent history는 reload 후에도 nonzero phase를 strict LIFO로 복원한다", async ({ page }) => {
  const backend = new StatefulWorkingBackend();
  backend.workingExists = true;
  backend.revision = 3;
  backend.tracks = [{ track_id: IDS.track1, track_type: "audio", name: "DSP", track_order: 0 }];
  backend.clips = [{
    clip_id: IDS.original, track_id: IDS.track1, source_asset_version_id: IDS.assetVersion,
    timeline_start: "0.000", source_in: "0.000", source_out: "8.000", source_duration: "30.000",
    timeline_duration: "8.000", loop_enabled: true, loop_phase: "1.25",
    gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null,
  }];
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: "DSP Track 선택" }).click();
  await page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ }).click();

  const gain = page.getByLabel("Clip gain exact value");
  const fadeIn = page.getByLabel("Fade In exact value");
  const loopDuration = page.getByLabel("Clip loop timeline duration");
  await gain.fill("3"); await gain.blur();
  await fadeIn.fill("0.5"); await fadeIn.blur();
  await loopDuration.fill("4"); await loopDuration.blur();
  await expectRevision(page, 6);
  expect(backend.history.projection()).toMatchObject({ cursor: 3, command_count: 3, can_undo: true, can_redo: false });

  await undo(page);
  expect(backend.requiredClipForTest(IDS.original)).toMatchObject({ loop_enabled: true, timeline_duration: "8.000", loop_phase: "1.25" });
  await page.reload();
  expect(backend.history.projection()).toMatchObject({ cursor: 2, command_count: 3, can_undo: true, can_redo: true });
  await undo(page);
  expect(backend.requiredClipForTest(IDS.original).fade_in).toBe("0");
  await page.reload();
  expect(backend.history.projection()).toMatchObject({ cursor: 1, command_count: 3, can_undo: true, can_redo: true });
  await undo(page);
  expect(backend.requiredClipForTest(IDS.original).gain_db).toBe("0.00");
  await redo(page);
  await redo(page);
  await redo(page);
  expect(backend.requiredClipForTest(IDS.original)).toMatchObject({
    gain_db: "3.00", fade_in: "0.5", loop_enabled: true, timeline_duration: "4.000", loop_phase: "1.25",
  });
  expect(backend.count(`/${IDS.original}/gain`, "PATCH")).toBe(1);
  expect(backend.count(`/${IDS.original}/fade`, "PATCH")).toBe(1);
  expect(backend.count(`/${IDS.original}/loop`, "PATCH")).toBe(1);
  expect(backend.count(`/${IDS.original}/loop/restore`)).toBe(0);

  await undo(page);
  await page.getByRole("button", { name: "DSP Track 선택" }).click();
  await page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ }).click();
  await gain.fill("6"); await gain.blur();
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  expect(backend.history.projection()).toMatchObject({ cursor: 3, command_count: 3, can_redo: false });
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  expect(backend.history.projection()).toMatchObject({ cursor: 0, command_count: 0, can_undo: false, can_redo: false });
});

async function responsiveSmoke(page: Page, backend: StatefulWorkingBackend) {
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toHaveAccessibleName("편집 실행 취소");
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toHaveAccessibleName("편집 다시 실행");
  await expectHistoryEmpty(page);
  await addTrack(page, "Responsive Track");
  await page.getByRole("button", { name: "Clip 배치" }).click();
  await expectRevision(page, 2);
  const clip = page.getByRole("button", { name: /Clip 44444444 선택 및 이동/ });
  await clip.click();
  await expect(clip).toHaveAttribute("aria-pressed", "true");
  await setPlayhead(page, 5);
  await page.getByRole("button", { name: /Playhead에서 Split/ }).click();
  await expectRevision(page, 3);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/unsplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 3,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 4);
  await browserMutation(page, `${workingBase}/clips/${IDS.original}/resplit`, "POST", {
    working_composition_id: IDS.working, expected_revision: 4,
    left_clip_id: IDS.left, right_clip_id: IDS.right,
  });
  await expectRevision(page, 5);
  const left = page.getByRole("button", { name: /Clip 66666666 선택 및 이동/ });
  await left.click();
  await page.getByRole("button", { name: /Clip 삭제/ }).click();
  await expectRevision(page, 6);
  await browserMutation(page, `${workingBase}/clips/${IDS.left}/restore`, "POST", {
    working_composition_id: IDS.working, expected_revision: 6,
  });
  await expectRevision(page, 7);
  expect(backend.clips.some((item) => item.clip_id === IDS.left)).toBe(true);
  await expect(page.getByRole("button", { name: "Responsive Track Track 삭제" })).toHaveAccessibleName("Responsive Track Track 삭제");
}

async function addTrack(page: Page, name: string) {
  await page.getByLabel("새 Track 이름").fill(name);
  await page.getByRole("button", { name: "Track 추가" }).click();
  await expect(page.getByLabel(`${name} Track 이름`)).toBeVisible();
}

async function undo(page: Page) {
  await page.getByRole("button", { name: "편집 실행 취소" }).click();
}

async function redo(page: Page) {
  await page.getByRole("button", { name: "편집 다시 실행" }).click();
}

async function browserMutation(
  page: Page,
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body: Record<string, unknown>,
) {
  await page.evaluate(async ({ path, method, body }) => {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Fixture mutation failed: ${response.status}`);
  }, { path, method, body });
  await page.reload();
}

async function expectHistoryEmpty(page: Page) {
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
}

async function expectRevision(page: Page, revision: number) {
  await expect(page.getByText(new RegExp(`revision ${revision} ·`))).toBeVisible();
}

async function drag(page: Page, locator: Locator, deltaX: number, moves: number) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("Drag target has no bounding box");
  const startX = box.x + Math.min(Math.max(box.width / 2, 2), Math.max(box.width - 2, 2));
  const startY = box.y + box.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (let index = 1; index <= moves; index += 1) {
    await page.mouse.move(startX + deltaX * index / moves, startY);
  }
  await page.mouse.up();
}

async function cancelDrag(page: Page, locator: Locator, deltaX: number) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("Cancel drag target has no bounding box");
  const startX = box.x + Math.min(Math.max(box.width / 2, 2), Math.max(box.width - 2, 2));
  const startY = box.y + box.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + deltaX, startY);
  await locator.dispatchEvent("pointercancel", { pointerId: 1, clientX: startX + deltaX, clientY: startY });
  await page.mouse.up();
}

async function setPlayhead(page: Page, seconds: number) {
  const audio = page.locator("audio");
  await expect(audio).toHaveCount(1);
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  const slider = page.getByRole("slider", { name: "Timeline Playhead 재생 위치" });
  await expect(slider).toBeVisible();
  const current = Number(await slider.getAttribute("aria-valuenow"));
  if (Math.abs(current - seconds) > 0.05) {
    const ruler = page.getByRole("button", { name: "초 단위 Timeline ruler" });
    await ruler.click({ position: { x: 164 + seconds * 64, y: 10 } });
  }
  await audio.evaluate((element: HTMLAudioElement, next) => {
    element.currentTime = next;
    element.dispatchEvent(new Event("timeupdate"));
  }, seconds);
  await expect.poll(async () => Number(await slider.getAttribute("aria-valuenow"))).toBeCloseTo(seconds, 1);
  await expect(page.getByRole("button", { name: /Playhead에서 Split/ })).toBeEnabled();
}

function decimal(value: unknown) {
  return Number(value).toFixed(3);
}

function isExpectedTransportConsoleError(message: string) {
  return /404 \(Not Found\)|409 \(Conflict\)|net::ERR_FAILED/.test(message);
}

function splitPlayhead(clip: Clip) {
  return Number(clip.timeline_start) + (Number(clip.source_out) - Number(clip.source_in)) / 2;
}

function compositionWorkspace() {
  return {
    state: "ready",
    project: {
      project_id: projectId,
      workspace_id: "workspace-1",
      title: "WorkingComposition E2E",
      lifecycle_status: "active",
      created_at: "2026-08-26T00:00:00Z",
      updated_at: "2026-08-26T00:00:00Z",
    },
    selection: {
      selected_snapshot_id: snapshotId,
      resolved_snapshot_id: snapshotId,
      resolution: "selected",
      is_current: true,
    },
    snapshot: {
      composition_snapshot_id: snapshotId,
      project_id: projectId,
      snapshot_version: 1,
      processing_chain_id: null,
      provider_versions: {},
      model_manifest_ids: {},
      created_at: "2026-08-26T00:00:00Z",
    },
    items: [{
      snapshot_item_id: "snapshot-item-1",
      item_role: "mix",
      sort_order: 0,
      asset_version: {
        asset_version_id: IDS.assetVersion,
        asset_id: "asset-1",
        version_number: 1,
        version_origin: "provider_output",
        parent_asset_version_id: null,
        processing_chain_id: null,
        provider_id: "fixture",
        model_manifest_id: null,
        settings_snapshot: {},
        created_at: "2026-08-26T00:00:00Z",
      },
      artifacts: [{
        artifact_id: "artifact-mix",
        asset_version_id: IDS.assetVersion,
        artifact_kind: "audio",
        media_type: "audio/wav",
        size_bytes: silentWav.length,
        checksum_algorithm: "sha256",
        artifact_checksum: "fixture-checksum",
        producer_type: "test",
        producer_id: "fixture",
        run_id: null,
        retention_status: "active",
        created_at: "2026-08-26T00:00:00Z",
        content_url: "/api/v1/artifacts/artifact-mix/content",
        download_url: null,
      }],
    }],
    track_projections: [{
      projection_id: "snapshot-item-1",
      identity_scope: "snapshot",
      snapshot_item_id: "snapshot-item-1",
      item_role: "mix",
      sort_order: 0,
      asset_id: "asset-1",
      asset_version_id: IDS.assetVersion,
    }],
    section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {},
    lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function createSilentWav(durationSeconds: number) {
  const sampleRate = 8_000;
  const dataSize = sampleRate * durationSeconds;
  const wav = Buffer.alloc(44 + dataSize, 128);
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
  return wav;
}
