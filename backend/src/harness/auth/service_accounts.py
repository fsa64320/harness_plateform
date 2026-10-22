"""Comptes de service : jeton opaque, stocké salé et haché, révocable (FR-002a, research R-09).

Format du jeton : `hsa_` + id du compte (32 hex) + id du groupe (32 hex) + secret. Les identifiants
permettent de retrouver le compte sous la RLS de son groupe sans lecture inter-groupes ; seul le secret
authentifie, et il n'est comparé qu'à son empreinte salée."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.auth.permissions import Principal
from harness.db.models.identity import Group, GroupStatus, ServiceAccount, ServiceAccountStatus
from harness.db.session import group_context

PREFIX = "hsa_"
_HEX = 32


@dataclass(frozen=True)
class NewToken:
    token: str
    secret_hash: str
    salt: str
    prefix: str


def _digest(salt: str, secret: str) -> str:
    return hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()


def generate_token(group_id: uuid.UUID, account_id: uuid.UUID) -> NewToken:
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    return NewToken(
        token=f"{PREFIX}{account_id.hex}{group_id.hex}{secret}",
        secret_hash=_digest(salt, secret),
        salt=salt,
        prefix=f"{PREFIX}{account_id.hex[:8]}",
    )


def _parse(token: str) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    if not token.startswith(PREFIX) or len(token) <= len(PREFIX) + 2 * _HEX:
        return None
    body = token[len(PREFIX) :]
    try:
        return uuid.UUID(hex=body[_HEX : 2 * _HEX]), uuid.UUID(hex=body[:_HEX]), body[2 * _HEX :]
    except ValueError:
        return None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Jeton invalide")


async def authenticate_token(session: AsyncSession, token: str) -> Principal:
    """Principal d'un compte de service actif de groupe actif ; 401 dans tout autre cas."""
    parsed = _parse(token)
    if parsed is None:
        raise _unauthorized()
    group_id, account_id, secret = parsed
    await group_context(session, group_id)
    account = (await session.execute(select(ServiceAccount).where(ServiceAccount.id == account_id))).scalar_one_or_none()
    if account is None or not hmac.compare_digest(account.token_hash, _digest(account.token_salt, secret)):
        raise _unauthorized()
    if account.status != ServiceAccountStatus.ACTIVE:
        raise _unauthorized()
    status = (await session.execute(select(Group.status).where(Group.id == group_id))).scalar()
    if status != GroupStatus.ACTIVE:
        raise _unauthorized()
    return Principal(
        kind="service_account",
        id=account.id,
        group_id=group_id,
        allowed_agent_ids=frozenset(uuid.UUID(i) for i in account.allowed_agent_ids),
    )
