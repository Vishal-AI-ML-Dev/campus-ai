import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

function trimTrailingSlash(value: string) {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

const BASE_URL = trimTrailingSlash(process.env.DEMO_BASE_URL || process.env.FRONTEND_URL || "");
const API_URL = trimTrailingSlash(process.env.DEMO_API_URL || process.env.API_URL || "");
const PASSWORD = process.env.DEMO_PASSWORD || "";
const MODE = process.env.DEMO_CAPTURE_MODE || "highlights";
const OUTPUT_DIR = path.resolve(process.cwd(), process.env.DEMO_OUTPUT_DIR || "demo-assets");
const SCREENSHOT_DIR = path.join(OUTPUT_DIR, "screenshots");
const TOKEN_KEY = "campus_ai_token";

type Role = "admin" | "teacher" | "tpo" | "student";
type CaptureRole = "public" | Role;

type Target = {
  role: CaptureRole;
  name: string;
  route: string;
};

const accounts: Record<Role, string> = {
  admin: process.env.DEMO_ADMIN_EMAIL || "",
  teacher: process.env.DEMO_TEACHER_EMAIL || "",
  tpo: process.env.DEMO_TPO_EMAIL || "",
  student: process.env.DEMO_STUDENT_EMAIL || "",
};

const targets: Target[] = [
  { role: "public", name: "01-public-home", route: "/" },
  { role: "public", name: "02-public-create-institution", route: "/create-institution" },
  { role: "public", name: "03-public-login", route: "/login" },
  { role: "public", name: "04-public-accept-invite", route: "/accept-invite" },

  { role: "admin", name: "10-admin-overview", route: "/dashboard" },
  { role: "admin", name: "11-admin-users-import-invites", route: "/dashboard/users" },
  { role: "admin", name: "12-admin-departments", route: "/dashboard/departments" },
  { role: "admin", name: "13-admin-institute-kpis", route: "/dashboard/institute" },

  { role: "student", name: "20-student-overview", route: "/dashboard" },
  { role: "student", name: "21-student-attendance", route: "/dashboard/attendance" },
  { role: "student", name: "22-student-resume", route: "/dashboard/resume" },
  { role: "student", name: "23-student-mentor", route: "/dashboard/mentor" },

  { role: "teacher", name: "30-teacher-overview", route: "/dashboard" },
  { role: "teacher", name: "31-teacher-verify", route: "/dashboard/verify" },
  { role: "teacher", name: "32-teacher-face-enroll", route: "/dashboard/face-enroll" },
  { role: "teacher", name: "33-teacher-face-attendance", route: "/dashboard/face-attendance" },

  { role: "tpo", name: "40-tpo-overview", route: "/dashboard" },
  { role: "tpo", name: "41-tpo-drives", route: "/dashboard/drives" },
  { role: "tpo", name: "42-tpo-applications", route: "/dashboard/applications" },
  { role: "tpo", name: "43-tpo-placement-analytics", route: "/dashboard/placement-analytics" },
];

const captured: Array<{ role: string; name: string; route: string; file: string }> = [];

async function getToken(page: Page, email: string): Promise<string> {
  if (!API_URL) throw new Error("DEMO_API_URL is required");
  if (!email) throw new Error("Demo email is missing");
  if (!PASSWORD) throw new Error("DEMO_PASSWORD is required");

  const response = await page.request.post(API_URL + "/auth/login", {
    form: { username: email, password: PASSWORD },
  });

  expect(response.ok(), "Login failed for " + email + ": " + response.status()).toBeTruthy();
  const body = await response.json();
  return body.access_token as string;
}

async function loginAs(page: Page, role: Role) {
  const token = await getToken(page, accounts[role]);
  await page.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value);
    },
    [TOKEN_KEY, token] as const,
  );
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  if (!BASE_URL) throw new Error("DEMO_BASE_URL is required");
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
  console.log("Demo capture mode: " + MODE);
  console.log("Targets: " + targets.length);
  console.log("Output: " + OUTPUT_DIR);
});

for (const target of targets) {
  test("capture " + target.name, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });

    if (target.role !== "public") {
      await loginAs(page, target.role);
    }

    const response = await page.goto(BASE_URL + target.route, {
      waitUntil: "domcontentloaded",
    });

    if (response && response.status() >= 400) {
      throw new Error("HTTP " + response.status() + " on " + target.route);
    }

    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1000);

    const fileName = target.name + ".png";
    const filePath = path.join(SCREENSHOT_DIR, fileName);

    await page.screenshot({ path: filePath, fullPage: true });

    captured.push({
      role: target.role,
      name: target.name,
      route: target.route,
      file: "screenshots/" + fileName,
    });

    console.log("OK " + target.name);
  });
}

test.afterAll(() => {
  const lines: string[] = [
    "# Campus AI Demo Capture",
    "",
    "Mode: " + MODE,
    "Base URL: " + BASE_URL,
    "",
    "Captured: " + captured.length,
    "",
  ];

  for (const item of captured) {
    lines.push("## " + item.name);
    lines.push("");
    lines.push("- Role: " + item.role);
    lines.push("- Route: " + item.route);
    lines.push("");
    lines.push("![" + item.name + "](" + item.file + ")");
    lines.push("");
  }

  writeFileSync(path.join(OUTPUT_DIR, "demo-report.md"), lines.join("\n"));
  writeFileSync(
    path.join(OUTPUT_DIR, "demo-metadata.json"),
    JSON.stringify({ mode: MODE, baseUrl: BASE_URL, apiUrl: API_URL, captured }, null, 2),
  );

  console.log("Demo report written to " + path.join(OUTPUT_DIR, "demo-report.md"));
});
