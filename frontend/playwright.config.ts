import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testIgnore: /(?:voice-enrollment-validation|fullstack-export)\.spec\.ts/,
  use: { baseURL: "http://127.0.0.1:3200", trace: "retain-on-failure" },
  webServer: { command: "npm run start -- --hostname 127.0.0.1 --port 3200", url: "http://127.0.0.1:3200", reuseExistingServer: false, timeout: 120_000 },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } }
  ],
});
