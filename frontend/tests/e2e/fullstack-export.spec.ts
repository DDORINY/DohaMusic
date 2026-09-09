import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

interface SeedAuthority { project_id: string; snapshot_id: string }

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem(
    "doha-studio-settings",
    JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }),
  ));
});

test("real production runner Export를 Artifact WAV download까지 완료한다", async ({ page }, testInfo) => {
  const seed = JSON.parse(readFileSync(process.env.DOHA_E2E_SEED_FILE!, "utf8")) as SeedAuthority;
  const exportPosts: string[] = [];
  const jobReads: string[] = [];
  const artifactGets: string[] = [];
  const failedRequests: string[] = [];
  const consoleErrors: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path === "/backend/api/v1/jobs") exportPosts.push(path);
    if (request.method() === "GET" && path.includes("/backend/api/v1/jobs/")) jobReads.push(path);
    if (request.method() === "GET" && path.includes("/backend/api/v1/artifacts/")) artifactGets.push(path);
  });
  page.on("requestfailed", (request) => failedRequests.push(request.url()));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });

  await page.goto(`/projects/${seed.project_id}`);
  const action = page.getByRole("button", { name: "Export WAV" });
  await expect(action).toBeVisible();
  const workspaceBefore = await page.locator(".main-workspace").boundingBox();
  await action.dblclick();
  await expect(page.getByLabel("현재 Snapshot 내보내기").getByText("완료", { exact: true }))
    .toBeVisible({ timeout: 60_000 });
  expect(exportPosts).toHaveLength(1);
  expect(jobReads.length).toBeGreaterThan(0);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download exported WAV" }).click();
  const download = await downloadPromise;
  const target = testInfo.outputPath("export.wav");
  await download.saveAs(target);
  const wav = readFileSync(target);
  expect(wav.subarray(0, 4).toString("ascii")).toBe("RIFF");
  expect(wav.subarray(8, 12).toString("ascii")).toBe("WAVE");
  expect(wav.readUInt16LE(20)).toBe(1);
  expect(wav.readUInt16LE(22)).toBe(2);
  expect(wav.readUInt32LE(24)).toBe(48_000);
  expect(wav.readUInt16LE(34)).toBe(16);
  expect((wav.length - 44) / 4).toBeGreaterThan(0);
  expect(download.suggestedFilename().toLowerCase()).toMatch(/\.wav$/);
  expect(artifactGets).toHaveLength(1);
  expect(failedRequests).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  expect(await page.evaluate(() => document.body.scrollHeight <= window.innerHeight)).toBe(true);
  const workspaceAfter = await page.locator(".main-workspace").boundingBox();
  expect(workspaceAfter?.height).toBe(workspaceBefore?.height);
  await expect(page.getByRole("button", { name: "Export WAV" })).toBeEnabled();
});
