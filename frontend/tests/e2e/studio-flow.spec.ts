import { test, expect, type Page } from "@playwright/test";
const uuid = "11111111-1111-1111-1111-111111111111";

function silentWavFixture() {
  const frameCount = 480;
  const dataSize = frameCount * 2 * 2;
  const wav = Buffer.alloc(44 + dataSize);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(36 + dataSize, 4);
  wav.write("WAVE", 8);
  wav.write("fmt ", 12);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(2, 22);
  wav.writeUInt32LE(48_000, 24);
  wav.writeUInt32LE(48_000 * 2 * 2, 28);
  wav.writeUInt16LE(2 * 2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(dataSize, 40);
  return wav;
}

const silentWav = silentWavFixture();
const voiceProfile = {
  id: uuid,
  name: "Doha Voice",
  display_filename: "voice-reference.wav",
  mime_type: "audio/wav",
  size_bytes: 320044,
  duration_seconds: 10,
  sample_rate: 16000,
  channels: 1,
  consent_confirmed: true,
  consent_text_version: "v1",
  status: "READY",
  quality_warnings: [],
  created_at: "2026-07-31T00:00:00Z",
  updated_at: "2026-07-31T00:00:00Z",
};
const pipeline = {
  id: "job-001",
  project_id: "project-001",
  voice_profile_id: uuid,
  status: "COMPLETED",
  current_step: "completed",
  progress_percent: 100,
  prompt: "새벽 도시 R&B",
  lyrics: "[Verse]\n빛나는 거리",
  genre: "R&B",
  duration_seconds: 30,
  seed: null,
  pipeline_version: "mock-v1",
  result_metadata: { provider: "mock", sample_rate: 48000 },
  failed_step: null,
  error_code: null,
  error_message: null,
  created_at: "2026-07-31T00:00:00Z",
  updated_at: "2026-07-31T00:00:01Z",
  completed_at: "2026-07-31T00:00:01Z",
  cancel_requested_at: null,
  cancelled_at: null,
  retry_of_job_id: null,
  can_cancel: false,
  can_retry: false,
  audio_analysis: {
    audio_analysis_version: "1.0",
    analysis_status: "COMPLETED",
    quality: {
      duration_seconds: 30,
      sample_rate: 48000,
      channels: 2,
      sample_peak_dbfs: -1.2,
      clipping_detected: false,
      clipping_sample_count: 0,
      clipping_ratio: 0,
      integrated_lufs: -13.8,
    },
    tempo: {
      version: "1.0",
      status: "COMPLETED",
      requested_bpm: 120,
      detected_bpm: 119.8,
      confidence: 0.91,
      bpm_error: -0.2,
      absolute_bpm_error: 0.2,
      half_time_candidate: false,
      double_time_candidate: false,
    },
    hook: {
      hook_analysis_version: "1.0",
      status: "COMPLETED",
      candidate: {
        start_seconds: 12.1,
        end_seconds: 27,
        duration_seconds: 14.9,
        confidence: 0.74,
        selection_strategy: "energy_repetition",
      },
    },
    warnings: [],
  },
};
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("doha-studio-settings", JSON.stringify({ state: { reducedMotion: null, onboardingCompleted: true }, version: 0 })));
});
async function mockBackend(
  page: Page,
  onPipelineCreate?: (body: Record<string, unknown>) => void,
) {
  let latestGenerationOptions: Record<string, unknown> | null = null;
  await page.route("**/backend/health", (r) =>
    r.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/backend/api/lyrics/validate", (r) =>
    r.fulfill({
      json: {
        valid: true,
        normalized_lyrics: "",
        sections: [],
        warnings: [],
        errors: [],
        character_count: 10,
        line_count: 2,
        section_count: 1,
        repetition_ratio: 0,
      },
    }),
  );
  await page.route("**/backend/api/voice-profiles", (route) =>
    route.fulfill({ json: [voiceProfile] }),
  );
  await page.route("**/backend/api/voice-profiles/upload", (route) =>
    route.fulfill({ status: 201, json: voiceProfile }),
  );
  await page.route("**/backend/api/pipelines", (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    latestGenerationOptions = body.generation_options as Record<string, unknown> | null;
    onPipelineCreate?.(body);
    return route.fulfill({ status: 202, json: pipeline });
  });
  await page.route("**/backend/api/pipelines/job-001", (r) =>
    r.fulfill({ json: pipeline }),
  );
  await page.route("**/backend/api/pipelines/job-001/files", (r) =>
    r.fulfill({
      json: [
        {
          id: "file-1",
          job_id: "job-001",
          file_type: "final",
          mime_type: "audio/wav",
          content_available: true,
          download_available: true,
          content_url: "/api/pipelines/job-001/files/file-1/content",
          download_url: "/api/pipelines/job-001/files/file-1/download",
          created_at: "2026-07-31T00:00:01Z",
        },
      ],
    }),
  );
  await page.route("**/backend/api/pipelines/job-001/files/file-1/content", (r) =>
    r.fulfill({ status: 200, contentType: "audio/wav", body: silentWav }),
  );
  await page.route("**/backend/api/pipelines/job-001/files/file-1/download", (r) =>
    r.fulfill({
      status: 200,
      contentType: "audio/wav",
      headers: { "Content-Disposition": 'attachment; filename="doha-job-final.wav"' },
      body: silentWav,
    }),
  );
  await page.route("**/backend/api/history**", (route) =>
    route.fulfill({ json: [{ job_id: "job-001", project_id: "project-001", title: "새벽 도시 R&B", status: "COMPLETED", created_at: pipeline.created_at, duration: 30, voice_profile_name: "Doha Voice", has_audio: true, can_cancel: false, can_retry: false, retry_of_job_id: null, generation_options: latestGenerationOptions, audio_analysis: pipeline.audio_analysis }] }),
  );
  await page.route("**/backend/api/projects", (route) =>
    route.fulfill({ json: [{ id: "project-001", title: "Default Project", description: null, created_at: pipeline.created_at, updated_at: pipeline.updated_at, job_count: 1 }] }),
  );
  await page.route("**/backend/api/v1/projects/project-001", (route) =>
    route.fulfill({ json: { data: { project_id: "project-001", title: "Default Project", description: null, created_at: pipeline.created_at, updated_at: pipeline.updated_at } } }),
  );
}
test("History에서 Result와 Player로 다시 이동한다", async ({ page }) => {
  await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () { this.dispatchEvent(new Event("play")); }; });
  await mockBackend(page);
  await page.goto("/history");
  await expect(page.getByRole("heading", { name: "새벽 도시 R&B" })).toBeVisible();
  await page.locator(".history-row").getByRole("button", { name: "재생" }).click();
  await expect(page.locator("audio")).toHaveAttribute("src", "/backend/api/pipelines/job-001/files/file-1/content");
  await page.getByRole("link", { name: "열기" }).click();
  await expect(page).toHaveURL(/\/result\/job-001/);
  await expect(page.getByRole("link", { name: "WAV 다운로드" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "오디오 분석" })).toBeVisible();
  await expect(page.getByText("-13.8 LUFS")).toBeVisible();
  await expect(page.getByText("감지되지 않음")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tempo 분석" })).toBeVisible();
  await expect(page.getByText("예상 템포는 약 119.8 BPM입니다.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "후렴 후보" })).toBeVisible();
  await expect(page.getByText("00:12~00:27")).toBeVisible();
  await expect(page.getByRole("link", { name: "DAW에서 편집" })).toHaveAttribute("href", "/projects/project-001");
});

test("PARTIAL 분석과 구형 Result fallback을 안전하게 표시한다", async ({ page }) => {
  await mockBackend(page);
  await page.route("**/backend/api/pipelines/job-001", (route) =>
    route.fulfill({ json: { ...pipeline, audio_analysis: { ...pipeline.audio_analysis, analysis_status: "PARTIAL", quality: { ...pipeline.audio_analysis.quality, integrated_lufs: null }, warnings: ["통합 음량을 분석하지 못했습니다."] } } }),
  );
  await page.goto("/result/job-001");
  await expect(page.getByText("일부 항목을 분석하지 못했습니다.")).toBeVisible();
  await expect(page.getByText("통합 음량을 분석하지 못했습니다.")).toBeVisible();

  await page.route("**/backend/api/pipelines/job-001", (route) =>
    route.fulfill({ json: { ...pipeline, project_id: null, audio_analysis: null } }),
  );
  await page.reload();
  await expect(page.getByText("이 음원에는 품질 분석 정보가 없습니다.")).toBeVisible();
  await expect(page.getByRole("link", { name: "DAW에서 편집" })).toHaveCount(0);
});

test("History와 Project에서 분석 상태를 간결하게 표시한다", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/history");
  await expect(page.getByText(/분석 완료 · 클리핑 없음 · -13.8 LUFS/)).toBeVisible();
  await expect(page.getByText("Tempo 완료")).toBeVisible();
  await expect(page.getByText("후렴 후보 있음")).toBeVisible();
  await expect(page.getByText(/119\.8 BPM/)).toHaveCount(0);
  await expect(page.getByText(/00:12~00:27/)).toHaveCount(0);
  await page.goto("/projects/project-001");
  await expect(page.getByRole("heading", { name: "Default Project", exact: true })).toBeVisible();
  await expect(page.getByText("설명 없음", { exact: true })).toBeVisible();
});
test("Landing에서 결과 metadata까지 핵심 흐름을 완료한다", async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {
      this.dispatchEvent(new Event("play"));
    };
    HTMLMediaElement.prototype.pause = function () {
      this.dispatchEvent(new Event("pause"));
    };
  });
  await mockBackend(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /당신의 이야기가/ }),
  ).toBeVisible();
  await page.getByRole("link", { name: /첫 곡 만들기/ }).click();
  await page.getByLabel("노래 설명").fill("새벽 도시 R&B");
  await page.getByRole("button", { name: /1절 중심/ }).click();
  await page.getByRole("button", { name: "가사 준비하기" }).click();
  await page.getByRole("textbox", { name: "가사" }).fill("[Verse]\n빛나는 거리");
  await page.getByRole("button", { name: "작성 내용 확인" }).click();
  await expect(page.getByText("가사 구성을 확인했습니다.")).toBeVisible();
  await page.getByRole("button", { name: "내 목소리 선택" }).click();
  await page.getByRole("radio", { name: /Doha Voice/ }).click();
  await page.getByRole("button", { name: "최종 확인" }).click();
  await expect(page.getByText("60초")).toBeVisible();
  await page.getByRole("button", { name: "음악 만들기" }).click();
  await expect(page).toHaveURL(/\/result\/job-001/);
  await expect(
    page.getByRole("heading", { name: "완성 정보" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "WAV 다운로드" }),
  ).toHaveAttribute(
    "href",
    "/backend/api/pipelines/job-001/files/file-1/download",
  );
  await expect(page.locator("audio")).toHaveAttribute(
    "src",
    "/backend/api/pipelines/job-001/files/file-1/content",
  );
  await page.locator(".result-hero").getByRole("button", { name: "재생" }).click();
  const resultPause = page.locator(".result-hero").getByRole("button", {
    name: "일시정지",
  });
  await expect(resultPause).toBeVisible();
  await resultPause.click();
  await expect(
    page.locator(".result-hero").getByRole("button", { name: "재생" }),
  ).toBeVisible();
  await expect(page.getByText("C:/internal/final.wav")).toHaveCount(0);
});

test("K-POP Structured Options를 Pipeline과 History에 보존한다", async ({ page }) => {
  let pipelineRequest: Record<string, unknown> | undefined;
  await mockBackend(page, (body) => {
    pipelineRequest = body;
  });
  await page.goto("/studio");
  await page.getByLabel("노래 설명").fill("새벽 도시 R&B");
  await page.getByRole("button", { name: /K-POP Easy Listening/ }).click();
  await page.getByText("K-POP 고급 설정").click();
  await page.getByLabel("목표 BPM").fill("112");
  await page.getByLabel("한국어 비율 (%)").fill("65");
  await page.getByLabel("영어 비율 (%)").fill("35");
  await page.getByLabel("곡 분위기·콘셉트").fill("midnight_warm");
  await page.getByLabel("후렴 Hook").fill("Moonlight Heart");
  await page.getByLabel("Hook 방식").selectOption("chant");
  await page.getByLabel("Hook 반복 횟수").fill("4");
  await page.getByRole("button", { name: "Dance Break" }).click();
  await page.getByRole("button", { name: "가사 준비하기" }).click();
  await page.getByRole("textbox", { name: "가사" }).fill("[Verse]\n빛나는 거리");
  await page.getByRole("button", { name: "작성 내용 확인" }).click();
  await page.getByRole("button", { name: "내 목소리 선택" }).click();
  await page.getByRole("radio", { name: /Doha Voice/ }).click();
  await page.getByRole("button", { name: "최종 확인" }).click();
  await expect(page.getByText("112 BPM (Prompt 목표)")).toBeVisible();
  await expect(page.getByText("Moonlight Heart")).toBeVisible();
  await page
    .getByRole("button", { name: "음악 만들기" })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect.poll(() => pipelineRequest).toBeTruthy();

  expect(pipelineRequest).toMatchObject({
    prompt: "새벽 도시 R&B",
    genre: "kpop_easy_listening",
    generation_options: {
      preset_id: "kpop_easy_listening",
      requested_bpm: 112,
      language_ratio: { ko: 65, en: 35 },
      hook: { phrase: "Moonlight Heart", style: "chant", repeat_count: 4 },
      include_post_chorus: true,
      include_dance_break: true,
      vocal_energy: "low",
      concept: "midnight_warm",
    },
  });
  expect(pipelineRequest).not.toHaveProperty("preset_id");
  await page.goto("/history");
  await expect(page.getByText(/K-POP Easy Listening · 112 BPM 목표 · Hook: Moonlight Heart/)).toBeVisible();
});
test("Voice WAV를 등록하고 목록에서 선택한다", async ({
  page,
}, testInfo) => {
  await mockBackend(page);
  await page.goto("/voice");
  await page.getByText("빠른 WAV 등록 (기존 방식)").click();
  await page.getByLabel("빠른 등록 이름").fill("Doha Voice");
  await page.getByLabel("목소리 파일").setInputFiles({
    name: "voice-reference.wav",
    mimeType: "audio/wav",
    buffer: Buffer.from("RIFFmock-dataWAVE"),
  });
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "내 목소리 등록" }).click();
  await expect(page.getByText("Doha Voice").first()).toBeVisible();
  await expect(page.getByText(/음악 만들기에 선택됨/)).toBeVisible();
  await expect(page.getByLabel("서버 참조 파일 경로")).toHaveCount(0);
  if (testInfo.project.name === "mobile") {
    await expect(
      page.getByRole("navigation", { name: "모바일 메뉴" }),
    ).toBeVisible();
  } else {
    await expect(
      page.getByRole("navigation", { name: "주요 메뉴" }),
    ).toBeVisible();
  }
});

test("Template 가사는 의미 기반 revision을 활성화하지 않는다", async ({
  page,
}) => {
  await mockBackend(page);
  await page.route("**/backend/api/lyrics", (route) =>
    route.fulfill({
      status: 201,
      json: {
        id: "lyrics-1",
        parent_id: null,
        version: 1,
        revision_instruction: null,
        source_hash: null,
        result_hash: null,
        title: "Template 가사",
        language: "ko",
        topic: "밤",
        genre: "R&B",
        mood: "따뜻한",
        keywords: [],
        structure: ["verse", "chorus"],
        sections: [],
        full_text: "[Verse]\n빛나는 거리",
        provider: "template",
        model_name: "template",
        model_version: null,
        status: "GENERATED",
        metadata: { capabilities: { revision: false } },
        created_at: "2026-07-31T00:00:00Z",
        updated_at: "2026-07-31T00:00:00Z",
      },
    }),
  );

  await page.goto("/lyrics");
  await page.getByLabel("주제").fill("밤");
  await page.getByRole("button", { name: "가사 만들어보기" }).click();

  await expect(page.getByText(/AI에게 다시 고쳐달라고 요청하는 기능은 준비 중/)).toBeVisible();
  await expect(page.getByLabel("수정 지시")).toHaveCount(0);
});

test("네트워크 오류에서도 Job ID를 보존하고 수동 재조회를 제공한다", async ({
  page,
}) => {
  await page.route("**/backend/api/pipelines/network-job", (route) =>
    route.abort("failed"),
  );

  await page.goto("/generation/network-job");

  await expect(page.getByText("진행 상태를 불러오지 못했습니다")).toBeVisible();
  await expect(page.getByText("음악 생성 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 확인" })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/\/generation\/network-job/);
});

test("진행 중인 음악을 확인 후 취소하고 취소 완료를 표시한다", async ({ page }) => {
  await mockBackend(page);
  let cancelled = false;
  const running = { ...pipeline, id: "cancel-job", status: "GENERATING", current_step: "music_started", progress_percent: 20, completed_at: null, can_cancel: true };
  await page.route("**/backend/api/pipelines/cancel-job", (route) => route.fulfill({ json: cancelled ? { ...running, status: "CANCELLED", current_step: "cancelled", can_cancel: false, can_retry: true, cancelled_at: "2026-07-31T00:01:00Z" } : running }));
  await page.route("**/backend/api/pipelines/cancel-job/cancel", (route) => { cancelled = true; route.fulfill({ json: { job_id: "cancel-job", status: "CANCEL_REQUESTED", cancel_requested_at: "2026-07-31T00:00:30Z", cancelled_at: null, message: "음악 만들기 취소를 요청했습니다." } }); });
  await page.goto("/generation/cancel-job");
  await page.getByRole("button", { name: "음악 만들기 취소" }).click();
  await expect(page.getByRole("dialog", { name: "음악 만들기를 취소할까요?" })).toBeVisible();
  await page.getByRole("button", { name: "취소하기" }).click();
  await expect(page.getByRole("heading", { name: "음악 만들기가 취소되었습니다" })).toBeVisible();
  await expect(page.getByRole("button", { name: "같은 설정으로 다시 만들기" })).toBeVisible();
});

test("실패한 음악을 새 Job으로 다시 만들고 생성 화면으로 이동한다", async ({ page }) => {
  await mockBackend(page);
  const failed = { ...pipeline, id: "failed-job", status: "FAILED", current_step: "failed", progress_percent: 20, completed_at: "2026-07-31T00:01:00Z", error_message: "음악을 완성하지 못했습니다.", can_retry: true };
  const retried = { ...pipeline, id: "retry-job", status: "PENDING", current_step: "queued", progress_percent: 0, completed_at: null, retry_of_job_id: "failed-job", can_cancel: true };
  await page.route("**/backend/api/pipelines/failed-job", (route) => route.fulfill({ json: failed }));
  await page.route("**/backend/api/pipelines/failed-job/retry", (route) => route.fulfill({ status: 202, json: { source_job_id: "failed-job", job: retried } }));
  await page.route("**/backend/api/pipelines/retry-job", (route) => route.fulfill({ json: retried }));
  await page.goto("/generation/failed-job");
  await page.getByRole("button", { name: "같은 설정으로 다시 만들기" }).click();
  await expect(page).toHaveURL(/\/generation\/retry-job/);
  await expect(page.getByRole("heading", { name: "곧 시작합니다" })).toBeVisible();
});
