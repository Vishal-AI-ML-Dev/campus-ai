"""Campus AI - AI Worker service entrypoint.

A SEPARATE FastAPI service (port 8100), decoupled from the main backend
(port 8000), that hosts the heavy/latency-prone AI work:
  * proof scoring for skills & project contributions (fills `ai_score`)
  * AI career mentor: grounded chat over the student's VERIFIED profile,
    augmented with vector RAG over a career knowledge base (persistent Qdrant)
  * AI resume builder (from the verified profile) + ATS scoring vs a job description
  * face attendance: detect + embed faces (InsightFace) and match a class
    photo against enrolled students stored in Qdrant (teacher confirms result)

Provider-agnostic chat: switch between Groq and Gemini via LLM_PROVIDER in .env.
RAG embeddings run locally (fastembed ONNX model) - no extra key or service.

Location:
    E:\\campus-ai\\ai-worker\\main.py

Run (from the ai-worker folder):
    uv run uvicorn main:app --reload --port 8100
"""

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config import insecure_worker_issues, metrics_token_issue, settings
from errors import register_exception_handlers
from metrics import metrics_middleware, render_latest
from observability import (
    init_sentry,
    request_logging_middleware,
    setup_logging,
)
from tracing import init_tracing
from correlation import invocation_id_middleware
from face import router as face_router
from mentor import router as mentor_router
from resume import router as resume_router
from scoring import router as scoring_router

# --- Observability: structured logging + (optional) Sentry --------------
# setup_logging() => one JSON log line per request; init_sentry() only turns
# on when SENTRY_DSN is set, so local/CI runs need no DSN or extra package.
WORKER_VERSION = "0.9.0"
setup_logging()
init_sentry(
    settings.SENTRY_DSN,
    settings.ENVIRONMENT,
    release=f"campus-ai-worker@{WORKER_VERSION}",
)
# Langfuse LLM tracing: only turns on when LANGFUSE_* keys are set (no-op otherwise).
init_tracing(
    settings.LANGFUSE_PUBLIC_KEY,
    settings.LANGFUSE_SECRET_KEY,
    settings.LANGFUSE_HOST,
)

# Phase 7 production gate: never serve with an open / weak shared secret.
if settings.is_production:
    _issues = insecure_worker_issues(settings)
    if _issues:
        raise RuntimeError(
            "Insecure AI worker configuration: " + " ".join(_issues)
            + " Refusing to start in production."
        )

# Phase 7: docs + OpenAPI schema outside production only; /metrics likewise
# (off in production until an access-controlled scrape path is decided).
_DOCS_ON = not settings.is_production
_METRICS_ON = not settings.is_production
# Phase 7.9: in production /metrics exists only with a valid dedicated
# METRICS_TOKEN; it is then exempt from the worker token and checks its own.
_METRICS_TOKEN_ISSUE = metrics_token_issue(settings)
_METRICS_PROTECTED = settings.is_production and _METRICS_TOKEN_ISSUE is None
if settings.is_production and settings.METRICS_TOKEN and _METRICS_TOKEN_ISSUE:
    logging.getLogger("campus_ai_worker").warning("/metrics stays disabled: %s", _METRICS_TOKEN_ISSUE)

app = FastAPI(
    title="Campus AI - AI Worker",
    version=WORKER_VERSION,
    docs_url="/docs" if _DOCS_ON else None,
    redoc_url="/redoc" if _DOCS_ON else None,
    openapi_url="/openapi.json" if _DOCS_ON else None,
)

# Log every request (method, path, status, duration_ms, request_id).
app.middleware("http")(request_logging_middleware)
# Phase 7.1: X-AI-Invocation-ID -> invocation_id on every log line. Registered
# after (= outside) the logging middleware so the access log carries it too.
app.middleware("http")(invocation_id_middleware)

# Aggregate Prometheus metrics at GET /metrics (no-op if prometheus_client
# isn't installed). /metrics is an open path (see _OPEN_PATHS) so a scraper
# doesn't need the worker token.
if _METRICS_ON or _METRICS_PROTECTED:
    app.middleware("http")(metrics_middleware)

# Uniform JSON error envelope (+ request id) for every failure kind.
register_exception_handlers(app)

# Endpoints that stay open (no token): liveness, sanity, API docs. CORS
# preflight (OPTIONS) is also allowed through. Everything else requires the
# shared secret WHEN one is configured (AI_WORKER_TOKEN set).
# Phase 7: in production only liveness + root are open (docs/metrics do not
# exist there); the comparison is constant-time.
_OPEN_PATHS = frozenset(
    {"/", "/health"}
    | ({"/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"} if _DOCS_ON else set())
    | ({"/metrics"} if (_METRICS_ON or _METRICS_PROTECTED) else set())
)


def worker_token_ok(provided: str | None, expected: str) -> bool:
    """Constant-time check of the X-Worker-Token header (bytes compare)."""
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@app.middleware("http")
async def _verify_worker_token(request: Request, call_next):
    token = settings.AI_WORKER_TOKEN
    if token and request.method != "OPTIONS" and request.url.path not in _OPEN_PATHS:
        if not worker_token_ok(request.headers.get("X-Worker-Token"), token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing worker token"},
            )
    return await call_next(request)


app.include_router(scoring_router)
app.include_router(mentor_router)
app.include_router(resume_router)
app.include_router(face_router)

# AI Mock Interview engine: mounted ONLY when MOCK_INTERVIEW_ENABLED is true
# (default off). Its routes sit behind the worker-token middleware above.
if settings.MOCK_INTERVIEW_ENABLED:
    from interview import router as interview_router

    app.include_router(interview_router)

    # Phase 8.3: AI coding review (review-only; reads code, never runs it).
    # Mounted only when its own flag is on too (default off).
    if settings.MOCK_INTERVIEW_CODING_ENABLED:
        from interview.coding.router import router as coding_router

        app.include_router(coding_router)

    # Phase 8.4: company context (JD skill map; the claim filter runs in the
    # interview engine whenever company context is used). Default off.
    if settings.MOCK_INTERVIEW_COMPANY_ENABLED:
        from interview.company import router as company_router

        app.include_router(company_router)


@app.get("/")
def root() -> dict:
    """Quick sanity check that the AI worker process is alive."""
    return {
        "service": "campus-ai-ai-worker",
        "status": "ok",
        "llm_provider": settings.LLM_PROVIDER,
    }


@app.get("/health")
def health() -> dict:
    """Liveness probe for deploys/monitoring."""
    return {"status": "healthy"}


if _METRICS_ON:

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        """Prometheus scrape endpoint (not registered in production, Phase 7)."""
        body, content_type = render_latest()
        return Response(content=body, media_type=content_type)

elif _METRICS_PROTECTED:

    @app.get("/metrics", include_in_schema=False)
    def metrics_protected(request: Request) -> Response:
        """Production scrape endpoint (Phase 7.9): Bearer METRICS_TOKEN,
        constant-time compare; 401 without detail otherwise."""
        auth = request.headers.get("authorization") or ""
        scheme, _, provided = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            provided.strip().encode("utf-8"), settings.METRICS_TOKEN.encode("utf-8")
        ):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        body, content_type = render_latest()
        return Response(content=body, media_type=content_type, headers={"Cache-Control": "no-store"})
