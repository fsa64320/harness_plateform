"""GET /me (FR-022b) et `harness-admin create-group` (FR-022a, FR-024)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select

from harness.cli import create_group
from harness.db.models.audit import AdminAuditEntry
from harness.db.models.identity import Group, Membership
from harness.db.session import platform_context
from tests.conftest import Actor


async def test_me_requires_authentication(client: httpx.AsyncClient, clean_db: None) -> None:
    assert (await client.get("/me")).status_code == 401


async def test_me_lists_only_own_active_groups_with_their_roles(
    client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
) -> None:
    g1, g2, other = await make_group("g1"), await make_group("g2"), await make_group("other")
    me = await login_as("erin@example.test", g1, ("approver",))
    await login_as("erin@example.test", g2, ("tool_manager",))
    await login_as("zed@example.test", other, ("admin",))

    body = (await client.get("/me", headers=me.headers)).json()
    assert body["email"] == "erin@example.test"
    by_group = {m["group_name"]: m["roles"] for m in body["memberships"]}
    assert by_group == {"g1": ["approver", "member"], "g2": ["member", "tool_manager"]}


async def test_create_group_creates_admin_membership_and_audit_entry(
    migrated_db: dict[str, str], clean_db: None
) -> None:
    from harness.db.session import get_sessionmaker

    group_id = await create_group("demo", ["alice@example.test"])

    async with get_sessionmaker()() as s, s.begin():
        await platform_context(s)
        group = (await s.execute(select(Group).where(Group.id == group_id))).scalar_one()
        assert group.name == "demo" and group.status == "active"
    async with get_sessionmaker()() as s, s.begin():
        from harness.db.session import group_context

        await group_context(s, group_id)
        members = (await s.execute(select(Membership))).scalars().all()
        assert len(members) == 1 and "admin" in members[0].roles
        audit = (await s.execute(select(AdminAuditEntry))).scalars().all()
        assert [a.action for a in audit] == ["group.create"]
