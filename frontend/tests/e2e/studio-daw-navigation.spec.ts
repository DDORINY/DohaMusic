import { expect, test, type Page, type Route } from "@playwright/test";

const projectId = "project-navigation";

test("Onboarding 닫기는 즉시 닫히고 reload와 다시 보기 뒤에도 focus 계약을 지킨다", async ({ page }) => {
  const runtime = observeRuntime(page);
  await freshOnboarding(page);

  const dialog = page.getByRole("dialog", { name: "DohaMusic 시작하기" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "닫기" })).toBeFocused();
  await page.getByRole("button", { name: "닫기" }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("main")).toBeVisible();

  await page.reload();
  await expect(dialog).toBeHidden();
  await page.goto(new URL("/settings", page.url()).toString());
  const reopen = page.getByRole("button", { name: "다시 보기" });
  await reopen.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(reopen).toBeFocused();
  expect(runtime.pageErrors).toEqual([]);
  expect(runtime.consoleErrors).toEqual([]);
});

test("첫 음악 만들기는 Onboarding을 완료하고 /studio로 이동한다", async ({ page }) => {
  const runtime = observeRuntime(page);
  await freshOnboarding(page);

  await page.getByRole("link", { name: "첫 음악 만들기" }).click();
  await expect(page).toHaveURL(/\/studio$/);
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.getByText("새 음악 생성")).toBeVisible();
  expect(runtime.pageErrors).toEqual([]);
  expect(runtime.consoleErrors).toEqual([]);
});

test("Studio와 DAW navigation에서 명시적 Project 선택 후 Timeline과 Editor에 접근한다", async ({ page }, testInfo) => {
  const runtime = observeRuntime(page);
  await page.addInitScript(() => localStorage.setItem(
    "doha-studio-settings",
    JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }),
  ));
  await installNavigationBackend(page);
  await page.goto("/studio");

  await expect(page.getByText("새 음악 생성")).toBeVisible();
  await expect(page.getByRole("button", { name: "가사 준비하기" })).toBeEnabled();
  if (testInfo.project.name === "mobile") {
    const mobileNavigation = page.getByRole("navigation", { name: "모바일 메뉴" });
    await expect(mobileNavigation.getByRole("link")).toHaveCount(5);
    expect(await mobileNavigation.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await mobileNavigation.getByRole("link").evaluateAll((links) => links.every((link) => {
      const bounds = link.getBoundingClientRect();
      return bounds.width >= 44 && bounds.height >= 44;
    }))).toBe(true);
    await mobileNavigation.getByRole("link", { name: "DAW 편집" }).click();
  } else {
    await page.getByRole("link", { name: "DAW에서 편집하기" }).click();
  }

  await expect(page).toHaveURL(/\/projects\?mode=daw$/);
  await expect(page.getByRole("heading", { name: "DAW에서 편집할 프로젝트 선택" })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: testInfo.project.name === "mobile" ? "모바일 메뉴" : "주요 메뉴" });
  await expect(navigation.getByRole("link", { name: "DAW 편집" })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("link", { name: "프로젝트" })).not.toHaveAttribute("aria-current");
  await page.getByRole("link", { name: "DAW 열기" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(page.getByRole("heading", { name: "곡 편집" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Composition Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Track / Clip Editor" })).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
  expect(runtime.pageErrors).toEqual([]);
  expect(runtime.consoleErrors).toEqual([]);
});

test("Project navigation은 일반 목록과 exact Project route를 별도로 제공한다", async ({ page }, testInfo) => {
  const runtime = observeRuntime(page);
  await page.addInitScript(() => localStorage.setItem(
    "doha-studio-settings",
    JSON.stringify({ state: { reducedMotion: true, onboardingCompleted: true }, version: 0 }),
  ));
  await installNavigationBackend(page);
  await page.goto("/studio");

  const navigation = page.getByRole("navigation", { name: testInfo.project.name === "mobile" ? "모바일 메뉴" : "주요 메뉴" });
  await navigation.getByRole("link", { name: "프로젝트" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("heading", { name: "프로젝트", exact: true })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "프로젝트" })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("link", { name: "DAW 편집" })).not.toHaveAttribute("aria-current");
  await page.getByRole("link", { name: "프로젝트 열기" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(page.getByRole("heading", { name: "곡 편집" })).toBeVisible();
  expect(runtime.pageErrors).toEqual([]);
  expect(runtime.consoleErrors).toEqual([]);
});

async function freshOnboarding(page: Page) {
  await page.goto(process.env.ONBOARDING_TARGET ?? "/");
  await page.evaluate(() => localStorage.removeItem("doha-studio-settings"));
  await page.reload();
}

function observeRuntime(page: Page) {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("Failed to load resource")) {
      consoleErrors.push(message.text());
    }
  });
  return { pageErrors, consoleErrors };
}

async function installNavigationBackend(page: Page) {
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/backend/health") return ok(route, { status: "ok" });
    if (path === "/backend/api/projects") return ok(route, [{
      id: projectId, title: "Navigation Project", description: "DAW navigation fixture",
      created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z", job_count: 0,
    }]);
    if (path === `/backend/api/v1/projects/${projectId}`) return ok(route, { data: {
      project_id: projectId, title: "Navigation Project", description: "DAW navigation fixture",
      created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z", job_count: 0, jobs: [],
    } });
    if (path === `/backend/api/v1/projects/${projectId}/composition`) return ok(route, { data: composition() });
    if (path === `/backend/api/v1/projects/${projectId}/working-composition`) return ok(route, { data: {
      working_composition_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      project_id: projectId, base_composition_snapshot_id: "snapshot-navigation", revision: 0,
      mix_settings: {}, tracks: [], clips: [], timeline_duration: "0.000",
    } });
    return route.fulfill({ status: 404, json: { error: { code: "TEST_ROUTE_NOT_FOUND" } } });
  });
}

function composition() {
  return {
    state: "ready",
    project: {
      project_id: projectId, workspace_id: "workspace-navigation", title: "Navigation Project",
      lifecycle_status: "active", created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z",
    },
    selection: {
      selected_snapshot_id: "snapshot-navigation", resolved_snapshot_id: "snapshot-navigation",
      resolution: "selected", is_current: true,
    },
    snapshot: {
      composition_snapshot_id: "snapshot-navigation", project_id: projectId, snapshot_version: 1,
      created_at: "2026-09-03T00:00:00Z", processing_chain_id: null,
      provider_versions: {}, model_manifest_ids: {},
    },
    items: [], track_projections: [],
    section_projection: { availability: "not_available", items: [] },
    mix_settings_snapshot: {},
    lineage: { processing_chain_id: null, provider_versions: {}, model_manifest_ids: {} },
  };
}

function ok(route: Route, json: unknown) {
  return route.fulfill({ status: 200, json });
}
