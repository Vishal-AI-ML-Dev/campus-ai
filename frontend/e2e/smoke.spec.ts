import { test, expect, type Page } from "@playwright/test";

/**
 * Campus AI frontend smoke test.
 *
 * STATUS: restored from the old project in Phase 3 with the hard-coded demo
 * password, old account addresses and deployment URLs removed. It has NOT been
 * run since restoration.
 *
 * For each role it logs in through the API, then opens every dashboard page for
 * that role and records: HTTP 4xx/5xx, uncaught JS errors, console.error output
 * and error-boundary text.
 *
 * Auth note (UNVERIFIED): the old spec injected a token into localStorage, but
 * the frontend now authenticates with httpOnly cookies (see src/lib/api.ts).
 * This version logs in with the browser context's own request client so the
 * auth cookies land in the context's cookie jar. Use `localhost` for BOTH the
 * frontend and the API - cookies are SameSite=Lax, and mixing `localhost` with
 * `127.0.0.1` makes requests cross-site so the cookie is not sent.
 *
 * Required environment (no defaults for credentials - see e2e/README.md):
 *   SMOKE_PASSWORD, SMOKE_ADMIN_EMAIL, SMOKE_TEACHER_EMAIL,
 *   SMOKE_TPO_EMAIL, SMOKE_STUDENT_EMAIL
 * Optional:
 *   API_URL     default http://localhost:8000
 *   API_PREFIX  default /v1
 */

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/$/, "");
const API_PREFIX = (process.env.API_PREFIX ?? "/v1").replace(/\/$/, "");
// Login goes through the request client but must establish a BROWSER cookie
// session, so it sends the frontend Origin (backend CSRF check, Phase 6).
const FRONTEND_ORIGIN = new URL(process.env.FRONTEND_URL || "http://localhost:3000").origin;
const PASSWORD = process.env.SMOKE_PASSWORD || "";

type Role = "admin" | "teacher" | "tpo" | "student";

const ACCOUNTS: Record<Role, string> = {
  admin: process.env.SMOKE_ADMIN_EMAIL || "",
  teacher: process.env.SMOKE_TEACHER_EMAIL || "",
  tpo: process.env.SMOKE_TPO_EMAIL || "",
  student: process.env.SMOKE_STUDENT_EMAIL || "",
};

const PAGES: Record<Role, string[]> = {
  student: [
    "/dashboard",
    "/dashboard/attendance",
    "/dashboard/academics",
    "/dashboard/timetable",
    "/dashboard/assignments",
    "/dashboard/study-hub",
    "/dashboard/doubts",
    "/dashboard/resume",
    "/dashboard/skills",
    "/dashboard/projects",
    "/dashboard/eca",
    "/dashboard/internships",
    "/dashboard/placements",
    "/dashboard/offers",
    "/dashboard/leave",
    "/dashboard/analytics",
    "/dashboard/mentor",
  ],
  teacher: [
    "/dashboard",
    "/dashboard/verify",
    "/dashboard/attendance",
    "/dashboard/face-enroll",
    "/dashboard/face-attendance",
    "/dashboard/assignments",
    "/dashboard/study-hub",
    "/dashboard/doubts",
    "/dashboard/timetable",
    "/dashboard/gradebook",
    "/dashboard/analytics",
    "/dashboard/leave",
  ],
  tpo: [
    "/dashboard",
    "/dashboard/drives",
    "/dashboard/placement-analytics",
    "/dashboard/applications",
    "/dashboard/recruiters",
  ],
  admin: [
    "/dashboard",
    "/dashboard/institute",
    "/dashboard/users",
    "/dashboard/departments",
    "/dashboard/announcements",
    "/dashboard/calendar",
    "/dashboard/timetable",
    "/dashboard/analytics",
    "/dashboard/placement-analytics",
    "/dashboard/audit",
  ],
};

// Benign console noise we don't want to fail on.
const IGNORE_CONSOLE = [
  "Download the React DevTools",
  "Failed to load resource", // network 4xx surface elsewhere; not a JS crash
  "favicon",
  "[Fast Refresh]",
];

const ERROR_TEXT = [
  "Something went wrong",
  "Application error",
  "Unhandled Runtime Error",
  "This page could not be found",
  "500 - Internal",
  "Internal Server Error",
];

type Finding = { role: string; path: string; kind: string; detail: string };
const findings: Finding[] = [];
let checked = 0;

test.beforeAll(() => {
  const missing = [
    !PASSWORD && "SMOKE_PASSWORD",
    !ACCOUNTS.admin && "SMOKE_ADMIN_EMAIL",
    !ACCOUNTS.teacher && "SMOKE_TEACHER_EMAIL",
    !ACCOUNTS.tpo && "SMOKE_TPO_EMAIL",
    !ACCOUNTS.student && "SMOKE_STUDENT_EMAIL",
  ].filter(Boolean);
  if (missing.length) {
    throw new Error(
      "Missing required environment variables: " + missing.join(", "),
    );
  }
});

/** Log in via the context's request client so auth cookies are stored in it. */
async function login(page: Page, role: Role): Promise<void> {
  const res = await page.request.post(`${API_URL}${API_PREFIX}/auth/login`, {
    form: { username: ACCOUNTS[role], password: PASSWORD },
    headers: { Origin: FRONTEND_ORIGIN },
  });
  expect(res.ok(), `login failed for role ${role}: ${res.status()}`).toBeTruthy();
}

for (const role of Object.keys(PAGES) as Role[]) {
  test(`${role} - all pages render without errors`, async ({ page }) => {
    await login(page, role);

    for (const path of PAGES[role]) {
      const consoleErrors: string[] = [];
      const pageErrors: string[] = [];
      const onConsole = (msg: { type: () => string; text: () => string }) => {
        if (msg.type() !== "error") return;
        const t = msg.text();
        if (IGNORE_CONSOLE.some((s) => t.includes(s))) return;
        consoleErrors.push(t);
      };
      const onPageError = (err: Error) => pageErrors.push(err.message);
      page.on("console", onConsole);
      page.on("pageerror", onPageError);

      const tags: string[] = [];
      try {
        let httpStatus = 0;
        // domcontentloaded settles quickly; networkidle alone is flaky because
        // background/keep-alive requests can stop it from ever settling.
        const resp = await page.goto(path, { waitUntil: "domcontentloaded" });
        httpStatus = resp ? resp.status() : 0;
        // Give client-side data fetches a bounded amount of time.
        await page
          .waitForLoadState("networkidle", { timeout: 8000 })
          .catch(() => {});
        await page.waitForTimeout(800).catch(() => {});

        const bodyText =
          (await page
            .locator("body")
            .innerText()
            .catch(() => "")) || "";
        const errText = ERROR_TEXT.find((t) => bodyText.includes(t));

        if (httpStatus >= 400) {
          tags.push(`HTTP ${httpStatus}`);
          findings.push({
            role,
            path,
            kind: "HTTP",
            detail: `status ${httpStatus}`,
          });
        }
        if (pageErrors.length) {
          tags.push(`JS-crash x${pageErrors.length}`);
          findings.push({
            role,
            path,
            kind: "JS",
            detail: pageErrors[0].slice(0, 120),
          });
        }
        if (errText) {
          tags.push("error-boundary");
          findings.push({ role, path, kind: "BOUNDARY", detail: errText });
        }
        if (consoleErrors.length) {
          tags.push(`console.error x${consoleErrors.length}`);
          findings.push({
            role,
            path,
            kind: "CONSOLE",
            detail: consoleErrors[0].slice(0, 120),
          });
        }
      } catch (e) {
        tags.push("NAV-timeout");
        findings.push({
          role,
          path,
          kind: "NAV",
          detail: String(e).slice(0, 120),
        });
      }

      checked++;
      // eslint-disable-next-line no-console
      console.log(
        `  ${tags.length ? "FAIL" : "OK  "} [${role}] ${path} ${tags.join(", ")}`,
      );

      page.off("console", onConsole);
      page.off("pageerror", onPageError);
    }
  });
}

test.afterAll(() => {
  // eslint-disable-next-line no-console
  console.log("\n" + "=".repeat(70));
  console.log(`  FRONTEND SMOKE SUMMARY   |   pages checked: ${checked}`);
  console.log("=".repeat(70));
  if (!findings.length) {
    console.log(
      "  No JS crash / error-boundary / 4xx-5xx recorded on the pages visited.",
    );
  } else {
    console.log(`  ${findings.length} issue(s) recorded:`);
    for (const f of findings) {
      console.log(`   - [${f.role}] ${f.path}  (${f.kind})  ${f.detail}`);
    }
  }
  console.log("=".repeat(70));
});
