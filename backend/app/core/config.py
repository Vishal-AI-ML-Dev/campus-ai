"""Application configuration.

Values are read from environment variables and, in local development, from a
`.env` file next to this module (validated/typed via pydantic-settings).

Security note (Phase 6): `SECRET_KEY` has NO code default - it must come
from the environment / `.env` everywhere, so a deployment that forgets it fails
to start instead of signing tokens with a published key. `ENVIRONMENT` only
accepts development | test | production, so a typo such as "prod" cannot
silently fall back to development behaviour (non-Secure cookies, no secrets
gate).
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ENVIRONMENTS = ("development", "test", "production")
# HS256 key length below which a SECRET_KEY counts as weak (32 bytes = 256 bit).
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Campus AI API"
    ENVIRONMENT: str = "development"

    @field_validator("ENVIRONMENT")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        """Normalise case and refuse unknown names (fail closed on typos)."""
        env = (value or "").strip().lower()
        if env not in ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(ALLOWED_ENVIRONMENTS)}"
            )
        return env

    # --- API versioning ---------------------------------------------------
    # Canonical prefix for all feature routes, e.g. /v1/auth/login. Feature
    # routers are ALSO mounted at the legacy unversioned paths (deprecated in
    # the OpenAPI schema) so existing clients keep working while they migrate
    # to /v1. Ops endpoints (/health, /metrics, /version, ...) stay unversioned.
    API_V1_PREFIX: str = "/v1"

    # --- Database ---------------------------------------------------------
    # Runtime connection string. MUST point at a NOBYPASSRLS role in prod
    # (e.g. app_user) so Row-Level Security actually filters rows. The
    # default Supabase `postgres` role has BYPASSRLS and silently ignores
    # all policies.
    DATABASE_URL: str

    # Owner-role URL used ONLY by Alembic migrations (DDL: create tables,
    # policies, FORCE RLS). Needs the table owner (Supabase `postgres`).
    # Falls back to DATABASE_URL when unset (e.g. local SQLite tests).
    MIGRATION_DATABASE_URL: str | None = None

    # --- Auth / JWT -------------------------------------------------------
    # REQUIRED (no default). Generate: `python -c "import secrets; print(secrets.token_hex(32))"`
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    # Refresh tokens (httpOnly cookie) live much longer than the access token
    # and are single-use / rotated on every /auth/refresh (see security.py).
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- Auth cookies -----------------------------------------------------
    # On login/refresh the access + refresh tokens are ALSO set as httpOnly
    # cookies (the Authorization: Bearer header keeps working too, for API
    # clients). Cross-site browser cookies (Vercel frontend -> Render API are
    # DIFFERENT sites) require SameSite=None + Secure. Leaving these unset
    # auto-picks safe values by environment: production -> SameSite=None +
    # Secure; otherwise SameSite=Lax + non-Secure so http://localhost dev works.
    COOKIE_SECURE: bool | None = None
    COOKIE_SAMESITE: str | None = None
    # Optional explicit cookie domain (e.g. ".example.com"). Empty = host-only.
    COOKIE_DOMAIN: str = ""

    # --- AI worker --------------------------------------------------------
    # Base URL of the separate AI worker service (proof scoring, mentor, ...).
    # Backend calls this over HTTP in a background task to fill `ai_score`.
    AI_WORKER_URL: str = "http://127.0.0.1:8100"

    # Shared secret sent to the AI worker (X-Worker-Token header) so a public
    # worker (e.g. Hugging Face Space) only answers our backend. Empty = off.
    AI_WORKER_TOKEN: str = ""

    # --- Redis (rate limiting + caching) ----------------------------------
    # Shared store used by BOTH the slowapi rate limiter (so per-IP counters
    # are consistent across every instance) and the app-level read cache
    # (see cache.py). Leave EMPTY to fall back to in-process behaviour:
    #   * rate limits count per-instance (fine for a single Render dyno)
    #   * the cache becomes a no-op (every call is a miss)
    # This keeps local dev and the test-suite zero-infra. Set it in the host
    # env (e.g. redis://:pass@host:6379/0) the moment you run >1 instance.
    REDIS_URL: str = ""

    # --- Client IP behind proxies (rate limiting) -------------------------
    # Number of reverse proxies in front of the API that APPEND the client
    # address to X-Forwarded-For (e.g. 1 behind a single load balancer).
    # 0 (default) = never trust X-Forwarded-For; key on the socket peer. With
    # N > 0 the N-th entry from the RIGHT is used - entries further left are
    # client-supplied and spoofable, so they are ignored.
    TRUSTED_PROXY_HOPS: int = Field(default=0, ge=0, le=10)

    # --- CORS -------------------------------------------------------------
    # Comma-separated list of allowed frontend origins for production, e.g.
    # "https://app.example.com". Local dev origins are always allowed
    # (see main.py). Set this in the host environment to your frontend origin.
    CORS_ORIGINS: str = ""

    # --- Staging access gate (Phase 7B) ----------------------------------
    # When set, EVERY request except GET /health must carry the header
    # X-Staging-Gate with this exact value (constant-time compare), else 403.
    # The staging frontend's server-side proxy adds it; browsers never see it.
    # Empty (default) = gate off. In production it must be >= 32 chars if set.
    STAGING_GATE_TOKEN: str = ""

    # --- HTTP security headers (Phase 7) ----------------------------------
    # Strict-Transport-Security max-age in seconds. 0 (default) = header NOT
    # sent. Enable ONLY once the API is reachable exclusively over HTTPS on its
    # final domain (e.g. 31536000 = 1 year after a short trial value such as
    # 300). includeSubDomains is a separate, deliberate opt-in.
    HSTS_MAX_AGE: int = Field(default=0, ge=0, le=63072000)
    HSTS_INCLUDE_SUBDOMAINS: bool = False

    # --- Observability ----------------------------------------------------
    # Sentry error tracking DSN. Empty = disabled (app runs fine without it).
    # Set this in the Render env to turn on production error capture.
    SENTRY_DSN: str = ""

    # Optional alert webhook (Slack/Discord/generic). When set, alerts.py POSTs
    # a JSON {"text": ...} payload here (e.g. a Slack Incoming Webhook URL) in
    # addition to always logging a structured `campus.alert` line. Empty = off.
    ALERT_WEBHOOK_URL: str = ""

    # --- File storage (Supabase Storage, signed URLs) ---------------------
    # Direct browser<->Supabase file uploads/downloads via short-lived signed
    # URLs; the backend only signs them. Leave all three empty to keep the
    # feature OFF (the /files routes return 503) - the rest of the app is
    # unaffected. Turn it on by setting these in the host env.
    #   SUPABASE_URL           project base URL, e.g. https://<ref>.supabase.co
    #   SUPABASE_SERVICE_KEY   service_role key (SERVER-ONLY, never exposed)
    #   SUPABASE_STORAGE_BUCKET  name of the PRIVATE bucket uploads land in
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "campus-uploads"

    # --- AI Mock Interview (feature flag, OFF by default) -----------------
    # Master switch. While False every /mock-interview/* route except
    # /mock-interview/status returns 404 and the rest of the app is untouched.
    # Turning it off again is the rollback: no code or data is removed.
    AI_MOCK_INTERVIEW_ENABLED: bool = False
    # Optional pilot allow-list of tenant ids, comma-separated (e.g. "3,7").
    # Empty = every institute once the master switch is on. Entries that are
    # not integers are ignored; if none are valid, NO institute is enabled
    # (fail closed). See app/routers/mock_interview.py.
    AI_MOCK_INTERVIEW_TENANT_IDS: str = ""
    # Phase 5 sub-flag: offer voice interviews (browser speech-to-text and
    # text-to-speech; the server only ever receives text). Needs the master
    # switch too. Off = text only; voice sessions already created can finish.
    AI_MOCK_INTERVIEW_VOICE_ENABLED: bool = False
    # Phase 6 sub-flag: offer video interviews (camera self-view that stays in
    # the browser; the server only ever receives text). Needs the master switch
    # and master allow-list too, AND the institute must be listed in
    # AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS. Unlike the master list, an EMPTY
    # video list enables NO institute (fail closed). Students only.
    AI_MOCK_INTERVIEW_VIDEO_ENABLED: bool = False
    AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS: str = ""
    # Phase 8.1 sub-flag: offer coding rounds (editor + review-only mode; no
    # code is ever executed by this service). Needs the master switch and
    # master allow-list too, AND the institute must be listed in
    # AI_MOCK_INTERVIEW_CODING_TENANT_IDS. Like video, an EMPTY coding list
    # enables NO institute (fail closed). Students only.
    AI_MOCK_INTERVIEW_CODING_ENABLED: bool = False
    AI_MOCK_INTERVIEW_CODING_TENANT_IDS: str = ""
    # Development only (decision B1-b): also offer problems whose status is
    # "draft" (not yet human-reviewed). Ignored when ENVIRONMENT=production,
    # so production only ever serves "published" problems.
    AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS: bool = False
    # Phase 8.3 sub-flag: AI help in coding rounds (review-only, decision
    # P8-D7 "coding-v1-unverified"): AI-tailored hints after the authored ones,
    # typed explanation questions after "Done", and the AI-estimate coding
    # feedback (/sessions/{id}/coding/feedback). Needs the coding gate too and
    # the worker's MOCK_INTERVIEW_CODING_ENABLED. Off = Batch 1 behaviour
    # (authored hints only, no explanation turns, no coding feedback). Nothing
    # is ever executed either way.
    AI_MOCK_INTERVIEW_CODING_AI_ENABLED: bool = False
    # Phase 8.4 sub-flag: institute company profiles (admin/TPO authoring,
    # students pick a published profile of their own institute) and
    # company-context question generation (decisions P8-D9..D11). Needs the
    # master switch and allow-list. Off = the company routes 404 and create
    # rejects a company profile. Evaluation is never affected.
    AI_MOCK_INTERVIEW_COMPANY_ENABLED: bool = False

    # --- AI Mock Interview: production operations (Phase 7) --------------
    # Institute operations: admin/TPO usage + controls API, institute quotas,
    # GET /mock-interview/usage/me. OFF = those routes 404 and no quota beyond
    # the existing per-student limits is enforced. Needs the master switch too.
    AI_MOCK_INTERVIEW_OPS_ENABLED: bool = False
    # Optional platform ceilings for institute quotas, as positive integers
    # ("" = no cap). An institute can only set lower values (Phase 7.6).
    AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX: str = ""
    AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX: str = ""
    # Token budgets count only provider-reported tokens (decision 2026-11-23,
    # spec 15.12 option a; the session cap is the hard limit). Warn (never
    # block) when at least this percent of the month's AI calls - once there
    # are MIN_CALLS of them - had unknown or partial usage. Checked only when
    # an institute token budget applies. 0 = no alert.
    AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT: int = Field(default=10, ge=0, le=100)
    AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS: int = Field(default=20, ge=1, le=100000)
    # Approved model price table (JSON, see docs/mock-interview/runbooks/
    # pricing.md). Empty = cost "pricing not configured": tokens are still
    # recorded, cost stays null. Prices are never invented (decision P7-D9).
    AI_PRICE_TABLE: str = ""
    # Circuit breaker for interview AI calls - ON by default (approved
    # exception P7-D4, conservative thresholds). Inert while
    # AI_MOCK_INTERVIEW_ENABLED is off; AI_CIRCUIT_ENABLED=false disables it.
    AI_CIRCUIT_ENABLED: bool = True
    AI_CIRCUIT_FAILURE_THRESHOLD: int = Field(default=5, ge=1, le=1000)
    AI_CIRCUIT_ERROR_RATE: float = Field(default=0.5, gt=0, le=1)
    AI_CIRCUIT_MIN_CALLS: int = Field(default=10, ge=1, le=10000)
    AI_CIRCUIT_WINDOW_SECONDS: int = Field(default=120, ge=10, le=86400)
    AI_CIRCUIT_OPEN_SECONDS: int = Field(default=60, ge=5, le=3600)
    # Minimum gap between two alerts with the same name/key (Phase 7.5).
    AI_ALERT_THROTTLE_SECONDS: int = Field(default=300, ge=0, le=86400)
    # Phase 7.9: dedicated bearer token for GET /metrics in production
    # (>= 32 chars, different from every other secret). Empty or invalid =
    # /metrics stays unregistered (404) in production. Ignored elsewhere.
    METRICS_TOKEN: str = ""

    # --- Placement management (Phase 9, feature flag, OFF by default) -----
    # Placement core v2: student placement profiles, drive settings and
    # lifecycle, eligibility v2, apply/status rules with history. Needs the
    # institute in PLACEMENT_MANAGEMENT_TENANT_IDS too; an EMPTY or invalid
    # list enables NO institute (fail closed). Off = every /placement/* v2
    # route 404s and the legacy /drives routes behave exactly as before
    # (decisions P9-D1, P9-D6; docs/mock-interview/specs/phase-9-...md).
    PLACEMENT_MANAGEMENT_ENABLED: bool = False
    PLACEMENT_MANAGEMENT_TENANT_IDS: str = ""

    @property
    def docs_enabled(self) -> bool:
        """Swagger UI, ReDoc and /openapi.json (Phase 7).

        Available in development/test only; never served in production, so the
        full route map and schemas are not published. Not overridable.
        """
        return self.ENVIRONMENT != "production"

    @property
    def metrics_enabled(self) -> bool:
        """Prometheus /metrics (Phase 7).

        Off in production until an access-controlled scrape path is decided
        (it would otherwise publish route names, traffic and error rates to
        anyone). On in development/test. Not overridable for now.
        """
        return self.ENVIRONMENT != "production"

    @property
    def cookie_secure(self) -> bool:
        """Whether auth cookies get the Secure flag (HTTPS-only).

        Explicit COOKIE_SECURE wins; otherwise Secure is on in production and
        off elsewhere so local http dev still receives the cookies.
        """
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.ENVIRONMENT == "production"

    @property
    def cookie_samesite(self) -> str:
        """SameSite policy for auth cookies.

        Explicit COOKIE_SAMESITE wins; otherwise cross-site 'none' in production
        (frontend and API are on different domains) and 'lax' in dev. Note that
        SameSite=None is only honoured by browsers together with Secure=True.
        """
        if self.COOKIE_SAMESITE:
            return self.COOKIE_SAMESITE.lower()
        return "none" if self.ENVIRONMENT == "production" else "lax"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()


settings = get_settings()


# Values that must never be used as real production secrets.
_INSECURE_SECRET_KEYS = {"", "dev-secret-change-me", "changeme", "secret"}


def metrics_token_issue(s: Settings = settings) -> str | None:
    """Why METRICS_TOKEN cannot protect /metrics (None = usable). Phase 7.9.

    The token must be a dedicated secret: >= 32 characters, no whitespace,
    not a known placeholder, and different from every other secret. The
    message never contains the value.
    """
    token = s.METRICS_TOKEN
    if not token:
        return "METRICS_TOKEN is not set"
    if token != token.strip() or any(ch.isspace() for ch in token):
        return "METRICS_TOKEN contains whitespace"
    if len(token) < MIN_SECRET_KEY_LENGTH or token in _INSECURE_SECRET_KEYS:
        return f"METRICS_TOKEN is shorter than {MIN_SECRET_KEY_LENGTH} characters or a placeholder"
    others = (s.SECRET_KEY, s.AI_WORKER_TOKEN, s.STAGING_GATE_TOKEN, s.SUPABASE_SERVICE_KEY)
    if any(token == (other or "").strip() for other in others if other):
        return "METRICS_TOKEN must be different from every other secret"
    return None


def insecure_secret_issues(s: Settings = settings) -> list[str]:
    """Return a list of weak/missing secret problems (empty list = all good).

    Called at startup (see main.py): the issues are always logged, and in
    production we refuse to boot - so a real deployment can never silently run
    with the throwaway dev JWT key or a token-less (open) AI worker. This is
    the §7 'secrets in env' gate: secrets must come from the host env, not the
    insecure code defaults.
    """
    issues: list[str] = []
    key = s.SECRET_KEY.strip()
    if key in _INSECURE_SECRET_KEYS or len(key) < MIN_SECRET_KEY_LENGTH:
        issues.append(
            "SECRET_KEY is a known placeholder or shorter than "
            f"{MIN_SECRET_KEY_LENGTH} characters - set a strong random "
            'value (python -c "import secrets; print(secrets.token_hex(32))").'
        )
    gate = s.STAGING_GATE_TOKEN.strip()
    if gate and len(gate) < MIN_SECRET_KEY_LENGTH:
        issues.append(
            f"STAGING_GATE_TOKEN is set but shorter than {MIN_SECRET_KEY_LENGTH} "
            "characters - use a long random value."
        )
    if not s.AI_WORKER_TOKEN.strip():
        issues.append(
            "AI_WORKER_TOKEN is empty - the AI worker would answer anyone; set "
            "the same shared secret here and on the worker."
        )
    return issues
