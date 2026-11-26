"""AI Career Mentor - LangGraph agent with vector RAG + token streaming.

Two sources of truth, kept strictly separate:
  1. The student's VERIFIED PROFILE (passed in by the backend) - the ONLY source
     for personal facts (their skills, projects, attendance, CGPA). The mentor
     must never claim the student has something not listed here.
  2. A GENERAL CAREER KNOWLEDGE BASE (vector-retrieved via rag.py) - used for
     advice, role expectations, interview/resume tips. Cited separately.

This keeps the 'no hallucination about the student' promise while still giving
rich, knowledgeable career guidance.

Architecture (Batch 10a)
------------------------
The mentor is now a small LangGraph StateGraph instead of one linear call:

    START -> retrieve (vector RAG) -> generate (LLM) -> END

Why LangGraph: it turns the mentor into a real, inspectable agent graph we can
grow (tools, memory/checkpointer, routing) without rewrites, and it gives us
first-class TOKEN STREAMING via `graph.stream(..., stream_mode="messages")`.

Two endpoints:
  * POST /mentor/chat          -> full JSON answer (back-compat, unchanged shape)
  * POST /mentor/chat/stream   -> Server-Sent Events, one token at a time

Conversation MEMORY is intentionally NOT persisted here yet (that is the next
batch); the backend still passes recent `history` on every turn.

Mounted under the `/mentor` prefix by `main.py`.

Location:
    E:\\campus-ai\\ai-worker\\mentor.py
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from config import settings
from llm import get_chat_model
from provider_errors import is_quota_error, quota_http_error
from rag import retrieve
from tracing import trace_config

router = APIRouter(prefix="/mentor", tags=["mentor"])


# --- Schemas (local to this feature) ---------------------------------------
class MentorProject(BaseModel):
    title: str
    contribution: str | None = None


class MentorProfile(BaseModel):
    """The student's VERIFIED data - the only ground truth for personal facts."""

    verified_skills: list[str] = Field(default_factory=list)
    projects: list[MentorProject] = Field(default_factory=list)
    attendance_percentage: float | None = None
    cgpa: float | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class MentorChatRequest(BaseModel):
    profile: MentorProfile
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)


class MentorChatResponse(BaseModel):
    answer: str
    provider: str


_SYSTEM_PROMPT = (
    "You are an AI Career Mentor for a college student on a placement platform. "
    "You have two sources:\n"
    "1) The student's VERIFIED PROFILE - the ONLY source for personal facts "
    "(their skills, projects, attendance, CGPA). NEVER claim the student has "
    "anything not listed there. If the profile lacks the needed info, say so "
    "honestly and tell them what to add or get verified.\n"
    "2) GENERAL CAREER KNOWLEDGE - use it for advice, role expectations, and "
    "interview/resume tips. Cite it as [source: career knowledge].\n"
    "When you refer to the student's own data, cite it as [source: verified "
    "profile]. Be concise, practical, and encouraging."
)


def _format_profile(p: MentorProfile) -> str:
    """Render the verified profile as compact text for the system prompt."""
    skills = ", ".join(p.verified_skills) if p.verified_skills else "(none verified yet)"
    if p.projects:
        projects = "; ".join(
            f"{pr.title} ({pr.contribution or 'contributor'})" for pr in p.projects
        )
    else:
        projects = "(none verified yet)"
    attendance = (
        f"{p.attendance_percentage}%"
        if p.attendance_percentage is not None
        else "(no data)"
    )
    cgpa = str(p.cgpa) if p.cgpa is not None else "(no data)"
    return (
        f"- Verified skills: {skills}\n"
        f"- Verified projects: {projects}\n"
        f"- Attendance: {attendance}\n"
        f"- CGPA: {cgpa}"
    )


def _compose_messages(
    profile: MentorProfile,
    question: str,
    history: list[ChatTurn],
    knowledge: str,
) -> list[BaseMessage]:
    """Build the LLM message list: system context + recent turns + question."""
    system = (
        f"{_SYSTEM_PROMPT}\n\nSTUDENT VERIFIED PROFILE:\n"
        f"{_format_profile(profile)}\n\nGENERAL CAREER KNOWLEDGE:\n"
        f"{knowledge}"
    )
    messages: list[BaseMessage] = [SystemMessage(content=system)]
    # Keep the last few turns for context (bounded to limit tokens).
    for turn in history[-10:]:
        if turn.role == "user":
            messages.append(HumanMessage(content=turn.content))
        else:
            messages.append(AIMessage(content=turn.content))
    messages.append(HumanMessage(content=question))
    return messages


# --- LangGraph agent -------------------------------------------------------
class MentorState(TypedDict, total=False):
    """State flowing through the graph (pydantic models carried as values)."""

    profile: MentorProfile
    question: str
    history: list[ChatTurn]
    knowledge: str
    answer: str


def _retrieve_node(state: MentorState) -> dict:
    """Vector-retrieve the most relevant career-knowledge chunks for the question.

    Retrieval failures are non-fatal (rag.retrieve swallows them): the mentor can
    still answer from the verified profile, just without extra general guidance.
    """
    chunks = retrieve(state["question"], k=3)
    knowledge = "\n---\n".join(chunks) if chunks else "(no relevant knowledge found)"
    return {"knowledge": knowledge}


def _generate_node(state: MentorState) -> dict:
    """Call the LLM with the verified profile + retrieved knowledge as context.

    When the graph runs with `stream_mode="messages"`, LangGraph streams the
    tokens produced by this call to the caller in real time.
    """
    messages = _compose_messages(
        state["profile"],
        state["question"],
        state.get("history", []),
        state.get("knowledge", ""),
    )
    result = get_chat_model().invoke(
        messages,
        config=trace_config("mentor_generate", provider=settings.LLM_PROVIDER),
    )
    return {"answer": str(result.content)}


@lru_cache(maxsize=1)
def _get_graph():
    """Build (once) and compile the mentor agent graph."""
    builder = StateGraph(MentorState)
    builder.add_node("retrieve", _retrieve_node)
    builder.add_node("generate", _generate_node)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    return builder.compile()


def _initial_state(payload: MentorChatRequest) -> MentorState:
    return {
        "profile": payload.profile,
        "question": payload.question,
        "history": payload.history,
    }


def _sse(event: dict) -> str:
    """Format one Server-Sent Event frame."""
    return f"data: {json.dumps(event)}\n\n"


# --- HTTP endpoints --------------------------------------------------------
@router.post("/chat", response_model=MentorChatResponse)
def mentor_chat(payload: MentorChatRequest) -> MentorChatResponse:
    """Answer a career question using the verified profile + retrieved knowledge."""
    try:
        final = _get_graph().invoke(_initial_state(payload))
    except Exception as exc:  # noqa: BLE001 - surface any provider/SDK error
        if is_quota_error(exc):  # Phase 10.3: 429 so the backend stops new calls
            raise quota_http_error() from None
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM call failed: {exc}",
        )
    return MentorChatResponse(
        answer=str(final.get("answer", "")), provider=settings.LLM_PROVIDER
    )


def _stream_tokens(payload: MentorChatRequest) -> Iterator[str]:
    """Run the graph and yield SSE events: many {type:token} then one {type:done}.

    Any error becomes a single {type:error} event so the client shows it instead
    of hanging on a half-open stream.
    """
    try:
        graph = _get_graph()
        streamed = False
        for chunk, _meta in graph.stream(
            _initial_state(payload),
            config=trace_config("mentor_stream", provider=settings.LLM_PROVIDER),
            stream_mode="messages",
        ):
            text = getattr(chunk, "content", "")
            if text:
                streamed = True
                yield _sse({"type": "token", "content": text})
        if not streamed:
            # Defensive: if a provider didn't stream, fall back to a full answer.
            final = graph.invoke(_initial_state(payload))
            yield _sse({"type": "token", "content": str(final.get("answer", ""))})
        yield _sse({"type": "done", "provider": settings.LLM_PROVIDER})
    except Exception as exc:  # noqa: BLE001 - never leave the stream hanging
        if is_quota_error(exc):  # Phase 10.3: additive machine-readable code
            yield _sse({"type": "error", "detail": "LLM call failed: provider quota exhausted",
                        "code": "quota_exhausted"})
            return
        yield _sse({"type": "error", "detail": f"LLM call failed: {exc}"})


@router.post("/chat/stream")
def mentor_chat_stream(payload: MentorChatRequest) -> StreamingResponse:
    """Stream the mentor's answer token-by-token as Server-Sent Events."""
    return StreamingResponse(
        _stream_tokens(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx/Render) so tokens flush immediately.
            "X-Accel-Buffering": "no",
        },
    )
