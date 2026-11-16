import type { NextConfig } from "next";

/**
 * Phase 7: HTTP security headers for every page.
 *
 * Next.js compiles `headers()` into the build output, so every input below is
 * read at BUILD time (Docker build args), exactly like NEXT_PUBLIC_*:
 *
 *   NEXT_PUBLIC_API_BASE_URL  API origin the browser calls -> connect-src.
 *   CSP_CONNECT_SRC_EXTRA     space-separated extra origins the browser must
 *                             reach with fetch/XHR, e.g. the file-storage
 *                             origin used for signed-URL uploads. Empty by
 *                             default: uploads to an external storage origin
 *                             are BLOCKED until it is listed here.
 *   HSTS_MAX_AGE              seconds; 0/unset = no Strict-Transport-Security.
 *                             Set only for an HTTPS-only deployment on its
 *                             final domain.
 *   HSTS_INCLUDE_SUBDOMAINS   "true" to add includeSubDomains (separate opt-in).
 *
 * CSP notes (chosen so the app does not break):
 *   - script-src keeps 'unsafe-inline': Next's hydration bootstrap, the
 *     theme script in layout.tsx and the static product-tour page
 *     (public/demo, inline handlers) are inline. A nonce-based policy would
 *     force every page to render dynamically; revisit later.
 *   - 'unsafe-eval' and ws: only in `next dev` (React Refresh / HMR).
 *   - frame-src / frame-ancestors 'self': the landing page embeds the
 *     same-origin /demo tour in an iframe; no other site may frame the app.
 *   - fonts are self-hosted by next/font at build time -> font-src 'self'.
 */

const isDev = process.env.NODE_ENV !== "production";

function originOf(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null; // relative / empty base URL -> same origin ('self')
  }
}

function extraOrigins(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(/\s+/)
    .map((s) => originOf(s.trim()))
    .filter((s): s is string => Boolean(s));
}

export function buildCsp(env: NodeJS.ProcessEnv = process.env, dev = isDev): string {
  const connect = new Set<string>(["'self'"]);
  const api = originOf(env.NEXT_PUBLIC_API_BASE_URL);
  if (api) connect.add(api);
  for (const o of extraOrigins(env.CSP_CONNECT_SRC_EXTRA)) connect.add(o);
  if (dev) {
    connect.add("ws:");
    connect.add("wss:");
  }
  const script = ["'self'", "'unsafe-inline'", ...(dev ? ["'unsafe-eval'"] : [])];
  return [
    "default-src 'self'",
    `script-src ${script.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${[...connect].join(" ")}`,
    "frame-src 'self'",
    "frame-ancestors 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

export function buildSecurityHeaders(
  env: NodeJS.ProcessEnv = process.env,
  dev = isDev,
): { key: string; value: string }[] {
  const headers = [
    { key: "Content-Security-Policy", value: buildCsp(env, dev) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "SAMEORIGIN" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ];
  const maxAge = Number.parseInt(env.HSTS_MAX_AGE ?? "0", 10);
  if (Number.isFinite(maxAge) && maxAge > 0) {
    const sub = env.HSTS_INCLUDE_SUBDOMAINS === "true" ? "; includeSubDomains" : "";
    headers.push({ key: "Strict-Transport-Security", value: `max-age=${maxAge}${sub}` });
  }
  return headers;
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Production image only (frontend/Dockerfile sets NEXT_OUTPUT_STANDALONE=1):
  // a self-contained server.js without devDependencies. Unset elsewhere so
  // `next start` keeps working for local/e2e builds.
  ...(process.env.NEXT_OUTPUT_STANDALONE === "1" ? { output: "standalone" as const } : {}),
  async headers() {
    return [{ source: "/:path*", headers: buildSecurityHeaders() }];
  },
};

export default nextConfig;
