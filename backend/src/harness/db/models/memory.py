"""Mémoire d'agent et de groupe, historique des révisions, propositions de mémoire de groupe."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.base import Base, GroupScopedMixin


class MemoryScope(enum.StrEnum):
    AGENT = "agent"
    GROUP = "group"


class ChangeType(enum.StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    CONFLICT = "conflict"


class ProposalStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryEntry(GroupScopedMixin, Base):
    """Source de vérité de la mémoire ; exposée à l'agent comme des fichiers (`SqlBackend`).

    Une entrée supprimée est conservée (`deleted_at`) avec son historique ; la recréer au même chemin
    la ressuscite en poursuivant ses versions."""

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("uq_memory_entries_agent_path", "group_id", "agent_id", "path", unique=True, postgresql_where=text("scope = 'agent'")),
        Index("uq_memory_entries_group_path", "group_id", "path", unique=True, postgresql_where=text("scope = 'group'")),
    )

    scope: Mapped[str] = mapped_column(String(10))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    path: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))  # verrou optimiste
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    author_type: Mapped[str] = mapped_column(String(10))  # agent | person
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryRevision(GroupScopedMixin, Base):
    __tablename__ = "memory_revisions"

    memory_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memory_entries.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    previous_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Contenu écrit par cette révision ; pour un conflit : l'écriture refusée, conservée pour mémoire.
    new_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String)  # « person:<id> » ou « agent:<id> »
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    change_type: Mapped[str] = mapped_column(String(10))


class GroupMemoryProposal(GroupScopedMixin, Base):
    __tablename__ = "group_memory_proposals"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    proposed_path: Mapped[str] = mapped_column(String(512))
    proposed_content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default=ProposalStatus.PENDING.value, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("persons.id"), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
