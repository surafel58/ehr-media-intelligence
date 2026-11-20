import { defineConfig, devices } from "@playwright/test"

// Pick a browser:
//   PW_BROWSER_PATH=path/to/brave.exe   any installed Chromium browser (Brave, Chrome, Edge)
//   PW_CHANNEL=msedge                   an installed Chrome or Edge channel
//   neither                             Playwright's own Chromium (run `pnpm exec playwright install chromium` once)
const browserPath = process.env.PW_BROWSER_PATH
const channel = process.env.PW_CHANNEL
const browser = browserPath ? { launchOptions: { executablePath: browserPath } } : channel ? { channel } : {}

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1, // one shared backend, keep runs predictable
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 900 }, ...browser },
    },
  ],
  webServer: [
    {
      command: "uv run python -m ehr_platform.api",
      cwd: "..",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command: "pnpm dev --host 127.0.0.1 --port 5173 --strictPort",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
    },
  ],
})
