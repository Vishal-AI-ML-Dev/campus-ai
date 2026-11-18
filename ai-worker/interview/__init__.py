"""AI Mock Interview engine (feature-flagged; see config.MOCK_INTERVIEW_ENABLED).

Phase 1 only exposes a liveness route. The stateless LangGraph engine
(plan -> question -> evaluate -> follow-up -> report) arrives in Phase 3.
"""

from interview.router import router

__all__ = ["router"]
