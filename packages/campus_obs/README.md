# campus_obs - shared observability (single source of truth)

These modules are **identical** across the backend and the AI worker. To avoid
maintaining two copies, the canonical versions live HERE and are propagated by a
sync script.

## Files
- `observability.py` - JSON structured logging + request_id context + optional Sentry
- `errors.py` - uniform JSON error envelope + exception handlers
- `metrics.py` - Prometheus request counter / latency histogram / in-flight gauge

## Rules
1. **Edit ONLY these files.** Never edit the copies in `backend/app/observability/`
   or `ai-worker/` directly.
2. After editing, run: `python scripts/sync_observability.py`
3. CI / pre-commit should run `python scripts/sync_observability.py --check`
   (exits non-zero if any copy drifts).

## Why copies (not a pip package)?
Backend (Render) and ai-worker (HF Space) deploy from separate build contexts.
Copying keeps both deploy pipelines unchanged (zero config change) while still
giving a single place to edit. `errors.py` uses a dual-layout import so the exact
same file works whether it sits in the `app.observability` package (backend) or
as a flat module (ai-worker).
