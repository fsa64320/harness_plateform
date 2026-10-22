"""Définition réutilisable d'un agent (FR-001)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class Agent(GroupScopedMixin, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String)
    instructions: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("persons.id"), nullable=True)
    # Ids de `Tool` (un serveur MCP entier) ou de `ToolOperation` (une seule opération).
    allowed_tool_ids: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    agent_memory_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    group_memory_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    subagents_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    max_iterations: Mapped[int] = mapped_column(Integer)
    max_duration_seconds: Mapped[int] = mapped_column(Integer)
    max_tokens: Mapped[int] = mapped_column(Integer)
    config_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def agent_limits(agent: Agent) -> dict[str, Any]:
    return {
        "max_iterations": agent.max_iterations,
        "max_duration_seconds": agent.max_duration_seconds,
        "max_tokens": agent.max_tokens,
    }
