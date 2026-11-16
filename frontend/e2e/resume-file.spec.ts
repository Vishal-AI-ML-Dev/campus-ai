import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Focused UI tests for the owner-only resume PDF flow on /dashboard/resume
 * (Phase 5T).
 *
 * Fully MOCKED: every backend call, the storage upload PUT and the resume AI
 * are intercepted with page.route(). No backend, no real storage, no AI
 * provider, no real accounts or data. Signed URLs are fake, only compared,
 * never printed. Real storage and real AI remain UNTESTED by this spec.
 */

const API_PREFIX = (process.env.API_PREFIX ?? "/v1").replace(/\/$/, "");
const api = (path: string) => `**${API_PREFIX}${path}`;
const STORAGE = "https://storage.mock.invalid";
const PATH_A = `5/resume/7/${"a".repeat(32)}-cv.pdf`;
const FAKE_DOWNLOAD = `${STORAGE}/download/fake-signed`;

type Version = {
  id: number;
  title: string;
  target_role: string | null;
  provider: string | null;
  is_primary: boolean;
  created_at: string;
  preview: string;
  file_path: string | null;
};

const version = (id: number, title: string, file_path: string | null = null): Version => ({
  id,
  title,
  target_role: null,
  provider: "mock",
  is_primary: false,
  created_at: "2030-01-01T00:00:00Z",
  preview: "# Synthetic resume",
  file_path,
});

function alert(page: Page) {
  return page.getByRole("alert").filter({ hasText: /\S/ });
}

async function mockBackground(page: Page) {
  await page.route(`**${API_PREFIX}/**`, (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
}

async function mockUser(page: Page, role: "student" | "teacher") {
  await page.route(api("/auth/me"), (route) =>
    route.fulfill({
      json: { id: 7, email: "synthetic@example.test", full_name: "Synthetic", role, is_active: true },
    }),
  );
}

/** Stateful versions list: GET returns the current state. */
async function mockVersions(page: Page, state: { versions: Version[] }) {
  await page.route(api("/resume/versions"), (route) => route.fulfill({ json: state.versions }));
}

/** MOCKED storage: answers CORS preflight + the direct PUT upload. */
async function mockStorage(page: Page, puts: string[]) {
  await page.route(`${STORAGE}/**`, (route) => {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "PUT, OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
    };
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    puts.push(route.request().method());
    return route.fulfill({ status: 200, headers: cors, body: "" });
  });
}

async function stubWindowOpen(page: Page) {
  await page.addInitScript(() => {
    (window as unknown as { __opened: string[] }).__opened = [];
    window.open = ((u?: string | URL) => {
      (window as unknown as { __opened: string[] }).__opened.push(String(u));
      return null;
    }) as typeof window.open;
  });
}

const pdf = { name: "cv.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 synthetic") };

test.describe("resume PDF (owner only, mocked)", () => {
  test("student uploads and attaches a PDF via the secure API flow", async ({ page }) => {
    const state = { versions: [version(1, "Main")] };
    const sent: { upload?: unknown; attach?: unknown } = {};
    const puts: string[] = [];
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, state);
    await mockStorage(page, puts);
    await page.route(api("/files/sign-upload"), async (route) => {
      sent.upload = route.request().postDataJSON();
      await route.fulfill({ json: { path: PATH_A, upload_url: `${STORAGE}/upload/x`, token: "mock" } });
    });
    await page.route(api("/resume/versions/1/file"), async (route) => {
      expect(route.request().method()).toBe("PUT");
      sent.attach = route.request().postDataJSON();
      state.versions = [version(1, "Main", PATH_A)];
      await route.fulfill({ json: { ...state.versions[0], markdown: "# Synthetic resume" } });
    });

    await page.goto("/dashboard/resume");
    await expect(page.getByText("Resume PDF: none attached")).toBeVisible();
    await page.getByLabel("Attach PDF for Main").setInputFiles(pdf);

    await expect(page.getByRole("status").filter({ hasText: 'PDF attached to "Main".' })).toBeVisible();
    await expect(page.getByRole("button", { name: "Download PDF for Main" })).toBeVisible();
    await expect(page.getByText("Resume PDF: attached (only you can open it)")).toBeVisible();
    expect(sent.upload).toEqual({ filename: "cv.pdf", kind: "resume" });
    expect(sent.attach).toEqual({ path: PATH_A }); // no owner/tenant ids sent
    expect(puts).toEqual(["PUT"]);
  });

  test("owner downloads via a just-in-time signed URL that is never stored", async ({ page }) => {
    const state = { versions: [version(2, "Backend CV", PATH_A)] };
    let signBody: unknown = null;
    const logs: string[] = [];
    page.on("console", (m) => logs.push(m.text()));
    await stubWindowOpen(page);
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, state);
    await page.route(api("/files/sign-download"), async (route) => {
      signBody = route.request().postDataJSON();
      await route.fulfill({ json: { download_url: FAKE_DOWNLOAD, expires_in: 600 } });
    });

    await page.goto("/dashboard/resume");
    await page.getByRole("button", { name: "Download PDF for Backend CV" }).click();
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __opened: string[] }).__opened))
      .toEqual([FAKE_DOWNLOAD]);
    expect(signBody).toEqual({ path: PATH_A });
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
    expect(stored.includes(FAKE_DOWNLOAD)).toBe(false);
    expect(logs.some((l) => l.includes(FAKE_DOWNLOAD))).toBe(false);
    expect(await page.content()).not.toContain(FAKE_DOWNLOAD);
  });

  test("download denied by the server shows an error and opens nothing", async ({ page }) => {
    await stubWindowOpen(page);
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, { versions: [version(3, "Old", PATH_A)] });
    await page.route(api("/files/sign-download"), (route) =>
      route.fulfill({ status: 403, json: { detail: "You do not have access to this file." } }),
    );
    await page.goto("/dashboard/resume");
    await page.getByRole("button", { name: "Download PDF for Old" }).click();
    await expect(alert(page)).toContainText("You do not have access to this file.");
    expect(await page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toEqual([]);
  });

  test("non-PDF is rejected before any API or storage call", async ({ page }) => {
    let calls = 0;
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, { versions: [version(1, "Main")] });
    await page.route(api("/files/**"), (route) => {
      calls += 1;
      return route.fulfill({ status: 500, json: {} });
    });
    await page.goto("/dashboard/resume");
    await page.getByLabel("Attach PDF for Main").setInputFiles({
      name: "cv.docx", mimeType: "application/octet-stream", buffer: Buffer.from("x"),
    });
    await expect(alert(page)).toContainText("Please choose a PDF file.");
    expect(calls).toBe(0);
  });

  test("attach refused by the server is reported; list not changed", async ({ page }) => {
    const puts: string[] = [];
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, { versions: [version(1, "Main")] });
    await mockStorage(page, puts);
    await page.route(api("/files/sign-upload"), (route) =>
      route.fulfill({ json: { path: PATH_A, upload_url: `${STORAGE}/upload/x`, token: "mock" } }),
    );
    await page.route(api("/resume/versions/1/file"), (route) =>
      route.fulfill({ status: 403, json: { detail: "That file cannot be attached to this resume." } }),
    );
    await page.goto("/dashboard/resume");
    await page.getByLabel("Attach PDF for Main").setInputFiles(pdf);
    await expect(alert(page)).toContainText("That file cannot be attached to this resume.");
    await expect(page.getByText("Resume PDF: none attached")).toBeVisible();
  });

  test("storage disabled (503) shows a clear message", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, { versions: [version(1, "Main")] });
    await page.route(api("/files/sign-upload"), (route) =>
      route.fulfill({ status: 503, json: { detail: "File storage is not configured on this server." } }),
    );
    await page.goto("/dashboard/resume");
    await page.getByLabel("Attach PDF for Main").setInputFiles(pdf);
    await expect(alert(page)).toContainText("not enabled on this server yet");
  });

  test("owner removes the attached PDF after confirming", async ({ page }) => {
    const state = { versions: [version(4, "Main", PATH_A)] };
    let deleted = 0;
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, state);
    await page.route(api("/resume/versions/4/file"), async (route) => {
      expect(route.request().method()).toBe("DELETE");
      deleted += 1;
      state.versions = [version(4, "Main")];
      await route.fulfill({ json: { ...state.versions[0], markdown: "#" } });
    });
    page.once("dialog", (d) => d.accept());
    await page.goto("/dashboard/resume");
    await page.getByRole("button", { name: "Remove PDF from Main" }).click();
    await expect(page.getByText("Resume PDF: none attached")).toBeVisible();
    expect(deleted).toBe(1);
  });

  test("non-student sees no resume-file controls", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "teacher");
    await mockVersions(page, { versions: [version(1, "Main", PATH_A)] });
    await page.goto("/dashboard/resume");
    await expect(page.getByRole("heading", { name: /Version history/ })).toBeVisible();
    await expect(page.getByText("Main", { exact: true })).toBeVisible();
    await expect(page.getByText(/Resume PDF:/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Download PDF/ })).toHaveCount(0);
  });

  test("existing generation + version workflow still works (AI mocked)", async ({ page }) => {
    const state = { versions: [] as Version[] };
    await mockBackground(page);
    await mockUser(page, "student");
    await mockVersions(page, state);
    await page.route(api("/resume/generate"), async (route) => {
      state.versions = [version(9, "Generated")];
      await route.fulfill({ json: { markdown: "# Synthetic generated resume", provider: "mock", version_id: 9 } });
    });
    await page.goto("/dashboard/resume");
    await page.getByRole("button", { name: "Generate resume" }).click();
    await expect(page.getByText("# Synthetic generated resume")).toBeVisible();
    await expect(page.getByText("Generated", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Attach PDF for Generated")).toBeVisible();
  });
});
