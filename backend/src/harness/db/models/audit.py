"""Journal des actions d'administration (FR-024)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class AdminAuditEntry(GroupScopedMixin, Base):
    __tablename__ = "admin_audit_entries"

    # Nul pour une action de plateforme (création ou désactivation d'un groupe).
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)  # type: ignore[assignment]
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("persons.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String)
    target_type: Mapped[str] = mapped_column(String)
    target_id: Mapped[str] = mapped_column(String)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
