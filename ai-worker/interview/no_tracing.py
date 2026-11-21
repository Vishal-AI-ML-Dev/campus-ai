"""Hard exclusion of the Mock Interview engine from LLM tracing (Phase 7.1, R1).

Interview prompts contain resumes, job descriptions and candidate answers, so
NO interview step may reach Langfuse (or any other LangChain tracer). This is
unconditional: there is no flag, setting or environment variable that turns
it back on, and the presence of LANGFUSE_* keys changes nothing here.

Two paths are closed:

1. **Callbacks.** Langfuse attaches to LangChain only as a callback handler
   (``tracing.trace_config``). Interview calls never use ``trace_config`` and
   pass ``callbacks=[]``. Because LangGraph *merges* an inherited callback
   manager with that empty list, ``tracing_disabled()`` also detaches the
   call from any parent runnable context, so it always starts as a root run.
2. **Environment tracing.** LangChain adds its LangSmith tracer on its own
   when ``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2`` is set. Interview
   calls and graph runs execute inside ``tracing_disabled()``, which forces
   that off for the current context (it overrides the environment).

The installed langfuse package (4.x) registers no LangChain configure hook
and auto-instruments nothing; its OpenTelemetry exporter only exports
Langfuse/gen_ai spans, and no OpenTelemetry LLM instrumentation is installed.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


def untraced_config() -> dict[str, Any]:
    """RunnableConfig for an interview LLM call or graph run: no callbacks."""
    return {"callbacks": []}


@contextmanager
def tracing_disabled() -> Iterator[None]:
    """Force LangChain/LangSmith tracing off for the current context and
    detach from any parent runnable, so no inherited callback manager (which
    LangGraph would MERGE with ``callbacks=[]``) can see interview runs."""
    with _detached_from_parent_runnable(), _langsmith_off(), _tracing_v2_var_off():
        yield


@contextmanager
def _detached_from_parent_runnable() -> Iterator[None]:
    try:
        from langchain_core.runnables.config import var_child_runnable_config
    except Exception:  # noqa: BLE001
        yield
        return
    token = var_child_runnable_config.set(None)
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


@contextmanager
def _langsmith_off() -> Iterator[None]:
    try:
        from langsmith.run_helpers import tracing_context
    except Exception:  # noqa: BLE001 - langsmith absent: nothing to disable
        yield
        return
    with tracing_context(enabled=False):
        yield


@contextmanager
def _tracing_v2_var_off() -> Iterator[None]:
    try:
        from langchain_core.tracers.context import tracing_v2_callback_var
    except Exception:  # noqa: BLE001
        yield
        return
    token = tracing_v2_callback_var.set(None)
    try:
        yield
    finally:
        tracing_v2_callback_var.reset(token)
