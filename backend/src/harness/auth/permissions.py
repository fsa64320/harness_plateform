"""Authentification de la requête, appartenance à un groupe et rôles (FR-019, FR-020, FR-022).

Règles : un non-membre reçoit 404 (jamais 403) pour ne pas révéler l'existence d'une ressource ;
un rôle insuffisant reçoit 403 ; un administrateur de plateforme sans appartenance reçoit 404 sur
le contenu d'un groupe (FR-022a).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from harness.config import get_settings
from harness.db.models.identity import Group, GroupStatus, Membership, MembershipStatus, Person
from harness.db.session import SessionDep, group_context, person_context

SESSION_COOKIE = "harness_session"
SESSION_MAX_AGE_SECONDS = 8 * 3600
_SALT = "harness-session"


@dataclass(frozen=True)
class Principal:
    """Appelant authentifié : une personne (SSO) ou un compte de service (jeton)."""

    kind: Literal["person", "service_account"]
    id: uuid.UUID
    email: str | None = None
    is_platform_admin: bool = False
    group_id: uuid.UUID | None = None  # compte de service : son unique groupe
    allowed_agent_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GroupAccess:
    """Résultat de `group_member` : l'appelant, le groupe et ses rôles dans ce groupe."""

    principal: Principal
    group_id: uuid.UUID
    roles: frozenset[str]
    membership_id: uuid.UUID | None = None

    @property
    def is_service_account(self) -> bool:
        return self.principal.kind == "service_account"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SALT)


def make_session_cookie(person_id: uuid.UUID) -> str:
    return _serializer().dumps(str(person_id))


def read_session_cookie(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(_serializer().loads(value, max_age=SESSION_MAX_AGE_SECONDS))
    except (BadSignature, SignatureExpired, ValueError):
        return None


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentification requise")


async def current_principal(request: Request, session: SessionDep) -> Principal:
    """Identité de l'appelant : jeton de compte de service (`Authorization: Bearer`) ou session SSO."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        from harness.auth.service_accounts import authenticate_token

        return await authenticate_token(session, authorization[7:].strip())

    cookie = request.cookies.get(SESSION_COOKIE)
    person_id = read_session_cookie(cookie) if cookie else None
    if person_id is None:
        raise _unauthenticated()
    person = await session.get(Person, person_id)
    if person is None:
        raise _unauthenticated()
    return Principal(
        kind="person", id=person.id, email=person.email, is_platform_admin=person.is_platform_admin
    )


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Ressource introuvable")


async def group_member(group_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> GroupAccess:
    """Vérifie que l'appelant appartient au groupe de l'URL et ouvre le contexte RLS de ce groupe.

    Non-membre, appartenance retirée, groupe désactivé ou inconnu : 404."""
    if principal.kind == "service_account":
        if principal.group_id != group_id:
            raise _not_found()
        await group_context(session, group_id)
        status = (await session.execute(select(Group.status).where(Group.id == group_id))).scalar()
        if status != GroupStatus.ACTIVE:
            raise _not_found()
        return GroupAccess(principal, group_id, frozenset({"member"}))

    await person_context(session, principal.id)
    row = (
        await session.execute(
            select(Membership.id, Membership.roles, Group.status)
            .join(Group, Group.id == Membership.group_id)
            .where(
                Membership.group_id == group_id,
                Membership.person_id == principal.id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).first()
    if row is None or row.status != GroupStatus.ACTIVE:
        raise _not_found()
    await group_context(session, group_id)
    return GroupAccess(principal, group_id, frozenset({*row.roles, "member"}), row.id)


GroupMemberDep = Annotated[GroupAccess, Depends(group_member)]


async def human_member(access: GroupMemberDep) -> GroupAccess:
    """Comme `group_member`, mais refuse un compte de service : il ne lance que des agents et suit ses
    propres exécutions (FR-002, FR-002b), il n'administre ni ne consulte rien d'autre."""
    if access.is_service_account:
        raise HTTPException(status_code=403, detail="Non disponible pour un compte de service")
    return access


HumanMemberDep = Annotated[GroupAccess, Depends(human_member)]


def require_roles(*roles: str) -> Callable[..., Awaitable[GroupAccess]]:
    """Dépendance qui exige au moins un des rôles (le rôle `member` suffit à tout membre actif)."""

    async def dependency(access: HumanMemberDep) -> GroupAccess:
        if not access.roles.intersection(roles):
            raise HTTPException(status_code=403, detail="Rôle insuffisant")
        return access

    return dependency


async def require_platform_admin(principal: PrincipalDep) -> Principal:
    if principal.kind != "person" or not principal.is_platform_admin:
        raise HTTPException(status_code=403, detail="Réservé à l'administration de la plateforme")
    return principal
