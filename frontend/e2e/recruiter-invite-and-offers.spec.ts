import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Focused UI tests for the recruiter invite-acceptance page and the student
 * offers page.
 *
 * Fully MOCKED: every backend call is intercepted with page.route(), so these
 * need no backend, no accounts and no real data - only the frontend dev/prod
 * server at FRONTEND_URL (default http://localhost:3000).
 *
 * STATUS: written in Phase 5K; NOT yet run (Playwright browsers are not
 * installed locally and installing them requires a download).
 *
 *   npx playwright test e2e/recruiter-invite-and-offers.spec.ts
 */

const API_PREFIX = (process.env.API_PREFIX ?? "/v1").replace(/\/$/, "");
const api = (path: string) => `**${API_PREFIX}${path}`;
const SYNTHETIC_TOKEN = "synthetic-invite-token-0123456789";

type OfferStatus = "extended" | "accepted" | "declined" | "withdrawn";

function offer(id: number, status: OfferStatus, extra: Record<string, unknown> = {}) {
  return {
    id,
    application_id: 100 + id,
    drive_id: 200 + id,
    drive_role: "Backend Engineer",
    company_name: `Synthetic Co ${id}`,
    student_id: 7,
    student_name: "Synthetic Student",
    role_title: `Role ${id}`,
    package_lpa: 12,
    location: "Remote",
    joining_date: null,
    expires_on: null,
    status,
    note: null,
    student_response_note: null,
    created_at: "2030-01-01T00:00:00Z",
    responded_at: status === "accepted" || status === "declined" ? "2030-01-02T00:00:00Z" : null,
    ...extra,
  };
}

/**
 * The page's own alert(s). Next.js also renders an always-present, EMPTY
 * `#__next-route-announcer__` with role="alert"; filtering to alerts that
 * contain text excludes it without weakening what is asserted.
 */
function alert(page: Page) {
  return page.getByRole("alert").filter({ hasText: /\S/ });
}

/** Any API call not mocked explicitly gets an empty, harmless answer. */
async function mockBackground(page: Page) {
  await page.route(`**${API_PREFIX}/**`, (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
}

async function mockUser(page: Page, role: "student" | "recruiter") {
  await page.route(api("/auth/me"), (route) =>
    route.fulfill({
      json: { id: 7, email: "synthetic@example.test", full_name: "Synthetic", role, is_active: true },
    }),
  );
}

// ---------------------------------------------------------------------------
// Recruiter invite acceptance
// ---------------------------------------------------------------------------
test.describe("recruiter accept-invite page", () => {
  test("captures the token, strips it from the URL and creates the account", async ({ page }) => {
    await mockBackground(page);
    let sent: Record<string, unknown> | null = null;
    await page.route(api("/recruiters/accept-invite"), async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ json: { access_token: "x", token_type: "bearer" } });
    });

    await page.goto(`/recruiter/accept-invite?token=${SYNTHETIC_TOKEN}`);
    await expect(page.getByRole("heading", { name: "Join as a recruiter" })).toBeVisible();
    await expect(page).not.toHaveURL(/token=/);
    await expect(page.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");

    await page.getByLabel("Full name").fill("Synthetic Recruiter");
    await page.getByLabel("Password", { exact: true }).fill("synthetic-pass-1");
    await page.getByLabel("Confirm password").fill("synthetic-pass-1");
    await page.getByRole("button", { name: "Create recruiter account" }).click();

    await expect(page.getByRole("heading", { name: "Your recruiter account is ready" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Continue to sign in" })).toHaveAttribute("href", "/login");
    expect(sent).toEqual({ token: SYNTHETIC_TOKEN, full_name: "Synthetic Recruiter", password: "synthetic-pass-1" });
    expect(Object.keys(sent ?? {})).not.toContain("tenant_id");
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
    expect(stored).not.toContain(SYNTHETIC_TOKEN);
  });

  test("missing token disables the form", async ({ page }) => {
    await mockBackground(page);
    await page.goto("/recruiter/accept-invite");
    await expect(alert(page)).toContainText("missing its code");
    await expect(page.getByRole("button", { name: "Create recruiter account" })).toBeDisabled();
  });

  test("mismatched passwords never reach the API", async ({ page }) => {
    await mockBackground(page);
    let calls = 0;
    await page.route(api("/recruiters/accept-invite"), (route) => {
      calls += 1;
      return route.fulfill({ json: {} });
    });
    await page.goto(`/recruiter/accept-invite?token=${SYNTHETIC_TOKEN}`);
    await page.getByLabel("Full name").fill("Synthetic");
    await page.getByLabel("Password", { exact: true }).fill("synthetic-pass-1");
    await page.getByLabel("Confirm password").fill("synthetic-pass-2");
    await page.getByRole("button", { name: "Create recruiter account" }).click();
    await expect(alert(page)).toContainText("do not match");
    expect(calls).toBe(0);
  });

  for (const [status, detail, expected] of [
    [400, "Invalid or already-used invite", "Ask the placement office for a new invite"],
    [409, "An account with this email already exists", "already exists"],
  ] as const) {
    test(`shows guidance for HTTP ${status}`, async ({ page }) => {
      await mockBackground(page);
      await page.route(api("/recruiters/accept-invite"), (route) =>
        route.fulfill({ status, json: { detail } }),
      );
      await page.goto(`/recruiter/accept-invite?token=${SYNTHETIC_TOKEN}`);
      await page.getByLabel("Full name").fill("Synthetic");
      await page.getByLabel("Password", { exact: true }).fill("synthetic-pass-1");
      await page.getByLabel("Confirm password").fill("synthetic-pass-1");
      await page.getByRole("button", { name: "Create recruiter account" }).click();
      await expect(alert(page)).toContainText(expected);
      await expect(page.getByRole("button", { name: "Create recruiter account" })).toBeEnabled();
    });
  }
});

// ---------------------------------------------------------------------------
// Student offers
// ---------------------------------------------------------------------------
test.describe("student offers page", () => {
  test("lists offers and accepts one after confirmation", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "student");
    const offers = [offer(1, "extended"), offer(2, "declined"), offer(3, "withdrawn")];
    await page.route(api("/recruiters/offers/mine"), (route) => route.fulfill({ json: offers }));
    let body: Record<string, unknown> | null = null;
    await page.route(api("/recruiters/offers/1/respond"), async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ json: offer(1, "accepted", { student_response_note: "Thanks" }) });
    });

    await page.goto("/dashboard/offers");
    await expect(page.getByRole("heading", { name: "My offers" })).toBeVisible();
    await expect(page.getByText("1 offer awaiting your response.")).toBeVisible();
    await expect(page.getByText("You declined this offer")).toBeVisible();
    await expect(page.getByText("The company withdrew this offer.")).toBeVisible();
    // Only the extended offer is actionable.
    await expect(page.getByRole("button", { name: /^Accept the/ })).toHaveCount(1);

    await page.getByLabel("Message to the recruiter (optional)").fill("Thanks");
    await page.getByRole("button", { name: "Accept the Role 1 offer from Synthetic Co 1" }).click();
    await page.getByRole("button", { name: "Yes, accept offer" }).click();
    await expect(page.getByText("You accepted this offer")).toBeVisible();
    expect(body).toEqual({ accept: true, note: "Thanks" });
    await expect(page.getByRole("button", { name: /^Accept the/ })).toHaveCount(0);
  });

  test("cancel leaves the offer unanswered", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "student");
    let calls = 0;
    await page.route(api("/recruiters/offers/mine"), (route) => route.fulfill({ json: [offer(1, "extended")] }));
    await page.route(api("/recruiters/offers/1/respond"), (route) => {
      calls += 1;
      return route.fulfill({ json: {} });
    });
    await page.goto("/dashboard/offers");
    await page.getByRole("button", { name: /^Decline the/ }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("button", { name: /^Decline the/ })).toBeVisible();
    expect(calls).toBe(0);
  });

  test("409 on respond shows the reason and refreshes", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "student");
    let listCalls = 0;
    await page.route(api("/recruiters/offers/mine"), (route) => {
      listCalls += 1;
      return route.fulfill({ json: [offer(1, listCalls === 1 ? "extended" : "withdrawn")] });
    });
    await page.route(api("/recruiters/offers/1/respond"), (route) =>
      route.fulfill({ status: 409, json: { detail: "This offer is withdrawn and can no longer be changed." } }),
    );
    await page.goto("/dashboard/offers");
    await page.getByRole("button", { name: /^Accept the/ }).click();
    await page.getByRole("button", { name: "Yes, accept offer" }).click();
    await expect(alert(page)).toContainText("can no longer be changed");
    await expect(page.getByText("The company withdrew this offer.")).toBeVisible();
    expect(listCalls).toBe(2);
  });

  test("empty and error states", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "student");
    let fail = true;
    await page.route(api("/recruiters/offers/mine"), (route) =>
      fail
        ? route.fulfill({ status: 500, json: { detail: "Synthetic failure", request_id: "req-1" } })
        : route.fulfill({ json: [] }),
    );
    await page.goto("/dashboard/offers");
    await expect(alert(page)).toContainText("Synthetic failure");
    fail = false;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByText("No offers yet")).toBeVisible();
  });

  test("non-students see a notice instead of the panel", async ({ page }) => {
    await mockBackground(page);
    await mockUser(page, "recruiter");
    await page.goto("/dashboard/offers");
    await expect(page.getByText("only available to student accounts")).toBeVisible();
  });
});
