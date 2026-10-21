"""Exécutions, événements de trace et demandes d'approbation (FR-002 à FR-007)."""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    LIMIT_REACHED = "limit_reached"
    INTERRUPTED = "interrupted"
    EXPIRED = "expired"
    REJECTED_QUOTA = "rejected_quota"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.STOPPED,
        RunStatus.LIMIT_REACHED,
        RunStatus.INTERRUPTED,
        RunStatus.EXPIRED,
        RunStatus.REJECTED_QUOTA,
    }
)


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Run(GroupScopedMixin, Base):
    __tablename__ = "runs"

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), server_default=RunStatus.QUEUED.value, index=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    triggered_by_type: Mapped[str] = mapped_column(String(20))  # person | service_account
    triggered_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    launch_mode: Mapped[str] = mapped_column(String(20))  # manual | programmatic
    thread_id: Mapped[str] = mapped_column(String)
    relaunched_from: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    iterations: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost: Mapped[float] = mapped_column(Numeric(14, 6), server_default=text("0"))
    # Temps actif cumulé (hors attente d'approbation), base de la limite de durée.
    active_seconds: Mapped[float] = mapped_column(Float, server_default=text("0"))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


class TraceEvent(GroupScopedMixin, Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        Index("ix_trace_events_group_run_seq", "group_id", "run_id", "seq"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ApprovalRequest(GroupScopedMixin, Base):
    __tablename__ = "approval_requests"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Un même arrêt (interrupt) du graphe peut porter plusieurs demandes ; la reprise attend toutes
    # les décisions, dans l'ordre `position`.
    interrupt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tool_operation: Mapped[str] = mapped_column(String)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default=ApprovalStatus.PENDING.value, index=True)
    no_eligible_approver: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("persons.id"), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
