import { expect, test, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const projectId = "clip-copy-e2e";
const workingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const sourceTrackId = "11111111-1111-4111-8111-111111111111";
const targetTrackId = "22222222-2222-4222-8222-222222222222";
const sourceClipId = "33333333-3333-4333-8333-333333333333";
const copiedClipId = "44444444-4444-4444-8444-444444444444";
const childClipId = "55555555-5555-4555-8555-555555555555";
const versionId = "66666666-6666-4666-8666-666666666666";
const artifactId = "77777777-7777-4777-8777-777777777777";
const previewArtifactId = "88888888-8888-4888-8888-888888888888";
const silentWav = createSilentWav();

type Clip = ReturnType<typeof sourceClip>;

class CopyBackend {
  revision = 3;
  clips: Clip[] = [sourceClip()];
  tombstones = new Map<string, Clip>();
  copyPosts: Array<{ sourceId: string; body: Record<string, unknown>; key: string | null }> = [];
  previewPosts = 0;
  commitPosts = 0;
  committedIds: string[] = [];
  loseNextCopyResponse = false;
  rejectNextCopyRevision = false;
  private completions = new Map<string, { clip: Clip; revision: number }>();
  history = new PersistentHistoryFixture(workingId, {
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
    if (path === "/backend/health") return this.ok(route, { status: "ok" });
    if (path === `/backend/api/v1/projects/${projectId}`) return this.ok(route, { data: { ...project(), project_id: projectId } });
    if (path === `/backend/api/v1/projects/${projectId}/composition`) {
      return this.ok(route, { data: composition() });
    }
    if (path === `/backend/api/v1/projects/${projectId}/working-composition` && method === "GET") {
      return this.ok(route, { data: this.working() });
    }
    if (path.endsWith("/working-composition/history") && method === "GET") {
      return this.ok(route, { data: this.history.projection() });
    }
    if (path.endsWith("/working-composition/history/undo") && method === "POST") {
      return this.history.mutate(route, "undo");
    }
    if (path.endsWith("/working-composition/history/redo") && method === "POST") {
      return this.history.mutate(route, "redo");
    }
    if (path === `/backend/api/v1/projects/${projectId}/asset-versions/${versionId}/media-source`) {
      return this.ok(route, { data: mediaSource() });
    }
    if (path === `/backend/api/v1/artifacts/${artifactId}/content`
      || path === `/backend/api/v1/artifacts/${previewArtifactId}/content`) {
      return route.fulfill({ status: 200, contentType: "audio/wav", body: silentWav });
    }
    if (path.endsWith("/working-composition/preview") && method === "POST") {
      this.previewPosts += 1;
      return this.ok(route, { data: {
        job_id: "preview-job", preview_render_id: "preview-render",
        working_composition_id: workingId, rendered_revision: this.revision,
        status: "queued", replayed: false,
      } }, 202);
    }
    if (path === "/backend/api/v1/jobs/preview-job") {
      return this.ok(route, { data: previewJob() });
    }
    const copyMatch = path.match(/\/working-composition\/clips\/([^/]+)\/copy$/);
    if (copyMatch && method === "POST") return this.copy(route, decodeURIComponent(copyMatch[1]));
    const restoreMatch = path.match(/\/working-composition\/clips\/([^/]+)\/restore$/);
    if (restoreMatch && method === "POST") return this.restore(route, decodeURIComponent(restoreMatch[1]));
    const deleteMatch = path.match(/\/working-composition\/clips\/([^/]+)$/);
    if (deleteMatch && method === "DELETE") return this.remove(route, decodeURIComponent(deleteMatch[1]));
    if (path.endsWith("/working-composition/commit") && method === "POST") {
      const body = request.postDataJSON() as { expected_revision: number };
      if (body.expected_revision !== this.revision) return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      this.commitPosts += 1;
      this.committedIds = this.clips.map((clip) => clip.clip_id);
      this.history.barrier();
      this.revision += 1;
      return this.ok(route, { data: {
        working_composition_id: workingId, composition_snapshot_id: "snapshot-copy",
        completed_revision: this.revision, replayed: false,
      } }, 201);
    }
    return this.error(route, 404, "TEST_ROUTE_NOT_FOUND");
  }

  private copy(route: Route, sourceId: string) {
    const request = route.request();
    const body = request.postDataJSON() as {
      expected_revision: number; target_track_id: string; target_timeline_start: string;
    };
    const key = request.headers()["idempotency-key"] ?? null;
    this.copyPosts.push({ sourceId, body, key });
    const replay = key ? this.completions.get(key) : undefined;
    if (replay) return this.copyResponse(route, replay.clip.clip_id, replay.revision, true);
    if (this.rejectNextCopyRevision) {
      this.rejectNextCopyRevision = false;
      this.revision += 1;
      return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    }
    if (body.expected_revision !== this.revision) return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
    const source = this.clips.find((clip) => clip.clip_id === sourceId);
    if (!source) return this.error(route, 404, "CLIP_NOT_FOUND");
    const start = Number(body.target_timeline_start);
    if (this.clips.some((clip) => clip.track_id === body.target_track_id
      && start < Number(clip.timeline_start) + Number(clip.source_out) - Number(clip.source_in)
      && start + Number(source.source_out) - Number(source.source_in) > Number(clip.timeline_start))) {
      return this.error(route, 409, "CLIP_OVERLAP");
    }
    const id = this.completions.size === 0 ? copiedClipId : crypto.randomUUID();
    const copied = {
      ...source,
      clip_id: id,
      track_id: body.target_track_id,
      timeline_start: Number(body.target_timeline_start).toFixed(3),
      split_from_clip_id: null,
    };
    this.clips.push(copied);
    this.history.barrier();
    this.revision += 1;
    if (key) this.completions.set(key, { clip: copied, revision: this.revision });
    if (this.loseNextCopyResponse) {
      this.loseNextCopyResponse = false;
      return route.abort("connectionreset");
    }
    return this.copyResponse(route, copied.clip_id, this.revision, false);
  }

  private remove(route: Route, id: string) {
    const index = this.clips.findIndex((clip) => clip.clip_id === id);
    if (index < 0) return this.error(route, 404, "CLIP_NOT_FOUND");
    const [clip] = this.clips.splice(index, 1);
    this.tombstones.set(id, clip);
    this.history.barrier();
    this.revision += 1;
    return this.ok(route, { data: { clip_id: id, completed_revision: this.revision, replayed: false } });
  }

  private restore(route: Route, id: string) {
    const clip = this.tombstones.get(id);
    if (!clip) return this.error(route, 409, "CLIP_ALREADY_ACTIVE");
    this.tombstones.delete(id);
    this.clips.push(clip);
    this.history.barrier();
    this.revision += 1;
    return this.ok(route, { data: { clip_id: id, completed_revision: this.revision, replayed: false } });
  }

  private working() {
    return {
      working_composition_id: workingId, project_id: projectId,
      base_composition_snapshot_id: "snapshot-1", revision: this.revision, mix_settings: {},
      tracks: [
        { track_id: sourceTrackId, track_type: "audio", name: "Source", track_order: 0 },
        { track_id: targetTrackId, track_type: "audio", name: "Target", track_order: 1 },
      ],
      clips: this.clips,
      timeline_duration: String(Math.max(0, ...this.clips.map((clip) => Number(clip.timeline_start) + 2))),
    };
  }

  private copyResponse(route: Route, id: string, revision: number, replayed: boolean) {
    return this.ok(route, { data: { clip_id: id, completed_revision: revision, replayed } }, 201);
  }
  private ok(route: Route, json: unknown, status = 200) { return route.fulfill({ status, json }); }
  private error(route: Route, status: number, code: string) {
    return route.fulfill({ status, json: { error: { code, error_code: code, message: code } } });
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("doha-studio-settings", JSON.stringify({
    state: { reducedMotion: true, onboardingCompleted: true }, version: 0,
  })));
});

test("explicit cross-Track Copy preserves source, waveform, revision and makes Preview stale", async ({ page }) => {
  const backend = new CopyBackend();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: "Working Preview 만들기" }).click();
  await expect(page.getByRole("button", { name: /Working Preview revision 3 재생/ })).toBeVisible();

  await page.getByRole("button", { name: /Clip 33333333 선택 및 이동/ }).click();
  const sourceSignature = await page.getByTestId(`clip-waveform-${sourceClipId}`)
    .getAttribute("data-waveform-signature");
  const action = page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" });
  await expect(action).toBeDisabled();
  await page.getByLabel("Copy 대상 Track").selectOption(targetTrackId);
  await page.getByLabel("Copy Timeline start").fill("0");
  await expect(action).toBeEnabled();
  await action.click();

  await page.getByRole("button", { name: "Target Track 선택" }).click();
  await expect(page.getByTestId(`clip-waveform-${copiedClipId}`)).toBeVisible();
  await expect(page.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
  expect(backend.previewPosts).toBe(1);
  expect(backend.copyPosts).toHaveLength(1);
  expect(backend.copyPosts[0]).toMatchObject({
    sourceId: sourceClipId,
    body: {
      working_composition_id: workingId,
      expected_revision: 3,
      target_track_id: targetTrackId,
      target_timeline_start: "0",
    },
  });
  expect(backend.copyPosts[0].key).toBeTruthy();
  expect(backend.clips.find((clip) => clip.clip_id === sourceClipId)).toEqual(sourceClip());
  const copied = backend.clips.find((clip) => clip.clip_id === copiedClipId)!;
  expect(copied).toMatchObject({
    source_asset_version_id: versionId, source_in: "0.000", source_out: "2.000",
    source_duration: "2.000", gain_db: sourceClip().gain_db, fade_in: sourceClip().fade_in,
    fade_out: sourceClip().fade_out, split_from_clip_id: null, track_id: targetTrackId,
  });
  expect(backend.revision).toBe(4);
  expect(await page.getByTestId(`clip-waveform-${copiedClipId}`).getAttribute("data-waveform-signature"))
    .toBe(sourceSignature);
  await page.keyboard.press("Control+C");
  await page.keyboard.press("Control+V");
  expect(backend.copyPosts).toHaveLength(1);
  expect(pageErrors).toEqual([]);
});

test("Copy response loss replays the same ID and establishes a history barrier", async ({ page }) => {
  const backend = new CopyBackend();
  backend.loseNextCopyResponse = true;
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: /Clip 33333333 선택 및 이동/ }).click();
  await page.getByLabel("Copy 대상 Track").selectOption(targetTrackId);
  await page.getByLabel("Copy Timeline start").fill("0");
  await page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }).click();
  await page.getByRole("button", { name: "Target Track 선택" }).click();
  await expect(page.getByTestId(`clip-waveform-${copiedClipId}`)).toBeVisible();
  expect(backend.copyPosts).toHaveLength(2);
  expect(backend.copyPosts[0].key).toBe(backend.copyPosts[1].key);
  expect(backend.clips.filter((clip) => clip.clip_id === copiedClipId)).toHaveLength(1);

  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  expect(backend.clips.some((clip) => clip.clip_id === copiedClipId)).toBe(true);
  expect(backend.copyPosts).toHaveLength(2);
});

test("overlap fails, split child Copy clears lineage, stale revision reconciles, and Commit freezes copied ID", async ({ page }) => {
  const backend = new CopyBackend();
  backend.clips.push({ ...sourceClip(), clip_id: childClipId, track_id: targetTrackId, split_from_clip_id: sourceClipId });
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);

  await page.getByRole("button", { name: /Clip 33333333 선택 및 이동/ }).click();
  await page.getByLabel("Copy 대상 Track").selectOption(sourceTrackId);
  await page.getByLabel("Copy Timeline start").fill("1");
  await page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }).click();
  await expect(page.locator(".alert-error")).toContainText(/겹칩니다|CLIP_OVERLAP/);
  expect(backend.copyPosts).toHaveLength(1);

  await page.getByRole("button", { name: "Target Track 선택" }).click();
  await page.getByRole("button", { name: /Clip 55555555 선택 및 이동/ }).click();
  await page.getByLabel("Copy 대상 Track").selectOption(sourceTrackId);
  await page.getByLabel("Copy Timeline start").fill("2");
  await page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }).click();
  await page.getByRole("button", { name: "Source Track 선택" }).click();
  await expect(page.getByTestId(`clip-waveform-${copiedClipId}`)).toBeVisible();
  expect(backend.clips.find((clip) => clip.clip_id === childClipId)?.split_from_clip_id).toBe(sourceClipId);
  expect(backend.clips.find((clip) => clip.clip_id === copiedClipId)?.split_from_clip_id).toBeNull();

  backend.rejectNextCopyRevision = true;
  await page.getByLabel("Copy Timeline start").fill("4");
  await page.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }).click();
  await expect(page.getByText(new RegExp(`revision ${backend.revision}`)).first()).toBeVisible();
  expect(backend.copyPosts).toHaveLength(3);
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();

  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByText(/새 버전으로 저장했습니다/)).toBeVisible();
  expect(backend.commitPosts).toBe(1);
  expect(backend.committedIds).toContain(copiedClipId);
});

function sourceClip() {
  return {
    clip_id: sourceClipId, track_id: sourceTrackId, source_asset_version_id: versionId,
    timeline_start: "0.000", source_in: "0.000", source_out: "2.000",
    source_duration: "2.000", timeline_duration: "2.000", loop_enabled: false, loop_phase: "0",
    gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null as string | null,
  };
}

function project() {
  return { id: projectId, title: "Clip Copy E2E", description: "stateful copy fixture", created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z", job_count: 0, jobs: [] };
}

function composition() {
  return {
    state: "ready",
    project: { project_id: projectId, workspace_id: "workspace-1", title: "Clip Copy E2E", lifecycle_status: "active", created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z" },
    selection: { selected_snapshot_id: "snapshot-1", resolved_snapshot_id: "snapshot-1", resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: "snapshot-1", project_id: projectId, snapshot_version: 1, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-08-30T00:00:00Z" },
    items: [{ snapshot_item_id: "item-1", item_role: "mix", sort_order: 0, asset_version: { asset_version_id: versionId, asset_id: "asset-1", version_number: 1, version_origin: "fixture", parent_asset_version_id: null, processing_chain_id: null, provider_id: null, model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-30T00:00:00Z" }, artifacts: [{ artifact_id: artifactId, asset_version_id: versionId, artifact_kind: "audio", media_type: "audio/wav", size_bytes: silentWav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture", producer_type: "test", producer_id: null, run_id: null, retention_status: "active", created_at: "2026-08-30T00:00:00Z", content_url: `/api/v1/artifacts/${artifactId}/content`, download_url: null }] }],
    track_projections: [], section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {}, lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function mediaSource() {
  return { asset_version_id: versionId, artifact_id: artifactId, media_type: "audio/wav", size_bytes: silentWav.length, artifact_checksum: "fixture", duration_seconds: "2", content_url: `/api/v1/artifacts/${artifactId}/content` };
}

function previewJob() {
  return {
    job_id: "preview-job", project_id: projectId, composition_snapshot_id: null,
    job_type: "working_preview", status: "succeeded", provider_id: null, model_manifest_id: null,
    progress_percent: 100, stage: null, retry_of_job_id: null, inputs: [],
    outputs: [{ output_role: "working_preview", output_order: 0, asset_version_id: null, artifact_id: previewArtifactId }],
    model_usages: [], error_code: null, error_message: null, error_retryable: null, error_details_id: null,
    created_at: "2026-08-30T00:00:00Z", started_at: "2026-08-30T00:00:01Z", completed_at: "2026-08-30T00:00:02Z",
  };
}

function createSilentWav() {
  const sampleRate = 8_000;
  const dataSize = sampleRate * 2;
  const wav = Buffer.alloc(44 + dataSize, 128);
  wav.write("RIFF", 0, "ascii"); wav.writeUInt32LE(36 + dataSize, 4); wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii"); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate, 28); wav.writeUInt16LE(1, 32); wav.writeUInt16LE(8, 34);
  wav.write("data", 36, "ascii"); wav.writeUInt32LE(dataSize, 40);
  return wav;
}
