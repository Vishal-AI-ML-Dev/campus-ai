/**
 * Campus AI - lightweight typed API client for the FastAPI backend.
 *
 * Responsibilities:
 *   - Prefix every request with the configured backend base URL.
 *   - Send auth via httpOnly cookies (credentials: "include") on every request.
 *   - Transparently refresh the session once on a 401, then retry.
 *   - Parse JSON responses and surface backend error details as thrown errors.
 *   - Support form-encoded POST (needed by the OAuth2 /auth/login endpoint).
 *
 * Dev note: cookies are SameSite=Lax in dev, so frontend + backend must be the
 * SAME SITE. Use `localhost` for BOTH (localhost:3000 -> localhost:8000); mixing
 * `localhost` and `127.0.0.1` makes the request cross-site and the cookie will
 * not be sent. Configure the API base via NEXT_PUBLIC_API_BASE_URL.
 *
 * Place this file at: src/lib/api.ts
 *
 * Example:
 *   import { api } from "@/lib/api"
 *   await api.postForm("/auth/login", { username: email, password })
 *   const me = await api.get<User>("/auth/me")
 */

// Base URL of the backend. Configured via .env.local (NEXT_PUBLIC_API_BASE_URL).
const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Canonical API version prefix. Every request goes to
// `${BASE_URL}${API_PREFIX}${path}`, so callers keep passing version-agnostic
// paths like "/auth/me". Override with NEXT_PUBLIC_API_PREFIX (set it to "" to
// fall back to the deprecated unversioned routes during a migration).
const API_PREFIX = process.env.NEXT_PUBLIC_API_PREFIX ?? "/v1";

// Legacy localStorage key from the old (pre-cookie) auth model. We no longer
// store tokens in the browser; clearToken() only purges a stale leftover value.
const LEGACY_TOKEN_KEY = "campus_ai_token";

/** Purge any token left behind by the old localStorage-based auth model. */
export function clearToken(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(LEGACY_TOKEN_KEY);
  }
}

/** Error thrown when the backend responds with a non-2xx status code. */
export class ApiError extends Error {
  readonly status: number;
  /** Backend request id (X-Request-ID) - quote this in a bug report. */
  readonly requestId?: string;

  constructor(status: number, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

type JsonBody = Record<string, unknown>;

/** "admin_email" -> "Admin email". */
function fieldLabel(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Turn a FastAPI error body into one readable sentence.
 *
 * `detail` is usually a string, but request-validation errors (HTTP 422) send
 * a LIST of objects like {loc: ["body", "admin_email"], msg: "..."}; calling
 * String() on that shows "[object Object]". Only `loc` and `msg` are used: the
 * `input` key echoes what the user submitted (possibly a password), so it is
 * never displayed.
 */
function formatDetail(data: unknown, status: number): string {
  const fallback = `Request failed (${status})`;
  if (!data || typeof data !== "object" || !("detail" in data)) return fallback;
  const detail = (data as JsonBody).detail;
  if (typeof detail === "string") return detail || fallback;

  const items = Array.isArray(detail) ? detail : [detail];
  const messages = items
    .map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return null;
      const { loc, msg } = item as { loc?: unknown; msg?: unknown };
      if (typeof msg !== "string") return null;
      const text = msg.replace(/^Value error,\s*/i, "");
      const field = Array.isArray(loc)
        ? loc.filter((p) => typeof p === "string" && p !== "body").pop()
        : undefined;
      return field ? `${fieldLabel(field as string)}: ${text}` : text;
    })
    .filter((m): m is string => Boolean(m));

  return messages.length > 0 ? messages.join(" ") : fallback;
}

/** Build the ApiError for a non-OK response body. */
function toApiError(res: Response, data: unknown): ApiError {
  // Every error carries a request id (body + X-Request-ID header) so it can be
  // quoted in a bug report and traced in the backend logs.
  const requestId =
    (data && typeof data === "object" && "request_id" in data
      ? String((data as JsonBody).request_id)
      : res.headers.get("X-Request-ID")) ?? undefined;
  return new ApiError(res.status, formatDetail(data, res.status), requestId);
}

// --- 401 -> refresh -> retry (single-flight) --------------------------------

// Endpoints where a 401 must NOT trigger a refresh attempt (a 401 there means
// bad credentials / an expired session, not just a stale access token).
const NO_REFRESH_PATHS = new Set([
  "/auth/login",
  "/auth/refresh",
  "/auth/logout",
]);

// Shared in-flight refresh so many parallel 401s trigger only ONE /auth/refresh.
let refreshInFlight: Promise<boolean> | null = null;

/** Attempt a single cookie-based token refresh; shared across callers. */
function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${BASE_URL}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

/**
 * fetch() wrapper that always sends cookies and, on a 401, transparently
 * refreshes the session once and retries the original request.
 */
async function authedFetch(path: string, init: RequestInit): Promise<Response> {
  const withCredentials: RequestInit = { ...init, credentials: "include" };
  let res = await fetch(`${BASE_URL}${API_PREFIX}${path}`, withCredentials);
  if (res.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    const refreshed = await refreshSession();
    if (refreshed) {
      res = await fetch(`${BASE_URL}${API_PREFIX}${path}`, withCredentials);
    }
  }
  return res;
}

/** Parse a response, throwing ApiError (with backend "detail") on failure. */
async function parse<T>(res: Response): Promise<T> {
  // 204 No Content -> nothing to parse.
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);

  if (!res.ok) throw toApiError(res, data);

  return data as T;
}

async function request<T>(
  method: string,
  path: string,
  body?: JsonBody,
): Promise<T> {
  const headers: Record<string, string> = {};

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await authedFetch(path, { method, headers, body: payload });
  return parse<T>(res);
}

/**
 * POST form-url-encoded data. Required by FastAPI's OAuth2 login endpoint,
 * which expects `username` + `password` as form fields (not JSON).
 */
async function requestForm<T>(
  path: string,
  form: Record<string, string>,
): Promise<T> {
  const body = new URLSearchParams(form).toString();
  const res = await authedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return parse<T>(res);
}

/**
 * POST multipart form-data (file uploads). The browser sets the multipart
 * boundary automatically, so we attach only the auth header.
 */
async function requestFile<T>(path: string, formData: FormData): Promise<T> {
  const res = await authedFetch(path, { method: "POST", body: formData });
  return parse<T>(res);
}

/** GET JSON plus the response headers (pagination: X-Total-Count, ...). */
async function requestWithHeaders<T>(path: string): Promise<{ data: T; headers: Headers }> {
  const res = await authedFetch(path, { method: "GET" });
  const data = await parse<T>(res);
  return { data, headers: res.headers };
}

/** GET a raw file/blob (e.g. a CSV template) with the auth header attached. */
async function requestBlob(path: string): Promise<Blob> {
  const res = await authedFetch(path, { method: "GET" });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw toApiError(res, data);
  }
  return res.blob();
}

type StreamEvent = {
  type?: "token" | "done" | "error" | "meta";
  content?: string;
  provider?: string;
  detail?: string;
  conversation_id?: number;
};

/**
 * POST and consume a Server-Sent Events (SSE) stream from the backend.
 *
 * Used by the AI mentor: the backend proxies the AI worker's token stream and
 * we surface each token via `onToken` so the UI renders the answer live. Reuses
 * authedFetch, so a 401 during the handshake still refreshes + retries once.
 *
 * Resolves with the final provider (from the {type:"done"} event) once the
 * stream ends. Throws ApiError on a non-OK response or a {type:"error"} event.
 */
async function requestStream(
  path: string,
  body: JsonBody,
  handlers: { onToken: (text: string) => void; signal?: AbortSignal },
): Promise<{ provider: string | null; conversationId: number | null }> {
  const res = await authedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    throw toApiError(res, data);
  }

  let provider: string | null = null;
  let conversationId: number | null = null;

  // Parse one complete SSE frame ("data: {...}") and dispatch it.
  const processFrame = (frame: string) => {
    const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
    if (!dataLine) return;
    const raw = dataLine.slice(5).trim();
    if (!raw) return;
    let event: StreamEvent;
    try {
      event = JSON.parse(raw) as StreamEvent;
    } catch {
      return;
    }
    if (event.type === "meta") {
      conversationId = event.conversation_id ?? null;
    } else if (event.type === "token" && event.content) {
      handlers.onToken(event.content);
    } else if (event.type === "done") {
      provider = event.provider ?? null;
    } else if (event.type === "error") {
      throw new ApiError(
        502,
        event.detail ?? "AI mentor is unavailable right now.",
      );
    }
  };

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // SSE frames are separated by a blank line ("\n\n"); buffer partial chunks.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    while (buffer.includes("\n\n")) {
      const sep = buffer.indexOf("\n\n");
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      processFrame(frame);
    }
  }
  if (buffer.trim()) processFrame(buffer);

  return { provider, conversationId };
}

/** Thin verb helpers over the backend API. */
export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: JsonBody) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: JsonBody) => request<T>("PATCH", path, body),
  put: <T>(path: string, body?: JsonBody) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  postForm: <T>(path: string, form: Record<string, string>) =>
    requestForm<T>(path, form),
  postFile: <T>(path: string, formData: FormData) =>
    requestFile<T>(path, formData),
  getBlob: (path: string) => requestBlob(path),
  /** GET JSON together with the response headers (e.g. X-Total-Count). */
  getWithHeaders: <T>(path: string) => requestWithHeaders<T>(path),
  /** Log out: revoke the refresh-token family + clear the auth cookies. */
  logout: () => request<void>("POST", "/auth/logout"),
  /** POST + consume a Server-Sent Events token stream (AI mentor). */
  stream: (
    path: string,
    body: JsonBody,
    handlers: { onToken: (text: string) => void; signal?: AbortSignal },
  ) => requestStream(path, body, handlers),
};
