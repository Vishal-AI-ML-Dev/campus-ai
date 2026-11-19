"""AI Mock Interview engine (feature-flagged; see config.MOCK_INTERVIEW_ENABLED).

Stateless LangGraph engine (Phase 3): ``graph.plan_graph`` plans topics and
``graph.turn_graph`` runs one interview step. See
docs/mock-interview/AI_INTERVIEW_ARCHITECTURE.md.
"""

from interview.router import router

__all__ = ["router"]
