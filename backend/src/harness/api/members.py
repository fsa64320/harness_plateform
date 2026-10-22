"""Membres du groupe : liste, invitation, rôles et quotas, retrait (FR-020, FR-023, FR-024)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from harness.auth.permissions import GroupAccess, GroupSessionDep, HumanMemberDep, require_roles
from harness.db.models.identity import Group, Membership, Role
from harness.groups import service

Email = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]

router = APIRouter(prefix="/groups/{group_id}/members", tags=["members"])

AdminDep = Annotated[GroupAccess, Depends(require_roles("admin"))]


class Invitation(BaseModel):
    email: Email
    roles: list[Role]


class MembershipUpdate(BaseModel):
    roles: list[Role] | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1)
    max_tokens_per_day: int | None = Field(default=None, ge=1)


def membership_out(m: Membership, email: str, group_name: str) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "group_id": str(m.group_id),
        "group_name": group_name,
        "person_email": email,
        "roles": sorted(m.roles),
        "max_concurrent_runs": m.max_concurrent_runs,
        "max_tokens_per_day": m.max_tokens_per_day,
        "status": m.status,
    }


async def _group_name(session: GroupSessionDep, group_id: uuid.UUID) -> str:
    return (await session.execute(select(Group.name).where(Group.id == group_id))).scalar_one()


def _error(exc: service.GroupError) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(409, {"code": "conflict", "message": str(exc)})
    return HTTPException(422, {"code": "invalid", "message": str(exc)})


@router.get("")
async def list_members(access: HumanMemberDep, session: GroupSessionDep) -> list[dict[str, Any]]:
    name = await _group_name(session, access.group_id)
    return [membership_out(m, email, name) for m, email in await service.list_members(session)]


@router.post("", status_code=201)
async def invite(body: Invitation, access: AdminDep, session: GroupSessionDep) -> dict[str, Any]:
    try:
        membership, email = await service.invite_member(session, access, email=body.email, roles=body.roles)
    except service.GroupError as exc:
        raise _error(exc) from exc
    return membership_out(membership, email, await _group_name(session, access.group_id))


@router.patch("/{membership_id}")
async def update(membership_id: uuid.UUID, body: MembershipUpdate, access: AdminDep, session: GroupSessionDep) -> dict[str, Any]:
    try:
        membership, email = await service.update_member(
            session, access, membership_id, body.model_dump(exclude_unset=True)
        )
    except service.GroupError as exc:
        raise _error(exc) from exc
    return membership_out(membership, email, await _group_name(session, access.group_id))


@router.delete("/{membership_id}", status_code=204)
async def remove(
    membership_id: uuid.UUID, request: Request, background: BackgroundTasks, access: AdminDep, session: GroupSessionDep
) -> Response:
    try:
        membership = await service.remove_member(session, access, membership_id)
    except service.GroupError as exc:
        raise _error(exc) from exc
    # ses exécutions en cours s'arrêtent une fois le retrait validé
    background.add_task(request.app.state.runner.stop_runs_where, access.group_id, triggered_by=membership.person_id)
    return Response(status_code=204)
