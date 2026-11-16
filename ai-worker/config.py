"""AI Worker configuration (env-driven).

Provider-agnostic: the worker can talk to either Google Gemini or Groq, chosen
at runtime via `LLM_PROVIDER`. You only need an API key for the provider(s) you
actually use.

Location:
    E:\\campus-ai\\ai-worker\\config.py
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ENVIRONMENTS = ("development", "test", "production")
# Shared-secret length below which a production worker refuses to boot.
MIN_WORKER_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    """Loaded from environment / a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Which LLM backend to use: "groq" or "gemini".
    LLM_PROVIDER: str = "groq"

    # API keys (only the selected provider's key is required at call time).
    GROQ_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None

    # Default models per provider (override in .env if you like).
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # Lower temperature -> more consistent, less 'creative' scoring.
    LLM_TEMPERATURE: float = 0.2

    # Shared secret. When set, every request except the open paths (/health, /)
    # must send a matching X-Worker-Token header (constant-time compare).
    # Empty = check disabled - allowed in development/test ONLY. In production
    # the worker refuses to start unless this is >= 32 characters (Phase 7).
    AI_WORKER_TOKEN: str = ""

    # --- Observability ----------------------------------------------------
    # Deploy environment label + optional Sentry error tracking.
    # SENTRY_DSN empty = disabled (worker runs fine without it).
    ENVIRONMENT: str = "development"
    SENTRY_DSN: str = ""

    @field_validator("ENVIRONMENT")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        """Normalise case and refuse unknown names (fail closed on typos such
        as "prod", which would otherwise silently skip the production gates)."""
        env = (value or "").strip().lower()
        if env not in ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(ALLOWED_ENVIRONMENTS)}"
            )
        return env

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    # --- Langfuse LLM tracing (optional) ----------------------------------
    # When both keys are set, every LLM call (mentor/resume/ATS/scoring) is
    # traced to Langfuse. Empty = disabled (worker runs fine without it).
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"


def insecure_worker_issues(s: "Settings") -> list[str]:
    """Problems that make a PRODUCTION worker unsafe to start (empty = ok)."""
    issues: list[str] = []
    if len(s.AI_WORKER_TOKEN.strip()) < MIN_WORKER_TOKEN_LENGTH:
        issues.append(
            "AI_WORKER_TOKEN is empty or shorter than "
            f"{MIN_WORKER_TOKEN_LENGTH} characters - the worker would answer "
            "anyone (or a guessable secret)."
        )
    return issues


settings = Settings()
