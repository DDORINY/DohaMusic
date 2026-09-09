import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const testRoot = resolve(process.cwd(), ".test-tmp", "fullstack-export");
const runtimeRoot = process.env.DOHA_E2E_RUNTIME_ROOT ?? resolve(testRoot, "runtime");
const allowedParent = process.env.DOHA_E2E_ALLOWED_PARENT ?? testRoot;
const seedFile = process.env.DOHA_E2E_SEED_FILE ?? resolve(testRoot, "seed.json");
const python = process.env.DOHA_E2E_PYTHON ?? "python";

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
      command: `cd .. && ${python} -m backend.tests.fullstack_export_server`,
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DOHA_E2E_RUNTIME_ROOT: runtimeRoot,
        DOHA_E2E_ALLOWED_PARENT: allowedParent,
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
