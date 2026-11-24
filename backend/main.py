"""Campus AI - Backend API entrypoint.

Progress:
  * Step 2: config, Supabase Postgres, users + JWT auth.
  * Step 3: Admin - users, departments, sections (RBAC).
  * Attendance: mark/view attendance (teacher marks, students view).
  * Academics: subjects + results + SGPA/CGPA (teacher manages, students view).
  * Skills: claim -> mentor verify/flag (the verified-data moat).
  * Projects: individual/group projects, verified per member.
  * AI auto-scoring of skills/projects via the AI worker (BackgroundTask).
  * AI Career Mentor: grounded chat over the student's verified profile.
  * AI Resume: build a Markdown resume from verified data + ATS score vs a JD.
  * Placement: drives + verified-data eligibility engine.
  * Placement applications: students apply to eligible drives; TPO shortlists.
  * Announcements: admin broadcasts; users read their own role's feed.
  * Academic calendar: admin posts holidays/exams/events; users see their feed.
  * Audit log: append-only trail of admin governance actions.
  * Face attendance: enroll students' reference faces (worker + Qdrant);
    class-photo matching comes next.
  * Assignments: teachers post assignments per section; students submit; teachers grade.
  * Study Hub: teachers upload study materials/notes per section; students browse.
  * Doubt Forum: students post doubts per section; peers/staff answer, upvote & accept.
  * Leave/OD: students apply for leave/on-duty; staff approve; bulk OD for events.
  * Attendance condonation: approved leave/OD excuses matching absences so a
    student's attendance % is never pulled down by official duty/approved leave.
  * Analytics & at-risk: explainable per-student risk (attendance + CGPA +
    submissions) + section dashboards for teachers.

Location:
    E:\\campus-ai\\backend\\main.py

Run (from the backend folder):
    uv run uvicorn main:app --reload --port 8000

Key URLs (feature routes are served under /v1 AND legacy unversioned paths):
    /docs          -> Swagger UI (test everything here)
    /version       -> API build version + supported version prefixes
    /health        -> liveness probe
    /db-check      -> verifies the database connection (dev/test only)
    /auth/*        -> register / login / refresh / logout / me
    /admin/*       -> users, departments & sections
    /attendance/*  -> mark & view attendance + class-photo face match
    /academics/*   -> subjects, results & SGPA/CGPA
    /assignments/* -> create, submit & grade assignments
    /materials/*   -> study hub: upload & browse study materials (section-scoped)
    /doubts/*      -> doubt forum: ask, answer, upvote & accept (section-scoped)
    /timetable/*   -> class timetable: weekly recurring schedule per section
    /leave/*       -> leave & OD requests (apply, approve, bulk OD for events)
    /skills/*      -> claim, verify/flag, view skills
    /projects/*    -> create, verify per member, view projects
    /mentor/*      -> AI career mentor chat
    /resume/*      -> AI resume builder + ATS scoring (student)
    /drives/*      -> placement drives, eligibility, applications & shortlisting
    /placement/analytics/* -> placement funnel, rate, per-drive & company stats
    /recruiters/*  -> recruiter portal: TPO onboards companies; recruiters self-onboard
    /people/*      -> staff roster: list students (optionally by section)
    /face/*        -> face attendance: enroll a student's reference face (staff)
    /announcements/* -> institute broadcasts (admin posts, everyone reads)
    /calendar/*    -> academic calendar (admin manages, everyone reads)
    /audit         -> append-only governance audit log (admin only)
"""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.routers.academics import router as academics_router
from app.routers.admin import router as admin_router
from app.routers.analytics import router as analytics_router
from app.routers.announcements import router as announcements_router
from app.routers.assignments import router as assignments_router
from app.routers.attendance import router as attendance_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.calendar_events import router as calendar_router
from app.core.config import insecure_secret_issues, metrics_token_issue, settings
from app.core.csrf import make_csrf_middleware
from app.core.security_headers import hsts_value, make_security_headers_middleware
from app.core.staging_gate import make_staging_gate_middleware
from app.core.db import engine, runtime_role_issues
from app.observability.errors import register_exception_handlers
from app.observability.health import readiness
from app.observability.metrics import metrics_middleware, render_latest
from app.core.rate_limit import limiter
from app.observability.observability import (
    init_sentry,
    request_logging_middleware,
    setup_logging,
)
from app.routers.doubts import router as doubts_router
from app.routers.eca import router as eca_router
from app.routers.face import router as face_router
from app.routers.files import router as files_router
from app.routers.institute import router as institute_router
from app.routers.internship import router as internship_router
from app.routers.leads import router as leads_router
from app.routers.leaveod import router as leaveod_router
from app.routers.materials import router as materials_router
from app.routers.mentor import router as mentor_router
from app.routers.mock_interview import router as mock_interview_router
from app.routers.mock_interview_admin import router as mock_interview_admin_router
from app.routers.mock_interview_coding import router as mock_interview_coding_router
from app.routers.mock_interview_company import router as mock_interview_company_router
from app.routers.notifications import router as notifications_router
from app.routers.people import router as people_router
from app.routers.placement import router as placement_router
from app.routers.placement_analytics import router as placement_analytics_router
from app.routers.projects import router as projects_router
from app.routers.recruiters import router as recruiters_router
from app.routers.resume import router as resume_router
from app.routers.skills import router as skills_router
from app.routers.tenants import router as tenants_router
from app.routers.timetable import router as timetable_router

# --- Observability: structured logging + (optional) Sentry --------------
# setup_logging() => one JSON log line per request; init_sentry() only turns
# on when SENTRY_DSN is set, so local/CI runs need no DSN or extra package.
API_VERSION = "0.35.0"
setup_logging()
init_sentry(
    settings.SENTRY_DSN,
    settings.ENVIRONMENT,
    release=f"campus-ai-backend@{API_VERSION}",
)

# Secrets gate (§7 'secrets in env'): never run with the throwaway dev JWT key
# or a token-less AI worker. Always warn; in production, refuse to boot so a
# misconfigured deploy fails loudly instead of shipping an insecure default.
_secret_issues = insecure_secret_issues()
if _secret_issues:
    _secret_msg = "Insecure secret configuration: " + " ".join(_secret_issues)
    if settings.ENVIRONMENT == "production":
        raise RuntimeError(
            f"{_secret_msg} Refusing to start in production - set these in the "
            "host environment."
        )
    logging.getLogger("campus-ai").warning(_secret_msg)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Production-only startup gate: refuse a runtime DB role that bypasses RLS."""
    if settings.ENVIRONMENT == "production":
        with engine.connect() as conn:
            role_issues = runtime_role_issues(conn)
        if role_issues:
            raise RuntimeError(
                "Insecure database role: "
                + "; ".join(role_issues)
                + ". Refusing to start in production - use a NOBYPASSRLS app role."
            )
    yield


# Phase 7: interactive docs + the OpenAPI schema exist outside production only.
_DOCS_URL = "/docs" if settings.docs_enabled else None
_REDOC_URL = "/redoc" if settings.docs_enabled else None
_OPENAPI_URL = "/openapi.json" if settings.docs_enabled else None
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=API_VERSION,
    lifespan=_lifespan,
    docs_url=_DOCS_URL,
    redoc_url=_REDOC_URL,
    openapi_url=_OPENAPI_URL,
)

# Rate limiting: per-client-IP quotas on abuse-prone public routes.
# Register the limiter + its HTTP 429 handler here; the actual quotas live as
# @limiter.limit(...) decorators on individual routes (auth/leads/recruiters).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Uniform JSON error envelope (+ request id) for HTTPException, request
# validation, and any unhandled exception. slowapi's 429 handler above stays
# more specific and keeps winning for rate-limit errors.
register_exception_handlers(app)

# Log every request (method, path, status, duration_ms, request_id).
app.middleware("http")(request_logging_middleware)

# Aggregate Prometheus metrics (request count, latency, in-flight) exposed at
# GET /metrics for scraping. No-op when prometheus_client isn't installed.
# Phase 7: off in production (no collection, no route) until monitoring
# access is decided - see Settings.metrics_enabled.
# Phase 7.9: in production, collected and served only with a valid dedicated
# METRICS_TOKEN (see the protected route below); otherwise still off.
_METRICS_TOKEN_ISSUE = metrics_token_issue(settings)
_METRICS_PROTECTED = settings.ENVIRONMENT == "production" and _METRICS_TOKEN_ISSUE is None
if settings.ENVIRONMENT == "production" and settings.METRICS_TOKEN and _METRICS_TOKEN_ISSUE:
    # Fail closed: an unusable token keeps /metrics unregistered (404). The
    # reason is logged, never the value.
    logging.getLogger("campus-ai").warning("/metrics stays disabled: %s", _METRICS_TOKEN_ISSUE)
if settings.metrics_enabled or _METRICS_PROTECTED:
    app.middleware("http")(metrics_middleware)

# CORS: allow the frontend to call the API from the browser. Deployed origins
# come from the CORS_ORIGINS env var (comma-separated).
# Phase 6: the local dev origins are NOT trusted in production - they are
# also the CSRF allow-list, and any page served on a visitor's own
# localhost:3000 would otherwise count as a trusted origin.
_cors_origins = (
    []
    if settings.ENVIRONMENT == "production"
    else ["http://localhost:3000", "http://127.0.0.1:3000"]
)
_cors_origins += [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

# CSRF: reject cookie-capable, state-changing browser requests whose Origin /
# Referer is not one of the CORS origins above (see app/core/csrf.py). Added
# BEFORE CORSMiddleware so CORS stays outermost and still answers preflights.
app.middleware("http")(make_csrf_middleware(_cors_origins))

# Phase 7B: private-staging access gate (off unless STAGING_GATE_TOKEN is set).
# Registered after CSRF (so it runs BEFORE CSRF and every route) and before the
# security-headers middleware (so its 403 still carries the security headers).
if settings.STAGING_GATE_TOKEN.strip():
    app.middleware("http")(
        make_staging_gate_middleware(settings.STAGING_GATE_TOKEN.strip())
    )

# Phase 7: security headers on every response (incl. CSRF 403s and errors).
# Registered AFTER the CSRF middleware so it wraps it; CORS stays outermost.
# The strict API CSP is skipped only on the docs pages, which exist outside
# production only.
app.middleware("http")(
    make_security_headers_middleware(
        docs_paths=frozenset(
            p
            for p in (_DOCS_URL, _REDOC_URL, app.swagger_ui_oauth2_redirect_url)
            if p and settings.docs_enabled
        ),
        hsts=hsts_value(settings.HSTS_MAX_AGE, settings.HSTS_INCLUDE_SUBDOMAINS),
    )
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Feature routers, mounted under BOTH the canonical versioned prefix (/v1,
# e.g. /v1/auth/login) and the legacy unversioned paths. The unversioned
# copies are marked deprecated in the OpenAPI schema but keep working so
# existing clients (frontend, integrations) don't break while they migrate to
# /v1. Add a new module to FEATURE_ROUTERS once - it gets both mounts.
FEATURE_ROUTERS = [
    auth_router,
    admin_router,
    announcements_router,
    calendar_router,
    audit_router,
    attendance_router,
    academics_router,
    analytics_router,
    assignments_router,
    materials_router,
    doubts_router,
    skills_router,
    eca_router,
    internship_router,
    institute_router,
    projects_router,
    mentor_router,
    notifications_router,
    resume_router,
    placement_router,
    placement_analytics_router,
    recruiters_router,
    leads_router,
    people_router,
    face_router,
    files_router,
    timetable_router,
    leaveod_router,
    tenants_router,
    # AI Mock Interview: feature-flagged (AI_MOCK_INTERVIEW_ENABLED, default
    # off). Only /mock-interview/status answers while it is off.
    mock_interview_router,
    # Phase 7.7: institute operations (admin/TPO), 404 unless
    # AI_MOCK_INTERVIEW_OPS_ENABLED (default off) is on for the institute.
    mock_interview_admin_router,
    # Phase 8.1: coding round (review-only; flag-gated, 404 when off).
    mock_interview_coding_router,
    # Phase 8.4: company profiles (AI_MOCK_INTERVIEW_COMPANY_ENABLED; 404 when off).
    mock_interview_company_router,
]

for _feature_router in FEATURE_ROUTERS:
    # Canonical, versioned API surface: /v1/...
    app.include_router(_feature_router, prefix=settings.API_V1_PREFIX)
    # Legacy unversioned alias (deprecated) - existing clients keep working.
    app.include_router(_feature_router, deprecated=True)


@app.get("/")
def root() -> dict:
    """Root endpoint - quick sanity check that the API process is alive."""
    return {"app": settings.PROJECT_NAME, "status": "ok"}


@app.get("/version")
def version() -> dict:
    """Report the API build version and the supported API version prefixes.

    Clients should call the versioned surface (e.g. /v1/...); the unversioned
    paths are deprecated aliases kept for backward compatibility.
    """
    return {
        "version": API_VERSION,
        "api_versions": ["v1"],
        "current": settings.API_V1_PREFIX,
    }


@app.get("/health")
def health() -> dict:
    """Liveness probe for deploys/monitoring. Dependency-free and fast."""
    return {"status": "healthy"}


if settings.ENVIRONMENT != "production":

    @app.get("/db-check", include_in_schema=False)
    def db_check() -> dict:
        """Dev-only DB probe (Phase 7A: not registered in production).

        Production health checks use /health (liveness) and /health/ready
        (readiness, DB required) instead.
        """
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"database": "connected"}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    """Readiness probe: 200 when required deps (DB) are reachable, else 503.

    Redis is reported for visibility but never fails readiness (it degrades to
    an in-process no-op). See health.py for the policy.
    """
    report, ready = readiness()
    return JSONResponse(status_code=200 if ready else 503, content=report)


if settings.metrics_enabled:

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        """Prometheus scrape endpoint: aggregate request counters + latency.

        Not registered in production (Phase 7) - returns 404 there.
        """
        body, content_type = render_latest()
        return Response(content=body, media_type=content_type)

elif _METRICS_PROTECTED:

    @app.get("/metrics", include_in_schema=False)
    def metrics_protected(request: Request) -> Response:
        """Production scrape endpoint (Phase 7.9): ``Authorization: Bearer
        <METRICS_TOKEN>`` required, compared in constant time. The token is
        never logged or echoed; a wrong or missing token gets a bare 401."""
        auth = request.headers.get("authorization") or ""
        scheme, _, provided = auth.partition(" ")
        expected = settings.METRICS_TOKEN.encode("utf-8")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            provided.strip().encode("utf-8"), expected
        ):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        body, content_type = render_latest()
        return Response(
            content=body, media_type=content_type, headers={"Cache-Control": "no-store"}
        )
