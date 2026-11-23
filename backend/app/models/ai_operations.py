"""AI operations models (Mock Interview Phase 7): usage ledger + institute controls.

Both tables are tenant-scoped and protected by Postgres RLS
(``tenant_isolation``, migrations c5e9a3b7d2f4 / d6f0b4c8e3a5) like every other
tenant table; the app layer still filters every query by ``tenant_id``.

``ai_usage_events`` - append-only ledger, one row per AI invocation (Phase 7.3).
  * ``event_id`` is the invocation id the backend generates BEFORE calling the
    worker (Phase 7.1). It is UNIQUE, so a retried write can never create a
    second row for the same call (insert-or-ignore).
  * Content-free by design: ids, counts, classes, timings and statuses only -
    never prompts, answers, resumes, job descriptions, questions or provider
    error messages.
  * Token counts are NULL when the provider reported none (unknown, not 0).
  * ``cost_micros`` is NULL unless an approved price table priced the call
    (decision P7-D9); ``price_version`` / ``cost_currency`` record which one.

``ai_feature_controls`` - one row per institute and feature (Phase 7.6). A
missing row means "platform defaults". Rows can only NARROW what the
environment flags allow (pause, disable voice/video, lower limits).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# BIGINT in Postgres; plain INTEGER on SQLite so the primary key autoincrements.
_BigId = BigInteger().with_variant(Integer(), "sqlite")

USAGE_OUTCOMES = (
    "ok",
    "fallback",
    "provider_error",
    "worker_error",
    "timeout",
    "unreachable",
    "circuit_open",
    "quota_blocked",
)


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_ai_usage_events_event_id"),
        Index("ix_ai_usage_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_ai_usage_events_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_ai_usage_events_tenant_subject", "tenant_id", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(_BigId, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    llm_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_unreported: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIFeatureControl(Base):
    __tablename__ = "ai_feature_controls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "feature", name="uq_ai_feature_controls_tenant_feature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    voice_allowed: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    video_allowed: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    # {student_sessions_per_day, tenant_sessions_per_month, tenant_tokens_per_month}
    limits_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
