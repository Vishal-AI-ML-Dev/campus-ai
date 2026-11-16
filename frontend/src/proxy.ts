import { NextResponse, type NextRequest } from "next/server";

/**
 * Phase 7B: same-origin API proxy for the private staging deployment.
 *
 * Active ONLY when both server-side env vars are set (never NEXT_PUBLIC_*,
 * so neither value reaches the browser bundle):
 *   STAGING_API_ORIGIN   e.g. https://<backend>.onrender.com
 *   STAGING_GATE_TOKEN   shared secret the backend's staging gate requires
 *
 * The browser calls /v1/... on the frontend's own origin (build with
 * NEXT_PUBLIC_API_BASE_URL=""), so the whole app sits behind the hosting
 * platform's deployment protection, auth cookies are first-party, and the
 * backend only answers requests that came through here.
 *
 * Unset (local dev, e2e, the Docker image) => this proxy is a no-op.
 */

// Platform deployment-protection cookies are for the frontend host only and
// must not be forwarded to the API.
const STRIP_COOKIES = new Set(["_vercel_jwt"]);

function forwardedCookie(raw: string | null): string | null {
  if (!raw) return null;
  const kept = raw
    .split(";")
    .map((c) => c.trim())
    .filter((c) => c && !STRIP_COOKIES.has(c.split("=", 1)[0]));
  return kept.length ? kept.join("; ") : null;
}

export function proxy(request: NextRequest) {
  const origin = process.env.STAGING_API_ORIGIN;
  const gate = process.env.STAGING_GATE_TOKEN;
  if (!origin || !gate) return NextResponse.next();

  const target = new URL(request.nextUrl.pathname + request.nextUrl.search, origin);
  const headers = new Headers(request.headers);
  headers.set("x-staging-gate", gate); // overwrites any client-supplied value
  const cookie = forwardedCookie(request.headers.get("cookie"));
  if (cookie) headers.set("cookie", cookie);
  else headers.delete("cookie");

  return NextResponse.rewrite(target, { request: { headers } });
}

export const config = {
  matcher: ["/v1/:path*"],
};
