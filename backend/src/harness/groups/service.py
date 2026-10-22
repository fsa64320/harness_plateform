"""Administration des groupes et des membres (FR-020, FR-022a, FR-022b, FR-023, FR-024).

Chaque action est journalisée avec son auteur et sa date. Les opérations de plateforme (créer, désactiver
un groupe) ne donnent accès à aucun contenu de groupe ; l'arrêt des exécutions concernées est confié au
lanceur par l'appelant, une fois la transaction validée."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.auth import service_accounts
from harness.auth.permissions import GroupAccess
from harness.db.models.agents import Agent
from harness.db.models.audit import AdminAuditEntry
from harness.db.models.identity import (
    Group,
    GroupStatus,
    Membership,
    MembershipStatus,
    Person,
    Role,
    ServiceAccount,
    ServiceAccountStatus,
)
from harness.db.session import group_context, platform_context
from harness.groups.audit import record_admin_action


class GroupError(Exception):
    """Erreur métier d'administration, traduite en réponse HTTP par la couche API."""


class NotFound(GroupError):
    pass


class Conflict(GroupError):
    pass


class Invalid(GroupError):
    pass


def normalize_roles(roles: Sequence[str]) -> list[str]:
    """Tout membre actif a au moins le rôle `member` ; les rôles inconnus sont refusés."""
    valid = {r.value for r in Role}
    unknown = set(roles) - valid
    if unknown:
        raise Invalid("Rôle inconnu : " + ", ".join(sorted(unknown)))
    return sorted({*roles, Role.MEMBER.value})


# --- plateforme ---------------------------------------------------------------------------------


async def create_group(
    session: AsyncSession, *, name: str, admin_emails: Sequence[str], actor_id: uuid.UUID | None, via: str = "api"
) -> Group:
    """Crée le groupe et désigne ses premiers administrateurs (FR-022a)."""
    await platform_context(session)
    if (await session.execute(select(Group.id).where(Group.name == name))).first() is not None:
        raise Conflict(f"Un groupe « {name} » existe déjà")
    group = Group(name=name)
    session.add(group)
    await session.flush()
    await group_context(session, group.id)
    for email in dict.fromkeys(e.strip().lower() for e in admin_emails):
        person = (await session.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
        if person is None:
            person = Person(email=email, display_name=email.split("@")[0])
            session.add(person)
            await session.flush()
        session.add(Membership(group_id=group.id, person_id=person.id, roles=["admin", "member"]))
    await record_admin_action(
        session,
        group_id=group.id,
        actor_id=actor_id,
        action="group.create",
        target_type="group",
        target_id=group.id,
        details={"name": name, "admins": list(admin_emails), "via": via},
    )
    return group


async def list_groups(session: AsyncSession) -> list[Group]:
    await platform_context(session)
    return list((await session.execute(select(Group).order_by(Group.name))).scalars())


async def disable_group(session: AsyncSession, *, group_id: uuid.UUID, actor_id: uuid.UUID) -> Group:
    """Désactive le groupe : plus aucun accès ; les données sont conservées."""
    await platform_context(session)
    group = (await session.execute(select(Group).where(Group.id == group_id).with_for_update())).scalar_one_or_none()
    if group is None:
        raise NotFound("Groupe introuvable")
    group.status = GroupStatus.DISABLED
    await session.flush()
    await group_context(session, group_id)
    await record_admin_action(
        session, group_id=group_id, actor_id=actor_id, action="group.disable", target_type="group",
        target_id=group_id, details={"name": group.name},
    )
    return group


# --- membres ------------------------------------------------------------------------------------


async def list_members(session: AsyncSession) -> list[tuple[Membership, str]]:
    rows = await session.execute(
        select(Membership, Person.email).join(Person, Person.id == Membership.person_id).order_by(Person.email)
    )
    return [(m, email) for m, email in rows.all()]


async def _active_admins(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Membership).where(
                    Membership.status == MembershipStatus.ACTIVE, Membership.roles.contains(["admin"])
                )
            )
        ).scalar_one()
    )


async def _membership(session: AsyncSession, membership_id: uuid.UUID) -> tuple[Membership, str]:
    row = (
        await session.execute(
            select(Membership, Person.email)
            .join(Person, Person.id == Membership.person_id)
            .where(Membership.id == membership_id)
            .with_for_update(of=Membership)
        )
    ).first()
    if row is None:
        raise NotFound("Membre introuvable")
    return row[0], row[1]


async def invite_member(session: AsyncSession, access: GroupAccess, *, email: str, roles: Sequence[str]) -> tuple[Membership, str]:
    email = email.strip().lower()
    final_roles = normalize_roles(roles)
    person = (await session.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
    if person is None:
        person = Person(email=email, display_name=email.split("@")[0])
        session.add(person)
        await session.flush()
    membership = (
        await session.execute(
            select(Membership).where(Membership.person_id == person.id).with_for_update()
        )
    ).scalar_one_or_none()
    if membership is not None and membership.status == MembershipStatus.ACTIVE:
        raise Conflict(f"{email} est déjà membre de ce groupe")
    if membership is None:
        membership = Membership(group_id=access.group_id, person_id=person.id, roles=final_roles)
        session.add(membership)
    else:  # personne retirée puis réinvitée : son appartenance est réactivée
        membership.status, membership.roles = MembershipStatus.ACTIVE, final_roles
    await session.flush()
    await record_admin_action(
        session, group_id=access.group_id, actor_id=access.principal.id, action="member.invite",
        target_type="membership", target_id=membership.id, details={"email": email, "roles": final_roles},
    )
    return membership, email


async def update_member(
    session: AsyncSession, access: GroupAccess, membership_id: uuid.UUID, changes: dict[str, Any]
) -> tuple[Membership, str]:
    membership, email = await _membership(session, membership_id)
    details: dict[str, Any] = {"email": email}
    if "roles" in changes and changes["roles"] is not None:
        new_roles = normalize_roles(changes["roles"])
        if "admin" in membership.roles and "admin" not in new_roles and await _active_admins(session) <= 1:
            raise Conflict("Le dernier administrateur du groupe ne peut pas perdre ce rôle")
        details["roles"] = {"from": sorted(membership.roles), "to": new_roles}
        membership.roles = new_roles
    for quota in ("max_concurrent_runs", "max_tokens_per_day"):
        if changes.get(quota) is not None:
            details[quota] = {"from": getattr(membership, quota), "to": changes[quota]}
            setattr(membership, quota, changes[quota])
    await session.flush()
    await record_admin_action(
        session, group_id=access.group_id, actor_id=access.principal.id, action="member.update",
        target_type="membership", target_id=membership.id, details=details,
    )
    return membership, email


async def remove_member(session: AsyncSession, access: GroupAccess, membership_id: uuid.UUID) -> Membership:
    """Retire la personne du groupe ; l'appelant arrête ensuite ses exécutions en cours."""
    membership, email = await _membership(session, membership_id)
    if membership.status == MembershipStatus.REMOVED:
        return membership
    if "admin" in membership.roles and await _active_admins(session) <= 1:
        raise Conflict("Le dernier administrateur du groupe ne peut pas être retiré")
    membership.status = MembershipStatus.REMOVED
    await session.flush()
    await record_admin_action(
        session, group_id=access.group_id, actor_id=access.principal.id, action="member.remove",
        target_type="membership", target_id=membership.id, details={"email": email},
    )
    return membership


# --- comptes de service ---------------------------------------------------------------------------


async def create_service_account(
    session: AsyncSession,
    access: GroupAccess,
    *,
    name: str,
    allowed_agent_ids: Sequence[uuid.UUID],
    max_concurrent_runs: int | None,
    max_tokens_per_day: int | None,
) -> tuple[ServiceAccount, str]:
    """Crée le compte ; le jeton est renvoyé ici une seule fois, seule son empreinte salée est gardée."""
    found = set((await session.execute(select(Agent.id).where(Agent.id.in_(set(allowed_agent_ids))))).scalars())
    if missing := set(allowed_agent_ids) - found:
        raise Invalid("Agent hors du groupe : " + ", ".join(sorted(map(str, missing))))
    account_id = uuid.uuid4()
    new = service_accounts.generate_token(access.group_id, account_id)
    account = ServiceAccount(
        id=account_id, group_id=access.group_id, name=name, token_hash=new.secret_hash, token_salt=new.salt,
        token_prefix=new.prefix, allowed_agent_ids=[str(i) for i in dict.fromkeys(allowed_agent_ids)],
    )
    if max_concurrent_runs is not None:
        account.max_concurrent_runs = max_concurrent_runs
    if max_tokens_per_day is not None:
        account.max_tokens_per_day = max_tokens_per_day
    session.add(account)
    await session.flush()
    await record_admin_action(
        session, group_id=access.group_id, actor_id=access.principal.id, action="service_account.create",
        target_type="service_account", target_id=account.id,
        details={"name": name, "allowed_agent_ids": account.allowed_agent_ids, "prefix": new.prefix},
    )
    return account, new.token


async def list_service_accounts(session: AsyncSession) -> list[ServiceAccount]:
    return list((await session.execute(select(ServiceAccount).order_by(ServiceAccount.created_at))).scalars())


async def revoke_service_account(session: AsyncSession, access: GroupAccess, account_id: uuid.UUID) -> ServiceAccount:
    """Révoque le compte : tout nouvel appel est refusé ; l'appelant arrête ses exécutions."""
    account = (
        await session.execute(select(ServiceAccount).where(ServiceAccount.id == account_id).with_for_update())
    ).scalar_one_or_none()
    if account is None:
        raise NotFound("Compte de service introuvable")
    if account.status != ServiceAccountStatus.REVOKED:
        account.status = ServiceAccountStatus.REVOKED
        await session.flush()
        await record_admin_action(
            session, group_id=access.group_id, actor_id=access.principal.id, action="service_account.revoke",
            target_type="service_account", target_id=account.id, details={"name": account.name},
        )
    return account


async def list_audit(session: AsyncSession) -> list[AdminAuditEntry]:
    return list(
        (await session.execute(select(AdminAuditEntry).order_by(AdminAuditEntry.occurred_at, AdminAuditEntry.created_at))).scalars()
    )
