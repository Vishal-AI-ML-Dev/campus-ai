import { defineConfig } from "@playwright/test";

/**
 * Campus-AI frontend smoke test config.
 *
 * Targets default to LOCAL services only; no deployment URL is baked in:
 *   FRONTEND_URL  (default http://localhost:3000)
 *   API_URL       (default http://localhost:8000, read by the specs)
 *
 * To test another environment you own, set both explicitly, e.g. PowerShell:
 *   $env:FRONTEND_URL="https://<your-frontend>"; $env:API_URL="https://<your-api>"
 *
 * Use `localhost` for both locally (auth cookies are SameSite=Lax).
 */
export default defineConfig({
  testDir: "./e2e",
  // One role test opens up to 16 pages, so the per-test budget is generous.
  timeout: 180_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.FRONTEND_URL || "http://localhost:3000",
    headless: true,
    ignoreHTTPSErrors: true,
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
  },
});
