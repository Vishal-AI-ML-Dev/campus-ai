"""LLM factory - returns a LangChain chat model for the configured provider.

Keeping this in one place means the rest of the worker (scoring, mentor, future
LangGraph agents) never cares which provider is active - just call
`get_chat_model()`.

Location:
    E:\\campus-ai\\ai-worker\\llm.py
"""

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from config import settings


def _budget_kwargs(timeout_name: str, retries_name: str) -> dict:
    """Phase 10.3: explicit provider timeout / retry count when configured."""
    kwargs: dict = {}
    if settings.LLM_TIMEOUT_SECONDS is not None:
        kwargs[timeout_name] = float(settings.LLM_TIMEOUT_SECONDS)
    if settings.LLM_MAX_RETRIES is not None:
        kwargs[retries_name] = int(settings.LLM_MAX_RETRIES)
    return kwargs


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """Build (once) the chat model for `settings.LLM_PROVIDER`.

    Imports are done lazily so you only need the SDK / key for the provider you
    actually use.
    """
    provider = settings.LLM_PROVIDER.strip().lower()

    if provider == "groq":
        from langchain_groq import ChatGroq

        if not settings.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set (.env) but LLM_PROVIDER=groq")
        return ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            **_budget_kwargs("timeout", "max_retries"),
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.GOOGLE_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set (.env) but LLM_PROVIDER=gemini"
            )
        return ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            **_budget_kwargs("request_timeout", "retries"),
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER {settings.LLM_PROVIDER!r} (use 'groq' or 'gemini')"
    )


def get_structured_model(schema: type[BaseModel]) -> Runnable:
    """Return a runnable that emits an instance of `schema` directly.

    Wraps the active chat model with the provider's native structured-output
    support (function calling / JSON mode) so callers get a validated Pydantic
    object instead of parsing JSON out of free text. Callers should still guard
    `.invoke()` and keep a fallback, because not every provider/model guarantees
    perfect schema adherence.
    """
    return get_chat_model().with_structured_output(schema)
