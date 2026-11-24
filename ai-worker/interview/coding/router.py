"""HTTP routes for the coding review steps (Phase 8.3, review-only).

Mounted by main.py only when BOTH ``MOCK_INTERVIEW_ENABLED`` and
``MOCK_INTERVIEW_CODING_ENABLED`` are true, behind the X-Worker-Token
middleware. Stateless; the backend sends public problem text, the code and
the typed explanation on every call.

  POST /interview/coding/hint      -> one AI-tailored hint (filtered)
  POST /interview/coding/followup  -> next explanation question, or "end"
  POST /interview/coding/review    -> AI estimates (code_quality, reasoning,
                                      testing) with verified evidence

Code is read as data and never executed. Every LLM call runs untraced (R1)
inside a usage scope (Phase 7.2 ``usage`` block: numbers only).
"""

from fastapi import APIRouter, HTTPException, status

from config import settings
from interview.coding import review as steps
from interview.coding.schemas import (
    FollowupRequest,
    FollowupResponse,
    HintRequest,
    HintResponse,
    ReviewRequest,
    ReviewResponse,
)
from interview.no_tracing import tracing_disabled
from interview.usage import usage_scope

router = APIRouter(prefix="/interview/coding", tags=["interview-coding"])


@router.post("/hint", response_model=HintResponse)
def coding_hint(payload: HintRequest) -> HintResponse:
    """502 when the model failed (the backend gives the hint back); 422 when the
    model only produced unsafe text, e.g. a solution (not an outage: the
    circuit breaker is not tripped, and the backend keeps the hint used)."""
    try:
        with tracing_disabled(), usage_scope() as usage:  # R1: inputs hold student code
            hint = steps.generate_hint(payload)
    except steps.HintUnavailable as exc:
        if str(exc) == "filtered":
            raise HTTPException(status_code=422, detail="No safe hint could be produced")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="A hint could not be generated"
        )
    return HintResponse(hint=hint, provider=settings.LLM_PROVIDER, usage=usage.summary())


@router.post("/followup", response_model=FollowupResponse)
def coding_followup(payload: FollowupRequest) -> FollowupResponse:
    """Never fails on LLM errors: a fixed explanation question is used instead."""
    with tracing_disabled(), usage_scope() as usage:
        action, question, source = steps.next_question(payload)
    return FollowupResponse(
        action=action,
        question=question,
        source=source,
        provider=settings.LLM_PROVIDER,
        usage=usage.summary(),
    )


@router.post("/review", response_model=ReviewResponse)
def coding_review(payload: ReviewRequest) -> ReviewResponse:
    """502 when the review could not be produced (nothing partial is returned)."""
    try:
        with tracing_disabled(), usage_scope() as usage:
            result = steps.review(payload)
    except steps.ReviewUnavailable:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="The code review could not be produced"
        )
    return ReviewResponse(**result, provider=settings.LLM_PROVIDER, usage=usage.summary())
