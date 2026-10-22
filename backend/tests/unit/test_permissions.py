"""Matrice des rôles et règle « 404, jamais 403 » pour les non-membres (FR-020, FR-022, FR-022a)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from harness.auth.permissions import GroupAccess, group_member, require_roles
from tests.conftest import Actor, install_auth_override

ROLES = ["admin", "tool_manager", "group_memory_manager", "approver", "member"]


@pytest_asyncio.fixture
async def probe_client(
    migrated_db: dict[str, str], clean_db: None
) -> AsyncIterator[httpx.AsyncClient]:
    """Application minimale dont chaque route exige un rôle ; l'authentification est simulée."""
    app = FastAPI()
    router = APIRouter(prefix="/groups/{group_id}")

    @router.get("/probe/any")
    async def any_member(access: GroupAccess = Depends(group_member)) -> dict[str, str]:
        return {"roles": ",".join(sorted(access.roles))}

    def make_handler(role: str):  # type: ignore[no-untyped-def]
        async def handler(access: GroupAccess = Depends(require_roles(role))) -> dict[str, str]:
            return {"ok": role}

        return handler

    for role in ROLES:
        router.add_api_route(f"/probe/{role}", make_handler(role), methods=["GET"])

    app.include_router(router)
    install_auth_override(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _status(c: httpx.AsyncClient, actor: Actor, group: uuid.UUID, route: str) -> int:
    r = await c.get(f"/groups/{group}/probe/{route}", headers=actor.headers)
    return r.status_code


async def test_each_role_only_passes_its_own_route(
    probe_client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
) -> None:
    g = await make_group()
    for role in ROLES:
        actor = await login_as(f"{role}@example.test", g, (role,))
        for route in ROLES:
            expected = 200 if route == role or route == "member" else 403
            assert await _status(probe_client, actor, g, route) == expected, (role, route)


async def test_roles_accumulate(
    probe_client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
) -> None:
    g = await make_group()
    actor = await login_as("both@example.test", g, ("approver", "tool_manager"))
    assert await _status(probe_client, actor, g, "approver") == 200
    assert await _status(probe_client, actor, g, "tool_manager") == 200
    assert await _status(probe_client, actor, g, "admin") == 403


async def test_non_member_gets_404_never_403(
    probe_client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
) -> None:
    g_a, g_b = await make_group(), await make_group()
    outsider = await login_as("outsider@example.test", g_b, ("admin",))
    for route in ["any", *ROLES]:
        assert await _status(probe_client, outsider, g_a, route) == 404, route


async def test_unknown_group_is_404(
    probe_client: httpx.AsyncClient, login_as: Callable[..., Awaitable[Actor]]
) -> None:
    actor = await login_as("lonely@example.test")
    assert await _status(probe_client, actor, uuid.uuid4(), "any") == 404


async def test_platform_admin_without_membership_gets_404_on_group_content(
    probe_client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
) -> None:
    g = await make_group()
    root = await login_as("root@example.test", None, platform_admin=True)
    for route in ["any", *ROLES]:
        assert await _status(probe_client, root, g, route) == 404, route


async def test_removed_member_and_disabled_group_are_404(
    probe_client: httpx.AsyncClient,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
    owner_session: Callable[[], AsyncSession],
) -> None:
    from sqlalchemy import update

    from harness.db.models.identity import Group, Membership

    g = await make_group()
    actor = await login_as("ex@example.test", g)
    assert await _status(probe_client, actor, g, "any") == 200

    async with owner_session() as s, s.begin():
        await s.execute(update(Membership).where(Membership.group_id == g).values(status="removed"))
    assert await _status(probe_client, actor, g, "any") == 404

    async with owner_session() as s, s.begin():
        await s.execute(update(Membership).where(Membership.group_id == g).values(status="active"))
        await s.execute(update(Group).where(Group.id == g).values(status="disabled"))
    assert await _status(probe_client, actor, g, "any") == 404


async def test_unauthenticated_is_401(
    probe_client: httpx.AsyncClient, make_group: Callable[..., Awaitable[uuid.UUID]]
) -> None:
    g = await make_group()
    r = await probe_client.get(f"/groups/{g}/probe/any")
    assert r.status_code == 401
