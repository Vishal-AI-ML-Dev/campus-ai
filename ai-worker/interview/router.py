"""HTTP routes for the AI Mock Interview engine.

Mounted by main.py only when MOCK_INTERVIEW_ENABLED is true, and protected by
the worker's X-Worker-Token middleware like every other non-open route.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/interview", tags=["interview"])


@router.get("/ping")
def interview_ping() -> dict:
    """Confirms the interview engine is mounted (no LLM call)."""
    return {"engine": "mock-interview", "status": "ok"}
