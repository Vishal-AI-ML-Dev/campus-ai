"""HTTP routes for the AI Mock Interview engine.

Mounted by main.py only when MOCK_INTERVIEW_ENABLED is true, and protected by
the worker's X-Worker-Token middleware like every other non-open route.

Stateless: the backend sends the config, plan and transcript on every call.
  POST /interview/plan  -> topic plan for a new session
  POST /interview/turn  -> opening question, or evaluation of the last answer
                           plus the next question / closing message
  POST /interview/report -> final evidence-backed report (Phase 4)
"""

from fastapi import APIRouter, HTTPException, status

from config import settings
from interview.graph import QuestionGenerationFailed, plan_graph, turn_graph
from interview.report import ReportGenerationFailed, ReportNotAssessable, report_graph
from interview.schemas import (
    PlanRequest,
    PlanResponse,
    ReportRequest,
    ReportResponse,
    TurnRequest,
    TurnResponse,
)

router = APIRouter(prefix="/interview", tags=["interview"])


@router.get("/ping")
def interview_ping() -> dict:
    """Confirms the interview engine is mounted (no LLM call)."""
    return {"engine": "mock-interview", "status": "ok"}


@router.post("/plan", response_model=PlanResponse)
def interview_plan(payload: PlanRequest) -> PlanResponse:
    """Plan topics for a new interview. Never fails on LLM errors: the planner
    falls back to the deterministic topic library."""
    final = plan_graph().invoke(
        {
            "config": payload.config,
            "profile": payload.profile,
            "raw_resume": payload.resume_text,
            "raw_jd": payload.job_description,
        }
    )
    return PlanResponse(plan=final["plan"], provider=settings.LLM_PROVIDER)


def _check_transcript(payload: TurnRequest) -> None:
    for expected, turn in enumerate(payload.transcript, start=1):
        if turn.seq != expected:
            raise HTTPException(
                status_code=422,
                detail="Transcript sequence numbers must be 1..n in order",
            )
    if payload.transcript and payload.transcript[-1].answer is None:
        raise HTTPException(
            status_code=422,
            detail="The last question has not been answered yet",
        )


@router.post("/turn", response_model=TurnResponse)
def interview_turn(payload: TurnRequest) -> TurnResponse:
    """Process one interview step (see module docstring)."""
    _check_transcript(payload)
    try:
        final = turn_graph().invoke(
            {
                "config": payload.config,
                "plan": payload.plan,
                "profile": payload.profile,
                "resume_text": payload.resume_text,
                "jd_text": payload.job_description,
                "transcript": payload.transcript,
                "remaining_seconds": payload.remaining_seconds,
            }
        )
    except QuestionGenerationFailed:
        # No details: provider errors can echo prompt fragments.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The interviewer could not generate the next question",
        )
    return TurnResponse(
        action=final["action"],
        evaluation=final.get("evaluation"),
        question=final.get("question"),
        closing_message=final.get("closing_message"),
        end_reason=final.get("end_reason"),
        level=final["level"],
        provider=settings.LLM_PROVIDER,
    )


@router.post("/report", response_model=ReportResponse)
def interview_report(payload: ReportRequest) -> ReportResponse:
    """Score a finished interview.

    502: a scoring call failed (retryable). 422: the model gave no
    evidence-backed score for any answer (the backend limits retries).
    """
    for expected, turn in enumerate(payload.transcript, start=1):
        if turn.seq != expected:
            raise HTTPException(
                status_code=422,
                detail="Transcript sequence numbers must be 1..n in order",
            )
    try:
        final = report_graph().invoke(
            {"config": payload.config, "plan": payload.plan, "transcript": payload.transcript}
        )
    except ReportGenerationFailed:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The report could not be generated",
        )
    except ReportNotAssessable:
        raise HTTPException(
            status_code=422,
            detail="No answer could be assessed with evidence",
        )
    return ReportResponse(
        rubric_version=final["rubric"].version,
        overall_score=final["overall_score"],
        categories=final["categories"],
        questions=final["questions"],
        strengths=final["strengths"],
        weaknesses=final["weaknesses"],
        recommendations=final["recommendations"],
        provider=settings.LLM_PROVIDER,
    )
