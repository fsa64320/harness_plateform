"""Mémoire : écriture avec verrou optimiste, historique de chaque changement, suppression conservée.

Une écriture dont la version attendue ne correspond plus n'est jamais appliquée en silence : elle est
refusée (conflit), et son contenu est gardé dans une révision `conflict` visible de tous (FR-016)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.db.models.memory import ChangeType, MemoryEntry, MemoryRevision, MemoryScope

MAX_PATH_LENGTH = 512
MAX_CONTENT_BYTES = 256 * 1024  # une entrée de mémoire est une note, pas un entrepôt de fichiers


class MemoryPathInvalid(ValueError):
    pass


class MemoryEntryNotFound(Exception):
    pass


class MemoryTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    type: str  # agent | person
    id: uuid.UUID | None

    @property
    def label(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class WriteOutcome:
    entry: MemoryEntry
    change_type: str
    conflict: bool = False
    expected_version: int | None = None
    actual_version: int | None = None


def normalize_path(path: str) -> str:
    """Chemin d'entrée sans « / » initial ; refuse les chemins vides, trop longs ou qui remontent."""
    clean = path[1:] if path.startswith("/") else path
    if not clean or len(clean) > MAX_PATH_LENGTH or "\\" in clean:
        raise MemoryPathInvalid(f"Chemin de mémoire invalide : {path[:80]!r}")
    if any(part in ("", ".", "..") for part in clean.split("/")):
        raise MemoryPathInvalid(f"Chemin de mémoire invalide : {path[:80]!r}")
    return clean


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def find_entry(
    session: AsyncSession, *, scope: str, agent_id: uuid.UUID | None, path: str, lock: bool = False
) -> MemoryEntry | None:
    """Entrée à ce chemin, supprimée ou non (la RLS borne la recherche au groupe)."""
    query = select(MemoryEntry).where(MemoryEntry.scope == scope, MemoryEntry.path == path)
    query = query.where(MemoryEntry.agent_id == agent_id) if agent_id else query.where(MemoryEntry.agent_id.is_(None))
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


def _revision(
    entry: MemoryEntry,
    change_type: str,
    actor: Actor,
    *,
    previous: str | None,
    new: str | None,
    version: int | None = None,
) -> MemoryRevision:
    return MemoryRevision(
        group_id=entry.group_id,
        memory_entry_id=entry.id,
        version=version if version is not None else entry.version,
        previous_content=previous,
        new_content=new,
        changed_by=actor.label,
        changed_at=_now(),
        change_type=change_type,
    )


async def write_entry(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    scope: str,
    agent_id: uuid.UUID | None,
    path: str,
    content: str,
    expected_version: int | None,
    actor: Actor,
    source_run_id: uuid.UUID | None,
) -> WriteOutcome:
    """Crée ou met à jour une entrée. `expected_version` (si fourni) est la version sur laquelle
    l'auteur a basé son changement ; un décalage produit un conflit au lieu d'écraser."""
    path = normalize_path(path)
    if len(content.encode()) > MAX_CONTENT_BYTES:
        raise MemoryTooLarge(f"Contenu trop volumineux (maximum {MAX_CONTENT_BYTES // 1024} Ko par entrée)")
    entry = await find_entry(session, scope=scope, agent_id=agent_id, path=path, lock=True)
    if entry is None:
        entry = MemoryEntry(
            group_id=group_id, scope=scope, agent_id=agent_id, path=path, content=content, version=1,
            source_run_id=source_run_id, author_type=actor.type, author_id=actor.id,
        )
        session.add(entry)
        await session.flush()
        session.add(_revision(entry, ChangeType.CREATE, actor, previous=None, new=content))
        await session.flush()
        return WriteOutcome(entry, ChangeType.CREATE)

    live = entry.deleted_at is None
    if live and expected_version is not None and entry.version != expected_version:
        session.add(_revision(entry, ChangeType.CONFLICT, actor, previous=entry.content, new=content))
        await session.flush()
        return WriteOutcome(entry, ChangeType.CONFLICT, True, expected_version, entry.version)

    previous = entry.content if live else None
    entry.content = content
    entry.version += 1
    entry.deleted_at = None
    entry.source_run_id = source_run_id
    entry.author_type, entry.author_id = actor.type, actor.id
    change = ChangeType.UPDATE if live else ChangeType.CREATE
    session.add(_revision(entry, change, actor, previous=previous, new=content))
    await session.flush()
    return WriteOutcome(entry, change)


async def record_conflict(
    session: AsyncSession, entry: MemoryEntry, actor: Actor, attempted: str, expected_version: int
) -> WriteOutcome:
    """Consigne un changement refusé avant d'avoir pu être calculé (édition basée sur une version
    périmée) : son intention est conservée dans l'historique."""
    session.add(_revision(entry, ChangeType.CONFLICT, actor, previous=entry.content, new=attempted))
    await session.flush()
    return WriteOutcome(entry, ChangeType.CONFLICT, True, expected_version, entry.version)


async def delete_entry(session: AsyncSession, entry: MemoryEntry, actor: Actor) -> MemoryEntry:
    """Supprime l'entrée (conservée avec son historique) : plus lue, plus réutilisée."""
    locked = (
        await session.execute(select(MemoryEntry).where(MemoryEntry.id == entry.id).with_for_update())
    ).scalar_one()
    if locked.deleted_at is not None:
        return locked
    previous = locked.content
    locked.version += 1
    locked.deleted_at = _now()
    session.add(_revision(locked, ChangeType.DELETE, actor, previous=previous, new=None))
    await session.flush()
    return locked


async def list_entries(
    session: AsyncSession, *, scope: str, agent_id: uuid.UUID | None = None
) -> list[MemoryEntry]:
    query = select(MemoryEntry).where(MemoryEntry.scope == scope, MemoryEntry.deleted_at.is_(None))
    if scope == MemoryScope.AGENT:
        query = query.where(MemoryEntry.agent_id == agent_id)
    return list((await session.execute(query.order_by(MemoryEntry.path))).scalars())


async def get_entry(session: AsyncSession, entry_id: uuid.UUID, *, include_deleted: bool = False) -> MemoryEntry:
    query = select(MemoryEntry).where(MemoryEntry.id == entry_id)
    if not include_deleted:
        query = query.where(MemoryEntry.deleted_at.is_(None))
    entry = (await session.execute(query)).scalar_one_or_none()
    if entry is None:
        raise MemoryEntryNotFound(str(entry_id))
    return entry


async def list_revisions(session: AsyncSession, entry_id: uuid.UUID) -> list[MemoryRevision]:
    return list(
        (
            await session.execute(
                select(MemoryRevision)
                .where(MemoryRevision.memory_entry_id == entry_id)
                .order_by(MemoryRevision.changed_at, MemoryRevision.created_at)
            )
        ).scalars()
    )
