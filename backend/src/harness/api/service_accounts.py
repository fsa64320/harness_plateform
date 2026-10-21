"""Comptes de service du groupe : création (jeton montré une fois), liste, révocation (FR-002a)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from harness.auth.permissions import GroupAccess, require_roles
from harness.db.models.identity import ServiceAccount
from harness.db.session import SessionDep
from harness.groups import service

router = APIRouter(prefix="/groups/{group_id}/service-accounts", tags=["service-accounts"])

AdminDep = Annotated[GroupAccess, Depends(require_roles("admin"))]


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1)
    allowed_agent_ids: list[uuid.UUID]
    max_concurrent_runs: int | None = Field(default=None, ge=1)
    max_tokens_per_day: int | None = Field(default=None, ge=1)


def account_out(a: ServiceAccount) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "name": a.name,
        "token_prefix": a.token_prefix,
        "allowed_agent_ids": a.allowed_agent_ids,
        "max_concurrent_runs": a.max_concurrent_runs,
        "max_tokens_per_day": a.max_tokens_per_day,
        "status": a.status,
    }


@router.get("")
async def list_accounts(access: AdminDep, session: SessionDep) -> list[dict[str, Any]]:
    return [account_out(a) for a in await service.list_service_accounts(session)]


@router.post("", status_code=201)
async def create_account(body: ServiceAccountCreate, access: AdminDep, session: SessionDep) -> dict[str, Any]:
    try:
        account, token = await service.create_service_account(
            session, access, name=body.name, allowed_agent_ids=body.allowed_agent_ids,
            max_concurrent_runs=body.max_concurrent_runs, max_tokens_per_day=body.max_tokens_per_day,
        )
    except service.Invalid as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    return {**account_out(account), "token": token}  # la seule fois où le jeton est montré


@router.post("/{service_account_id}/revoke", status_code=204)
async def revoke(
    service_account_id: uuid.UUID, request: Request, background: BackgroundTasks, access: AdminDep, session: SessionDep
) -> Response:
    try:
        await service.revoke_service_account(session, access, service_account_id)
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    background.add_task(request.app.state.runner.stop_runs_where, access.group_id, triggered_by=service_account_id)
    return Response(status_code=204)
