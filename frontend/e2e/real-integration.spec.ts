import { test, expect, type Browser, type BrowserContext, type Page, type Response } from "@playwright/test";
import { randomBytes } from "node:crypto";

/**
 * REAL browser -> REAL backend integration (Phase 5O). NO API MOCKS.
 *
 * Runs only when E2E_REAL=1, against a SEPARATE test stack whose backend uses
 * the isolated test database (FRONTEND_URL / API_URL must point at it). Each
 * browser context aborts any request to the running stack's API port (8000)
 * or the old mocked-test port (8999) - a safety guard, not a mock.
 *
 * Synthetic data only; setup steps the UI does not offer (creating staff,
 * drives, applications, offers) use the REAL API with the actor's own
 * browser-context cookies. Passwords/tokens live in memory only and are never
 * printed: assertions on them compare booleans.
 */

const RUN = process.env.E2E_REAL === "1";
const API = (process.env.API_URL || "").replace(/\/$/, "");
// Setup calls reuse the actor's browser cookies, so they must carry the
// frontend's Origin like the real UI does (backend CSRF check, Phase 6).
const FRONTEND_ORIGIN = new URL(process.env.FRONTEND_URL || "http://localhost:3000").origin;
const PREFIX = "/v1";
const run = randomBytes(5).toString("hex");
const DOMAIN = "zz-api-iso.example.com";
const pw = () => randomBytes(12).toString("base64url");

test.skip(!RUN || !API, "Set E2E_REAL=1 and API_URL to the isolated test backend");
test.describe.configure({ mode: "serial" });

type Actor = { ctx: BrowserContext; page: Page; email: string; password: string };
type Creds = { email: string; password: string; id: number };
type Json = Record<string, unknown>;
type State = {
  adminA?: Actor; adminB?: Actor; student?: Actor & { id?: number }; teacher?: Actor;
  tpo?: Actor; recruiter?: Actor; tpoCreds?: Creds;
  companyId?: number; driveId?: number; offerId?: number;
};
const S: State = {};
const blocked: string[] = [];

async function newCtx(browser: Browser): Promise<BrowserContext> {
  const ctx = await browser.newContext();
  await ctx.route(/^https?:\/\/(localhost|127\.0\.0\.1):(8000|8999)\//, (route) => {
    blocked.push(new URL(route.request().url()).host);
    return route.abort();
  });
  return ctx;
}

async function api<T = Json>(ctx: BrowserContext, method: string, path: string, data?: unknown) {
  const res = await ctx.request.fetch(`${API}${PREFIX}${path}`, {
    method, data, failOnStatusCode: false, headers: { Origin: FRONTEND_ORIGIN },
  });
  let json = null as unknown as T;
  try { json = await res.json(); } catch { /* 204 */ }
  return { status: res.status(), json };
}

async function loginUI(browser: Browser, email: string, password: string): Promise<Actor> {
  const ctx = await newCtx(browser);
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/dashboard");
  return { ctx, page, email, password };
}

async function authCookies(ctx: BrowserContext) {
  const all = await ctx.cookies();
  return all
    .filter((c) => c.name === "access_token" || c.name === "refresh_token")
    .map((c) => ({ name: c.name, path: c.path, httpOnly: c.httpOnly, sameSite: c.sameSite, secure: c.secure }));
}

async function signupUI(browser: Browser, key: string): Promise<Actor> {
  const ctx = await newCtx(browser);
  const page = await ctx.newPage();
  const email = `admin-${key}-${run}@${DOMAIN}`;
  const password = pw();
  await page.goto("/create-institution");
  await page.locator("#institutionName").fill(`E2E Institute ${key.toUpperCase()} ${run}`);
  await page.locator("#institutionSlug").fill(`zz-api-iso-e2e-${run}-${key}`);
  await page.locator("#adminFullName").fill(`Synthetic Admin ${key}`);
  await page.locator("#adminEmail").fill(email);
  await page.locator("#adminPassword").fill(password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL("**/dashboard");
  return { ctx, page, email, password };
}

async function createUser(admin: Actor, role: string, label: string): Promise<Creds> {
  const email = `${label}-${run}@${DOMAIN}`;
  const password = pw();
  const r = await api(admin.ctx, "POST", "/admin/users", { email, full_name: `Synthetic ${label}`, password, role });
  expect(r.status, `create ${role}`).toBe(201);
  return { email, password, id: r.json.id as number };
}

test.afterAll(async () => {
  for (const a of Object.values(S)) {
    if (a && typeof a === "object" && "ctx" in a) await (a as Actor).ctx.close().catch(() => {});
  }
});

// ---------------------------------------------------------------------------
test("1. institute signup, logout and login (real cookies)", async ({ browser }) => {
  const admin = await signupUI(browser, "a");
  await expect(admin.page.getByText("admin dashboard")).toBeVisible();
  const cookies = await authCookies(admin.ctx);
  expect(cookies.map((c) => `${c.name}:${c.path}:${c.httpOnly}:${c.sameSite}`).sort()).toEqual([
    "access_token:/:true:Lax",
    "refresh_token:/v1/auth:true:Lax",
  ]);
  await admin.page.getByRole("button", { name: "Logout" }).click();
  await admin.page.waitForURL("**/login");
  expect((await authCookies(admin.ctx)).length).toBe(0);
  expect((await api(admin.ctx, "GET", "/auth/me")).status).toBe(401);
  await admin.ctx.close();
  S.adminA = await loginUI(browser, admin.email, admin.password);
  await expect(S.adminA!.page.getByText("admin dashboard")).toBeVisible();
});

test("2. student submits a skill, teacher verifies it in the UI", async ({ browser }) => {
  const student = await createUser(S.adminA!, "student", "student-a");
  const teacher = await createUser(S.adminA!, "teacher", "teacher-a");
  S.tpoCreds = await createUser(S.adminA!, "tpo", "tpo-a");
  S.student = await loginUI(browser, student.email, student.password);
  S.student!.id = student.id;
  S.teacher = await loginUI(browser, teacher.email, teacher.password);

  const skillName = `E2E Skill ${run}`;
  const sp = S.student!.page;
  await sp.goto("/dashboard/skills");
  await sp.getByPlaceholder("FastAPI").fill(skillName);
  await sp.getByPlaceholder("https://github.com/me/project").fill("https://example.com/proof");
  await sp.getByRole("button", { name: "Claim skill" }).click();
  await expect(sp.getByText(skillName)).toBeVisible();

  const tp = S.teacher!.page;
  await tp.goto("/dashboard/verify");
  const row = tp
    .locator("li, tr, div")
    .filter({ has: tp.getByText(skillName, { exact: true }) })
    .filter({ has: tp.getByRole("button", { name: "Verify", exact: true }) })
    .last();
  const decided = tp.waitForResponse((r: Response) => r.url().includes("/decision") && r.request().method() === "PATCH");
  await row.getByRole("button", { name: "Verify", exact: true }).click();
  expect((await decided).status()).toBe(200);

  await sp.reload();
  const mine = (await api<Json[]>(S.student!.ctx, "GET", "/skills/me")).json;
  const sk = mine.find((s) => s.name === skillName);
  expect(sk?.status).toBe("verified");
  expect(sk?.ai_score).toBe(77); // scored by the local mock AI via the real backend path
  await expect(sp.getByText("verified").first()).toBeVisible();
});

test("3. recruiter accepts invite in the browser, then signs in separately", async ({ browser }) => {
  S.tpo = await loginUI(browser, S.tpoCreds!.email, S.tpoCreds!.password);
  const recEmail = `recruiter-${run}@${DOMAIN}`;
  const inv = await api(S.tpo!.ctx, "POST", "/recruiters/invite", {
    company_name: `zz-api-iso-e2e-${run} Synthetic Co`, email: recEmail,
  });
  expect(inv.status).toBe(201);
  S.companyId = (inv.json.recruiter as Json).id as number;
  const token = inv.json.token as string;

  const ctx = await newCtx(browser);
  const page = await ctx.newPage();
  await page.goto(`/recruiter/accept-invite?token=${encodeURIComponent(token)}`);
  await expect(page.getByRole("heading", { name: "Join as a recruiter" })).toBeVisible();
  expect(page.url().includes("token=")).toBe(false);
  const recPw = pw();
  await page.getByLabel("Full name").fill("Synthetic Recruiter");
  await page.getByLabel("Password", { exact: true }).fill(recPw);
  await page.getByLabel("Confirm password").fill(recPw);
  await page.getByRole("button", { name: "Create recruiter account" }).click();
  await expect(page.getByRole("heading", { name: "Your recruiter account is ready" })).toBeVisible();
  expect((await authCookies(ctx)).length).toBe(0); // no session until sign-in
  const reuse = await api(ctx, "POST", "/recruiters/accept-invite", { token, full_name: "x", password: recPw });
  expect(reuse.status).toBe(400);
  await page.getByRole("link", { name: "Continue to sign in" }).click();
  await page.waitForURL("**/login");
  await page.locator("#email").fill(recEmail);
  await page.locator("#password").fill(recPw);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/dashboard");
  await expect(page.getByText("recruiter dashboard")).toBeVisible();
  // Sidebar + dashboard quick-link both read "Candidates": assert the first.
  await expect(page.getByRole("link", { name: /Candidates/ }).first()).toBeVisible();
  S.recruiter = { ctx, page, email: recEmail, password: recPw };
});

test("4. student views an offer in the browser and accepts it", async () => {
  const roleTitle = `E2E Engineer ${run}`;
  const drive = await api(S.tpo!.ctx, "POST", "/drives", { company_name: "Synthetic Co", role_title: roleTitle, package_lpa: 9 });
  expect(drive.status).toBe(201);
  S.driveId = drive.json.id as number;
  const app = await api(S.student!.ctx, "POST", `/drives/${S.driveId}/apply`);
  expect(app.status).toBe(201);
  expect((await api(S.tpo!.ctx, "PATCH", `/drives/applications/${app.json.id}/status`, { status: "shortlisted" })).status).toBe(200);
  expect((await api(S.tpo!.ctx, "PATCH", `/recruiters/drives/${S.driveId}/recruiter`, { recruiter_id: S.companyId })).status).toBe(200);
  const offer = await api(S.recruiter!.ctx, "POST", "/recruiters/me/offers", { application_id: app.json.id });
  expect(offer.status).toBe(201);
  S.offerId = offer.json.id as number;

  const sp = S.student!.page;
  await sp.goto("/dashboard/offers");
  await expect(sp.getByRole("heading", { name: roleTitle })).toBeVisible();
  await sp.getByRole("button", { name: new RegExp(`^Accept the ${roleTitle} offer`) }).click();
  await sp.getByRole("button", { name: "Yes, accept offer" }).click();
  await expect(sp.getByText("You accepted this offer")).toBeVisible();
  const mine = (await api<Json[]>(S.student!.ctx, "GET", "/recruiters/offers/mine")).json;
  expect(mine.find((o) => o.id === S.offerId)?.status).toBe("accepted");
});

test("5a. expired access cookie is refreshed through /v1/auth/refresh", async () => {
  const { ctx, page } = S.student!;
  await ctx.clearCookies({ name: "access_token" });
  expect((await authCookies(ctx)).map((c) => c.name)).toEqual(["refresh_token"]);
  const refreshed = page.waitForResponse((r: Response) => r.url().endsWith(`${PREFIX}/auth/refresh`));
  await page.goto("/dashboard/offers");
  expect((await refreshed).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "My offers" })).toBeVisible();
  const after = await authCookies(ctx);
  expect(after.map((c) => `${c.name}:${c.path}`).sort()).toEqual(["access_token:/", "refresh_token:/v1/auth"]);
});

test("5b. another institute cannot see institute A's data", async ({ browser }) => {
  S.adminB = await signupUI(browser, "b");
  const users = (await api<Json[]>(S.adminB!.ctx, "GET", "/admin/users?limit=200")).json;
  expect(users.some((u) => String(u.email).includes(`-${run}@`) && !String(u.email).includes("admin-b"))).toBe(false);
  expect((await api(S.adminB!.ctx, "GET", `/drives/${S.driveId}`)).status).toBe(404);
  const offers = (await api<Json[]>(S.adminB!.ctx, "GET", "/recruiters/offers")).json;
  expect(offers.some((o) => o.id === S.offerId)).toBe(false);
  const bp = S.adminB!.page;
  await bp.goto("/dashboard/users");
  await expect(bp.getByText(S.student!.email)).toHaveCount(0);
});

test("5c. logout clears cookies and revokes the browser session", async () => {
  const { ctx, page } = S.student!;
  await page.getByRole("button", { name: "Logout" }).click();
  await page.waitForURL("**/login");
  expect((await authCookies(ctx)).length).toBe(0);
  await page.goto("/dashboard/offers");
  await page.waitForURL("**/login");
  expect((await api(ctx, "GET", "/auth/me")).status).toBe(401);
  expect((await api(ctx, "POST", "/auth/refresh")).status).toBe(401);
  expect(blocked, "requests to the running stack / mocked port").toEqual([]);
});
