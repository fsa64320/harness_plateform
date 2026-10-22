"""GET /me : identité courante et groupes accessibles (FR-022b)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from harness.auth.permissions import PrincipalDep
from harness.db.models.identity import Group, GroupStatus, Membership, MembershipStatus, Person
from harness.db.session import SessionDep, person_context

router = APIRouter()


class MembershipOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    group_name: str
    person_email: str
    roles: list[str]
    max_concurrent_runs: int
    max_tokens_per_day: int
    status: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str | None
    is_platform_admin: bool
    memberships: list[MembershipOut]


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, session: SessionDep) -> MeOut:
    memberships: list[MembershipOut] = []
    if principal.kind == "person":
        await person_context(session, principal.id)
        rows = (
            await session.execute(
                select(Membership, Group.name, Person.email)
                .join(Group, Group.id == Membership.group_id)
                .join(Person, Person.id == Membership.person_id)
                .where(
                    Membership.person_id == principal.id,
                    Membership.status == MembershipStatus.ACTIVE,
                    Group.status == GroupStatus.ACTIVE,
                )
                .order_by(Group.name)
            )
        ).all()
        memberships = [
            MembershipOut(
                id=m.id,
                group_id=m.group_id,
                group_name=name,
                person_email=email,
                roles=sorted(m.roles),
                max_concurrent_runs=m.max_concurrent_runs,
                max_tokens_per_day=m.max_tokens_per_day,
                status=m.status,
            )
            for m, name, email in rows
        ]
    return MeOut(
        id=principal.id,
        email=principal.email,
        is_platform_admin=principal.is_platform_admin,
        memberships=memberships,
    )
