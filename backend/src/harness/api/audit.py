"""Journal d'administration du groupe (FR-024), réservé aux administrateurs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from harness.auth.permissions import GroupAccess, GroupSessionDep, require_roles
from harness.groups import service

router = APIRouter(prefix="/groups/{group_id}/audit", tags=["audit"])

AdminDep = Annotated[GroupAccess, Depends(require_roles("admin"))]


@router.get("")
async def audit(access: AdminDep, session: GroupSessionDep) -> list[dict[str, Any]]:
    return [
        {
            "actor_id": str(e.actor_id) if e.actor_id else None,
            "action": e.action,
            "target_type": e.target_type,
            "target_id": e.target_id,
            "details": e.details,
            "occurred_at": e.occurred_at.isoformat().replace("+00:00", "Z"),
        }
        for e in await service.list_audit(session)
    ]
