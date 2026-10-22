"""Journal d'administration : chaque action est consignée avec son auteur et sa date (FR-024)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from harness.db.models.audit import AdminAuditEntry
from harness.traces.redaction import redact


async def record_admin_action(
    session: AsyncSession,
    *,
    group_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID,
    details: dict[str, Any] | None = None,
) -> AdminAuditEntry:
    """Ajoute l'entrée dans la transaction courante ; `details` est masqué (jamais de secret)."""
    entry = AdminAuditEntry(
        group_id=group_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=redact(details or {}, known_secrets=[]),
    )
    session.add(entry)
    await session.flush()
    return entry
