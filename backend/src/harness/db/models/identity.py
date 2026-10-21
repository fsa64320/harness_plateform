"""Personnes, groupes, appartenances (et plus tard comptes de service)."""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class Role(enum.StrEnum):
    ADMIN = "admin"
    TOOL_MANAGER = "tool_manager"
    GROUP_MEMORY_MANAGER = "group_memory_manager"
    APPROVER = "approver"
    MEMBER = "member"


class GroupStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class Person(Base):
    """Personne authentifiée par le SSO. `is_platform_admin` n'ouvre aucun contenu de groupe
    (FR-022a). `oidc_subject` reste vide tant que la personne invitée ne s'est pas connectée."""

    __tablename__ = "persons"

    oidc_subject: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String, default="")
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Group(Base):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String(20), server_default=GroupStatus.ACTIVE.value)
    trace_retention_days: Mapped[int] = mapped_column(Integer, server_default=text("90"))
    approval_timeout_hours: Mapped[int] = mapped_column(Integer, server_default=text("24"))
    proposal_expiry_days: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    cost_per_1k_tokens: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))


class Membership(GroupScopedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("group_id", "person_id"),)

    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("persons.id"), index=True)
    roles: Mapped[list[str]] = mapped_column(JSONB, server_default=text("""'["member"]'::jsonb"""))
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    max_tokens_per_day: Mapped[int] = mapped_column(Integer, server_default=text("1000000"))
    status: Mapped[str] = mapped_column(String(20), server_default=MembershipStatus.ACTIVE.value)
