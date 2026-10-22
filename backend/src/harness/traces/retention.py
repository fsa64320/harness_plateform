"""Rétention : purge des événements de trace et des révisions de mémoire plus vieux que le réglage du
groupe (`Group.trace_retention_days`, 90 jours par défaut). Les exécutions, les entrées de mémoire et le
journal d'administration sont conservés."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from harness.db.models.identity import Group
from harness.db.models.memory import MemoryRevision
from harness.db.models.runs import TraceEvent


async def purge_group(session: AsyncSession, group: Group) -> tuple[int, int]:
    """Supprime les traces et révisions périmées du groupe ; renvoie `(événements, révisions)`."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=group.trace_retention_days)
    events = await session.execute(delete(TraceEvent).where(TraceEvent.occurred_at < cutoff))
    revisions = await session.execute(delete(MemoryRevision).where(MemoryRevision.changed_at < cutoff))
    return int(events.rowcount or 0), int(revisions.rowcount or 0)  # type: ignore[attr-defined]
