"""Administration de la plateforme : créer, lister et désactiver des groupes (FR-022a).

Ces routes ne donnent accès à aucun contenu de groupe : le contexte « plateforme » n'ouvre que la table des
groupes (et l'écriture du journal)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from harness.auth.permissions import Principal, require_platform_admin
from harness.db.models.identity import Group
from harness.db.session import SessionDep
from harness.groups import service

Email = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]

router = APIRouter(prefix="/admin", tags=["admin"])

PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]


class GroupCreate(BaseModel):
    name: str = Field(min_length=1)
    admin_emails: list[Email] = Field(min_length=1)


def group_out(group: Group) -> dict[str, Any]:
    return {"id": str(group.id), "name": group.name, "status": group.status}


@router.get("/groups")
async def list_groups(principal: PlatformAdminDep, session: SessionDep) -> list[dict[str, Any]]:
    return [group_out(g) for g in await service.list_groups(session)]


@router.post("/groups", status_code=201)
async def create_group(body: GroupCreate, principal: PlatformAdminDep, session: SessionDep) -> dict[str, Any]:
    try:
        group = await service.create_group(
            session, name=body.name, admin_emails=body.admin_emails, actor_id=principal.id
        )
    except service.Conflict as exc:
        raise HTTPException(409, {"code": "conflict", "message": str(exc)}) from exc
    return group_out(group)


@router.post("/groups/{group_id}/disable", status_code=204)
async def disable_group(
    group_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    principal: PlatformAdminDep,
    session: SessionDep,
) -> Response:
    try:
        await service.disable_group(session, group_id=group_id, actor_id=principal.id)
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    # les exécutions du groupe s'arrêtent une fois la désactivation validée (tâche d'arrière-plan : la
    # transaction est déjà commitée quand elle démarre)
    background.add_task(request.app.state.runner.stop_group_runs, group_id)
    return Response(status_code=204)
