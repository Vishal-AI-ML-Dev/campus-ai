"""AI Career Mentor endpoints (student-facing).

Gathers the student's VERIFIED profile from the database and asks the AI worker
for grounded career guidance. Verified-only by design - this is the moat:
unverified claims never reach the mentor's context, so advice can't be built on
unproven data.

Batch 10a: answers stream token-by-token (Server-Sent Events).
Batch 10b: conversations + messages are PERSISTED (durable memory). Prior turns
are loaded from the DB and replayed to the worker as history, so the mentor
remembers the thread across reloads and devices. The AI worker stays stateless
- the backend owns memory (the worker runs as a separate service and cannot
reach this database).

Mounted under the `/mentor` prefix by `main.py`.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_client import ask_mentor, stream_mentor
from app.core.db import SessionLocal, get_db, set_current_tenant
from app.models import (
    AttendanceRecord,
    AttendanceStatus,
    MentorConversation,
    MentorMessage,
    Project,
    ProjectMember,
    Result,
    Skill,
    SkillStatus,
    Subject,
    User,
)
from app.schemas import (
    MentorConversationDetailOut,
    MentorConversationOut,
)
from app.core.security import apply_tenant_guc, get_current_user

router = APIRouter(
    prefix="/mentor",
    tags=["mentor"],
    # Stamp the caller's tenant onto the DB session so the Postgres RLS policies
    # on mentor_conversations / mentor_messages are enforced (Batch 10b).
    # No-op on SQLite; harmless everywhere.
    dependencies=[Depends(apply_tenant_guc)],
)

# Longest history (turns) we replay to the worker. Keeps the prompt bounded on
# long threads; the full transcript still lives in the DB.
_MAX_HISTORY_TURNS = 20


class ChatTurn(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class MentorChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
        examples=["Backend role ke liye mera skill gap kya hai?"],
    )
    # Batch 10b server-side memory: omit to START a new conversation, or pass an
    # existing id to CONTINUE one. History is loaded from the DB; the optional
    # `history` field is kept only for backwards compatibility and is ignored.
    conversation_id: int | None = None
    history: list[ChatTurn] = Field(default_factory=list)


class MentorChatResponse(BaseModel):
    answer: str
    provider: str
    conversation_id: int


def _build_verified_profile(db: Session, student_id: int) -> dict:
    """Collect ONLY the student's verified data for the mentor's context."""
    # Verified skills (names only).
    skills = list(
        db.scalars(
            select(Skill.name).where(
                Skill.student_id == student_id, Skill.status == SkillStatus.verified
            )
        )
    )

    # Verified project contributions (title + what they did).
    proj_rows = db.execute(
        select(Project.title, ProjectMember.contribution)
        .join(Project, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.student_id == student_id,
            ProjectMember.status == SkillStatus.verified,
        )
    ).all()
    projects = [{"title": title, "contribution": contribution} for title, contribution in proj_rows]

    # Overall attendance % ((present + late) / total).
    statuses = list(
        db.scalars(
            select(AttendanceRecord.status).where(
                AttendanceRecord.student_id == student_id
            )
        )
    )
    total = len(statuses)
    attended = sum(
        1 for s in statuses if s in (AttendanceStatus.present, AttendanceStatus.late)
    )
    attendance_percentage = round(attended / total * 100, 1) if total else None

    # Credit-weighted CGPA across all results.
    gp_rows = db.execute(
        select(Result.grade_point, Subject.credits).join(
            Subject, Result.subject_id == Subject.id
        ).where(Result.student_id == student_id)
    ).all()
    total_credits = sum(credits for _, credits in gp_rows)
    cgpa = (
        round(sum(gp * credits for gp, credits in gp_rows) / total_credits, 2)
        if total_credits
        else None
    )

    return {
        "verified_skills": skills,
        "projects": projects,
        "attendance_percentage": attendance_percentage,
        "cgpa": cgpa,
    }


# ---------------------------------------------------------------------------
# Conversation memory helpers (Batch 10b)
# ---------------------------------------------------------------------------


def _title_from_question(question: str) -> str:
    """A short, human title for a new conversation (first question, trimmed)."""
    clean = " ".join(question.strip().split())
    return f"{clean[:117]}..." if len(clean) > 120 else clean


def _get_owned_conversation(
    db: Session, conversation_id: int, user: User
) -> MentorConversation:
    """Fetch a conversation, enforcing tenant + ownership (404 otherwise)."""
    convo = db.get(MentorConversation, conversation_id)
    if (
        convo is None
        or convo.tenant_id != user.tenant_id
        or convo.student_id != user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return convo


def _resolve_conversation(
    db: Session, payload: MentorChatRequest, user: User
) -> MentorConversation:
    """Continue the given conversation, or start a fresh one titled from the Q."""
    if payload.conversation_id is not None:
        return _get_owned_conversation(db, payload.conversation_id, user)
    convo = MentorConversation(
        tenant_id=user.tenant_id,
        student_id=user.id,
        title=_title_from_question(payload.question),
    )
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def _load_history(db: Session, conversation_id: int) -> list[dict]:
    """Prior turns (oldest first, capped) to replay to the worker as context."""
    rows = list(
        db.scalars(
            select(MentorMessage)
            .where(MentorMessage.conversation_id == conversation_id)
            .order_by(MentorMessage.id.asc())
        )
    )
    turns = [{"role": m.role, "content": m.content} for m in rows]
    return turns[-_MAX_HISTORY_TURNS:]


def _add_message(
    db: Session,
    convo: MentorConversation,
    role: str,
    content: str,
    provider: str | None = None,
) -> None:
    """Persist one turn and bump the conversation's recency for the sidebar."""
    db.add(
        MentorMessage(
            tenant_id=convo.tenant_id,
            conversation_id=convo.id,
            role=role,
            content=content,
            provider=provider,
        )
    )
    convo.updated_at = datetime.now(timezone.utc)
    db.add(convo)
    db.commit()


# ---------------------------------------------------------------------------
# Streaming (SSE) helpers
# ---------------------------------------------------------------------------


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def _parse_sse_frame(frame: str) -> dict | None:
    """Extract the JSON payload from a single SSE frame ('data: {...}')."""
    for line in frame.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            data = stripped[len("data:"):].strip()
            if not data:
                return None
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
    return None


def _persist_assistant(
    conversation_id: int, tenant_id: int, answer: str, provider: str | None
) -> None:
    """Save the streamed assistant turn using a FRESH, tenant-scoped session.

    A brand-new session (not the request's) is used because the request-scoped
    session may already be torn down by the time the last token flushes.
    """
    db = SessionLocal()
    try:
        set_current_tenant(db, tenant_id)
        convo = db.get(MentorConversation, conversation_id)
        if convo is None:
            return
        db.add(
            MentorMessage(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                provider=provider,
            )
        )
        convo.updated_at = datetime.now(timezone.utc)
        db.add(convo)
        db.commit()
    finally:
        db.close()


def _stream_with_persistence(
    profile: dict,
    question: str,
    history: list[dict],
    conversation_id: int,
    tenant_id: int,
) -> Iterator[bytes]:
    """Proxy the worker's SSE stream to the client while capturing the full
    answer, then persist the assistant turn once the stream ends."""
    # First frame tells the client which conversation this stream belongs to.
    yield _sse({"type": "meta", "conversation_id": conversation_id})

    parts: list[str] = []
    provider: str | None = None
    buffer = ""
    for chunk in stream_mentor(profile, question, history):
        # Pass the worker's bytes straight through - our bookkeeping never adds
        # latency to the tokens the user sees.
        yield chunk
        buffer += chunk.decode("utf-8", errors="ignore")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            event = _parse_sse_frame(frame)
            if event is None:
                continue
            if event.get("type") == "token":
                parts.append(str(event.get("content", "")))
            elif event.get("type") == "done":
                candidate = event.get("provider")
                if isinstance(candidate, str):
                    provider = candidate

    answer = "".join(parts).strip()
    if answer:
        _persist_assistant(conversation_id, tenant_id, answer, provider)


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=MentorChatResponse)
def mentor_chat(
    payload: MentorChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MentorChatResponse:
    """Ask the AI mentor a question, grounded on YOUR verified profile.

    Persists both turns into a conversation (a new one when no id is given) so
    the thread is remembered; prior turns are replayed to the worker.
    """
    profile = _build_verified_profile(db, current_user.id)
    convo = _resolve_conversation(db, payload, current_user)
    history = _load_history(db, convo.id)
    _add_message(db, convo, "user", payload.question)
    result = ask_mentor(profile, payload.question, history)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI mentor is unavailable right now (is the AI worker running?)",
        )
    provider = result.get("provider", "unknown")
    _add_message(db, convo, "assistant", result["answer"], provider)
    return MentorChatResponse(
        answer=result["answer"], provider=provider, conversation_id=convo.id
    )


@router.post("/chat/stream")
def mentor_chat_stream(
    payload: MentorChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the mentor's answer token-by-token (Server-Sent Events).

    The verified profile, conversation, history, and the user's turn are all
    resolved up-front (they need the DB session). The streaming generator
    afterwards proxies the AI worker and persists the assistant turn at the end
    via its own fresh session.
    """
    profile = _build_verified_profile(db, current_user.id)
    convo = _resolve_conversation(db, payload, current_user)
    history = _load_history(db, convo.id)
    _add_message(db, convo, "user", payload.question)
    return StreamingResponse(
        _stream_with_persistence(
            profile, payload.question, history, convo.id, current_user.tenant_id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable proxy buffering (nginx/Render) so tokens flush immediately.
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Conversation management endpoints (Batch 10b)
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=list[MentorConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MentorConversation]:
    """List the caller's saved mentor conversations, most-recent first."""
    return list(
        db.scalars(
            select(MentorConversation)
            .where(
                MentorConversation.tenant_id == current_user.tenant_id,
                MentorConversation.student_id == current_user.id,
            )
            .order_by(MentorConversation.updated_at.desc())
        )
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=MentorConversationDetailOut,
)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MentorConversation:
    """Full transcript of one conversation (tenant + owner scoped)."""
    return _get_owned_conversation(db, conversation_id, current_user)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a conversation and its messages (owner only)."""
    convo = _get_owned_conversation(db, conversation_id, current_user)
    db.delete(convo)
    db.commit()
