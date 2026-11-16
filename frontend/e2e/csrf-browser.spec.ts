import { test, expect, type Browser, type BrowserContext, type Page, type Request } from "@playwright/test";
import { randomBytes } from "node:crypto";

/**
 * REAL browser CSRF checks (Phase 6). NO API MOCKS for the backend.
 *
 * Runs only when E2E_REAL=1 against the isolated test stack (FRONTEND_URL /
 * API_URL). Meant for a backend started with production-style cookies
 * (COOKIE_SAMESITE=none, COOKIE_SECURE=true - Chrome accepts Secure cookies on
 * http://localhost), so the browser really attaches the victim's cookies to
 * cross-site requests and only the backend's Origin check stands in the way.
 *
 * The "attacker" page is served by page.route() at http://evil.example.test -
 * it never touches the network. Synthetic data only; passwords live in memory
 * and are never printed.
 */

const RUN = process.env.E2E_REAL === "1";
const API = (process.env.API_URL || "").replace(/\/$/, "");
const PREFIX = "/v1";
const EVIL = "http://evil.example.test";
// All attacks are TOP-LEVEL navigations: Chrome's local-network-access
// protection blocks sub-resource requests (fetch, iframes) from a non-local
// page to localhost before they are sent, so they would never reach this
// local backend (against a public API they would; the server-side Origin
// decision is identical and is covered by backend tests/test_csrf.py).
const run = randomBytes(5).toString("hex");
const DOMAIN = "zz-api-iso.example.com";

test.skip(!RUN || !API, "Set E2E_REAL=1 and API_URL to the isolated test backend");
test.describe.configure({ mode: "serial" });

type Victim = { ctx: BrowserContext; page: Page; email: string; password: string };
let victim: Victim | undefined;

async function newCtx(browser: Browser): Promise<BrowserContext> {
  const ctx = await browser.newContext();
  // Safety guard (not a mock): never reach the running stack's API port.
  await ctx.route(/^https?:\/\/(localhost|127\.0\.0\.1):(8000|8999)\//, (r) => r.abort());
  return ctx;
}

/** Serve an attacker page at EVIL/<name> inside this context. */
async function serveEvil(ctx: BrowserContext, name: string, html: string, headers: Record<string, string> = {}) {
  await ctx.route(`${EVIL}/${name}`, (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: html, headers }),
  );
}

const autoForm = (action: string, fields: Record<string, string> = {}, enctype = "application/x-www-form-urlencoded") => `<!doctype html><html><body>
<form id="f" method="POST" action="${action}" enctype="${enctype}">
${Object.entries(fields).map(([k, v]) => `<input type="hidden" name="${k}" value="${v}">`).join("\n")}
</form><script>document.getElementById("f").submit()</script></body></html>`;

/** Cookie header a request actually carried (Chromium exposes it). */
async function sentCookieNames(req: Request): Promise<string[]> {
  const h = await req.allHeaders();
  return (h["cookie"] || "").split(";").map((c) => c.trim().split("=")[0]).filter(Boolean);
}

async function sessionStillValid(ctx: BrowserContext): Promise<boolean> {
  const r = await ctx.request.get(`${API}${PREFIX}/auth/me`, { failOnStatusCode: false });
  return r.status() === 200;
}

test.afterAll(async () => {
  await victim?.ctx.close().catch(() => {});
});

test("0. victim signs up through the real UI (production-style cookies)", async ({ browser }) => {
  const ctx = await newCtx(browser);
  const page = await ctx.newPage();
  const email = `csrf-victim-${run}@${DOMAIN}`;
  const password = randomBytes(12).toString("base64url");
  await page.goto("/create-institution");
  await page.locator("#institutionName").fill(`CSRF Institute ${run}`);
  await page.locator("#institutionSlug").fill(`zz-api-iso-e2e-${run}-csrf`);
  await page.locator("#adminFullName").fill("Synthetic CSRF Victim");
  await page.locator("#adminEmail").fill(email);
  await page.locator("#adminPassword").fill(password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL("**/dashboard");
  victim = { ctx, page, email, password };

  const cookies = (await ctx.cookies()).filter((c) => c.name === "access_token" || c.name === "refresh_token");
  expect(cookies.map((c) => `${c.name}:${c.sameSite}:${c.secure}`).sort()).toEqual([
    "access_token:None:true",
    "refresh_token:None:true",
  ]);
  expect(await sessionStillValid(ctx)).toBe(true);
});

test("1. cross-site auto-submitting form to logout is rejected although cookies are sent", async () => {
  const { ctx, page } = victim!;
  await serveEvil(ctx, "logout.html", autoForm(`${API}${PREFIX}/auth/logout`));
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${API}${PREFIX}/auth/logout`),
    page.goto(`${EVIL}/logout.html`),
  ]);
  expect(resp.status()).toBe(403);
  // The browser DID attach the refresh cookie - only the Origin check stopped it.
  expect(await sentCookieNames(resp.request())).toContain("refresh_token");
  expect((await resp.request().allHeaders())["origin"]).toBe(EVIL);
  expect(await sessionStillValid(ctx)).toBe(true);
});

test("2. cross-site text/plain form (no preflight) to the refresh route is rejected", async () => {
  const { ctx, page } = victim!;
  await serveEvil(ctx, "refresh.html", autoForm(`${API}${PREFIX}/auth/refresh`, { x: "y" }, "text/plain"));
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${API}${PREFIX}/auth/refresh`),
    page.goto(`${EVIL}/refresh.html`),
  ]);
  expect(resp.status()).toBe(403);
  expect((await resp.request().allHeaders())["content-type"]).toContain("text/plain");
  expect(await sentCookieNames(resp.request())).toContain("refresh_token");
  expect(await sessionStillValid(ctx)).toBe(true);
});

test("3. sandboxed (opaque, Origin: null) document's form post is rejected", async () => {
  const { ctx, page } = victim!;
  // A top-level document served with CSP sandbox has an opaque origin.
  await serveEvil(ctx, "sandbox.html", autoForm(`${API}${PREFIX}/auth/logout`), {
    "content-security-policy": "sandbox allow-forms allow-scripts",
  });
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${API}${PREFIX}/auth/logout`),
    page.goto(`${EVIL}/sandbox.html`),
  ]);
  expect((await resp.request().allHeaders())["origin"]).toBe("null");
  expect(resp.status()).toBe(403);
  expect(await sessionStillValid(ctx)).toBe(true);
});

test("4. login CSRF: a forged cross-site login plants no session", async ({ browser }) => {
  const ctx = await newCtx(browser);
  const page = await ctx.newPage();
  await serveEvil(ctx, "login.html", autoForm(`${API}${PREFIX}/auth/login`, {
    username: victim!.email, password: victim!.password,
  }));
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${API}${PREFIX}/auth/login`),
    page.goto(`${EVIL}/login.html`),
  ]);
  expect(resp.status()).toBe(403);
  const planted = (await ctx.cookies()).filter((c) => c.name === "access_token" || c.name === "refresh_token");
  expect(planted).toEqual([]);
  await ctx.close();
});

test("5. the legitimate UI still works: logout from the app origin succeeds", async () => {
  const { ctx, page } = victim!;
  await page.goto("/dashboard");
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${API}${PREFIX}/auth/logout` && r.request().method() === "POST"),
    page.evaluate(async ({ url }) => {
      await fetch(url, { method: "POST", credentials: "include" });
    }, { url: `${API}${PREFIX}/auth/logout` }),
  ]);
  expect(resp.status()).toBe(204);
  expect(await sessionStillValid(ctx)).toBe(false);
});
