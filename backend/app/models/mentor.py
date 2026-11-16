"""AI mentor conversations ORM models."""

from datetime import datetime
from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class MentorConversation(Base):
    """A saved AI-mentor chat thread for one student (durable memory).

    Batch 10b gives the mentor persistent memory. Each conversation belongs to a
    single student inside an institute; its transcript lives in
    ``mentor_messages``. Tenant-scoped and protected by Postgres RLS
    (``tenant_isolation``), so one institute can never read another's mentor
    chats - matching every other tenant-scoped table.
    """

    __tablename__ = "mentor_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Short human label (derived from the first question); shown in the sidebar.
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages: Mapped[list["MentorMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # id is monotonic, so ordering by it is deterministic chronological order
        # (created_at has only second precision on SQLite and can tie).
        order_by="MentorMessage.id",
    )

    def __repr__(self) -> str:
        return (
            f"<MentorConversation id={self.id} student={self.student_id} "
            f"title={self.title!r}>"
        )


class MentorMessage(Base):
    """One turn (user or assistant) inside a mentor conversation.

    The backend owns memory: prior messages are replayed to the (stateless) AI
    worker as history on each new turn, so the worker needs no database of its
    own. Tenant-scoped + RLS-protected, cascade-deleted with the conversation.
    """

    __tablename__ = "mentor_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("mentor_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "user" | "assistant".
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Which LLM produced an assistant turn (null for user turns).
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped["MentorConversation"] = relationship(
        back_populates="messages"
    )

    def __repr__(self) -> str:
        return (
            f"<MentorMessage id={self.id} convo={self.conversation_id} "
            f"role={self.role}>"
        )
