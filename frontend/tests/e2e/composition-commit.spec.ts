import { expect, test, type Page, type Route } from "@playwright/test";
import { PersistentHistoryFixture } from "./support/persistent-history-fixture";

const projectId = "composition-commit-e2e";
const workingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const trackId = "11111111-1111-4111-8111-111111111111";
const clipId = "22222222-2222-4222-8222-222222222222";
const versionId = "33333333-3333-4333-8333-333333333333";
const artifactId = "44444444-4444-4444-8444-444444444444";
const silentWav = createSilentWav();

class CommitBackend {
  revision = 2;
  trackName = "Commit Track";
  clips = [clip()];
  baseSnapshotId = "snapshot-1";
  selectedSnapshotId = "snapshot-1";
  snapshotVersion = 1;
  commitPosts: Array<{ revision: number; key: string | null }> = [];
  renamePosts = 0;
  compositionReads = 0;
  loseNextResponse = false;
  rejectNextCommit: "COMMIT_FAILED" | "WORKING_COMPOSITION_REVISION_CONFLICT" | null = null;
  private completions = new Map<string, { snapshotId: string; revision: number }>();
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
      this.compositionReads += 1;
      return this.ok(route, { data: composition(this.selectedSnapshotId, this.snapshotVersion) });
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
    if (path === `/backend/api/v1/artifacts/${artifactId}/content`) {
      return route.fulfill({ status: 200, contentType: "audio/wav", body: silentWav });
    }
    if (path.endsWith(`/working-composition/tracks/${trackId}`) && method === "PATCH") {
      const body = request.postDataJSON() as { name: string; expected_revision: number };
      this.renamePosts += 1;
      if (body.expected_revision !== this.revision) {
        return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      }
      const before = this.trackName;
      const after = body.name;
      this.trackName = after;
      this.history.append({
        clipId,
        applyBefore: () => { this.trackName = before; },
        applyAfter: () => { this.trackName = after; },
      });
      this.revision += 1;
      return this.ok(route, { data: { track_id: trackId, completed_revision: this.revision, replayed: false } });
    }
    if (path.endsWith("/working-composition/commit") && method === "POST") {
      const body = request.postDataJSON() as { expected_revision: number };
      const key = request.headers()["idempotency-key"] ?? null;
      this.commitPosts.push({ revision: body.expected_revision, key });
      const replay = key ? this.completions.get(key) : undefined;
      if (replay) return this.commitResponse(route, replay, true);
      if (this.rejectNextCommit) {
        const code = this.rejectNextCommit;
        this.rejectNextCommit = null;
        if (code === "WORKING_COMPOSITION_REVISION_CONFLICT") this.revision += 1;
        return this.error(route, code === "COMMIT_FAILED" ? 500 : 409, code);
      }
      if (body.expected_revision !== this.revision) {
        return this.error(route, 409, "WORKING_COMPOSITION_REVISION_CONFLICT");
      }
      if (this.clips.length === 0) return this.error(route, 409, "WORKING_COMPOSITION_EMPTY");
      this.snapshotVersion += 1;
      const completion = { snapshotId: `snapshot-${this.snapshotVersion}`, revision: this.revision + 1 };
      this.revision = completion.revision;
      this.baseSnapshotId = completion.snapshotId;
      this.selectedSnapshotId = completion.snapshotId;
      this.history.barrier();
      if (key) this.completions.set(key, completion);
      if (this.loseNextResponse) {
        this.loseNextResponse = false;
        return route.abort("connectionreset");
      }
      return this.commitResponse(route, completion, false);
    }
    return this.error(route, 404, "TEST_ROUTE_NOT_FOUND");
  }

  private working() {
    return {
      working_composition_id: workingId,
      project_id: projectId,
      base_composition_snapshot_id: this.baseSnapshotId,
      revision: this.revision,
      mix_settings: {},
      tracks: [{ track_id: trackId, track_type: "audio", name: this.trackName, track_order: 0 }],
      clips: this.clips,
      timeline_duration: this.clips.length ? "2.000" : "0",
    };
  }

  private commitResponse(
    route: Route,
    completion: { snapshotId: string; revision: number },
    replayed: boolean,
  ) {
    return this.ok(route, { data: {
      working_composition_id: workingId,
      composition_snapshot_id: completion.snapshotId,
      completed_revision: completion.revision,
      replayed,
    } }, 201);
  }

  private ok(route: Route, json: unknown, status = 200) { return route.fulfill({ status, json }); }
  private error(route: Route, status: number, code: string) {
    return route.fulfill({ status, json: { error: { code, message: code } } });
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("doha-studio-settings", JSON.stringify({
      state: { reducedMotion: true, onboardingCompleted: true }, version: 0,
    }));
  });
});

test("Commit freezes a new version and becomes an Undo/Redo history barrier", async ({ page }) => {
  const backend = new CommitBackend();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);

  const trackName = page.getByLabel("Commit Track Track 이름");
  await trackName.fill("Edited before commit");
  await trackName.blur();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();

  await expect(page.getByText(/새 버전으로 저장했습니다/)).toBeVisible();
  await expect(page.getByText(/revision 4/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  expect(backend.commitPosts).toHaveLength(1);
  expect(backend.commitPosts[0]).toMatchObject({ revision: 3 });
  expect(backend.commitPosts[0].key).toBeTruthy();
  expect(backend.baseSnapshotId).toBe("snapshot-2");
  expect(backend.selectedSnapshotId).toBe("snapshot-2");
  expect(backend.compositionReads).toBeGreaterThan(1);

  await page.keyboard.press("Control+Z");
  expect(backend.renamePosts).toBe(1);
  const nextName = page.getByLabel("Edited before commit Track 이름");
  await nextName.fill("First edit after commit");
  await nextName.blur();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  expect(pageErrors).toEqual([]);
});

test("Commit response loss replays the same snapshot and failure preserves history", async ({ page }) => {
  const backend = new CommitBackend();
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  const trackName = page.getByLabel("Commit Track Track 이름");
  await trackName.fill("History survives failure");
  await trackName.blur();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();

  backend.rejectNextCommit = "COMMIT_FAILED";
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  expect(backend.snapshotVersion).toBe(1);

  backend.loseNextResponse = true;
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByText(/새 버전으로 저장했습니다/)).toBeVisible();
  expect(backend.commitPosts).toHaveLength(3);
  expect(backend.commitPosts[1].key).toBe(backend.commitPosts[2].key);
  expect(backend.snapshotVersion).toBe(2);
  expect(backend.selectedSnapshotId).toBe("snapshot-2");
});

test("Empty commit is disabled and revision conflict reconciles without auto retry", async ({ page }) => {
  const backend = new CommitBackend();
  backend.clips = [];
  await backend.install(page);
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" })).toBeDisabled();
  await expect(page.getByText(/활성 Clip을 하나 이상 배치하면/)).toBeVisible();
  expect(backend.commitPosts).toHaveLength(0);

  backend.clips = [clip()];
  await page.reload();
  backend.rejectNextCommit = "WORKING_COMPOSITION_REVISION_CONFLICT";
  await page.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }).click();
  await expect(page.getByText(/revision 3/).first()).toBeVisible();
  expect(backend.commitPosts).toHaveLength(1);
  expect(backend.snapshotVersion).toBe(1);
});

function clip() {
  return {
    clip_id: clipId, track_id: trackId, source_asset_version_id: versionId,
    timeline_start: "0.000", source_in: "0.000", source_out: "2.000",
    source_duration: "2.000", timeline_duration: "2.000", loop_enabled: false, loop_phase: "0",
    gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null,
  };
}

function project() {
  return { id: projectId, title: "Composition Commit E2E", description: "stateful commit fixture", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z", job_count: 0, jobs: [] };
}

function composition(snapshotId: string, snapshotVersion: number) {
  return {
    state: "ready",
    project: { project_id: projectId, workspace_id: "workspace-1", title: "Composition Commit E2E", lifecycle_status: "active", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
    selection: { selected_snapshot_id: snapshotId, resolved_snapshot_id: snapshotId, resolution: "selected", is_current: true },
    snapshot: { composition_snapshot_id: snapshotId, project_id: projectId, snapshot_version: snapshotVersion, processing_chain_id: null, provider_versions: {}, model_manifest_ids: {}, created_at: "2026-08-29T00:00:00Z" },
    items: [{ snapshot_item_id: "item-1", item_role: "mix", sort_order: 0, asset_version: { asset_version_id: versionId, asset_id: "asset-1", version_number: 1, version_origin: "fixture", parent_asset_version_id: null, processing_chain_id: null, provider_id: null, model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-29T00:00:00Z" }, artifacts: [{ artifact_id: artifactId, asset_version_id: versionId, artifact_kind: "audio", media_type: "audio/wav", size_bytes: silentWav.length, checksum_algorithm: "sha256", artifact_checksum: "fixture", producer_type: "test", producer_id: null, run_id: null, retention_status: "active", created_at: "2026-08-29T00:00:00Z", content_url: `/api/v1/artifacts/${artifactId}/content`, download_url: null }] }],
    track_projections: [], section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {}, lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function mediaSource() {
  return { asset_version_id: versionId, artifact_id: artifactId, media_type: "audio/wav", size_bytes: silentWav.length, artifact_checksum: "fixture", duration_seconds: "2", content_url: `/api/v1/artifacts/${artifactId}/content` };
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
