"""Catalogue d'outils du groupe : serveurs MCP (puis skills et hooks) et leurs opérations."""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class ToolKind(enum.StrEnum):
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    HOOK = "hook"


class ToolStatus(enum.StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Classification(enum.StrEnum):
    READ_ONLY = "read_only"
    EXTERNAL_EFFECT = "external_effect"
    UNCLASSIFIED = "unclassified"  # traité comme `external_effect`


class Tool(GroupScopedMixin, Base):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("group_id", "kind", "name"),)

    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(String(20), server_default=ToolStatus.ACTIVE.value)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("persons.id"), nullable=True)

    # Serveur MCP : URL HTTPS, transport distant uniquement.
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class ToolOperation(GroupScopedMixin, Base):
    """Outil élémentaire exposé par un serveur MCP, avec sa classification (FR-005a)."""

    __tablename__ = "tool_operations"
    __table_args__ = (UniqueConstraint("tool_id", "operation_name"),)

    tool_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    operation_name: Mapped[str] = mapped_column(String)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    classification: Mapped[str] = mapped_column(String(20), server_default=Classification.UNCLASSIFIED.value)
