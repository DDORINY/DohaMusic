import { defineConfig, devices } from "@playwright/test";

const runtimeRoot = "D:/Temp/DohaMusic/fullstack-export-runtime";
const seedFile = "D:/Temp/DohaMusic/fullstack-export-seed.json";

process.env.DOHA_E2E_SEED_FILE = seedFile;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: /fullstack-export\.spec\.ts/,
  timeout: 90_000,
  retries: 0,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3200", trace: "retain-on-failure" },
  webServer: [
    {
      command: "cd .. && D:\\DohaMusic\\.venv\\Scripts\\python.exe -m backend.tests.fullstack_export_server",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DOHA_E2E_RUNTIME_ROOT: runtimeRoot,
        DOHA_E2E_ALLOWED_PARENT: "D:/Temp/DohaMusic",
        DOHA_E2E_SEED_FILE: seedFile,
      },
    },
    {
      command: "npm run start -- --hostname 127.0.0.1 --port 3200",
      url: "http://127.0.0.1:3200",
      reuseExistingServer: false,
      timeout: 120_000,
      env: { DOHAMUSIC_API_ORIGIN: "http://127.0.0.1:8000" },
    },
  ],
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
