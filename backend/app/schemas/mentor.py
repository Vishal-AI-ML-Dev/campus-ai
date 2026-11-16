"""AI mentor schemas."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class MentorMessageOut(BaseModel):
    """One persisted turn in a mentor conversation."""

    id: int
    role: str
    content: str
    provider: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MentorConversationOut(BaseModel):
    """Sidebar summary of a saved mentor conversation."""

    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MentorConversationDetailOut(MentorConversationOut):
    """A conversation plus its full transcript (oldest turn first)."""

    messages: list[MentorMessageOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
