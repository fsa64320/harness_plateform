"""Contrat : administration des groupes, membres, comptes de service et journal (FR-002a, FR-022a, FR-023, FR-024)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx

from tests.conftest import Actor
from tests.world_fixtures import World

Login = Callable[..., Awaitable[Actor]]


async def test_platform_admin_creates_a_group_and_designates_its_first_admins(
    client: httpx.AsyncClient, login_as: Login, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    root = await login_as("root@example.test", None, platform_admin=True)
    r = await client.post(
        "/admin/groups", json={"name": "equipe-a", "admin_emails": ["alice@example.test"]}, headers=root.headers
    )
    assert r.status_code == 201
    assert_matches_contract(r)
    group = r.json()
    assert group["name"] == "equipe-a" and group["status"] == "active"

    # la première administratrice accède à son groupe avec le rôle admin ; l'admin de plateforme, non (FR-022a)
    alice = await login_as("alice@example.test")
    me = (await client.get("/me", headers=alice.headers)).json()
    assert [(m["group_name"], m["roles"]) for m in me["memberships"]] == [("equipe-a", ["admin", "member"])]
    assert (await client.get(f"/groups/{group['id']}/members", headers=root.headers)).status_code == 404


async def test_creating_a_group_requires_platform_admin(
    client: httpx.AsyncClient, login_as: Login, make_group: Callable[..., Awaitable[uuid.UUID]],
    assert_matches_contract: Callable[[httpx.Response], None],
) -> None:
    g = await make_group()
    group_admin = await login_as("admin@example.test", g, ("admin",))
    r = await client.post(
        "/admin/groups", json={"name": "pirate", "admin_emails": ["x@example.test"]}, headers=group_admin.headers
    )
    assert r.status_code == 403
    assert_matches_contract(r)


async def test_duplicate_group_name_is_a_conflict(client: httpx.AsyncClient, login_as: Login) -> None:
    root = await login_as("root@example.test", None, platform_admin=True)
    body = {"name": "doublon", "admin_emails": ["a@example.test"]}
    assert (await client.post("/admin/groups", json=body, headers=root.headers)).status_code == 201
    assert (await client.post("/admin/groups", json=body, headers=root.headers)).status_code == 409


async def test_platform_admin_lists_and_disables_groups(
    client: httpx.AsyncClient, login_as: Login, world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    root = await login_as("root@example.test", None, platform_admin=True)
    listing = await client.get("/admin/groups", headers=root.headers)
    assert listing.status_code == 200
    assert [g["name"] for g in listing.json()] == ["demo"]

    r = await client.post(f"/admin/groups/{world.group_id}/disable", headers=root.headers)
    assert r.status_code == 204
    assert_matches_contract(r)
    assert (await client.get(world.url("/agents"), headers=world.alice.headers)).status_code == 404  # accès bloqué
    assert (await client.get("/admin/groups", headers=root.headers)).json()[0]["status"] == "disabled"


async def test_members_list_invite_update_and_remove(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    c, admin = world.client, world.alice.headers
    listing = await c.get(world.url("/members"), headers=world.bob.headers)
    assert listing.status_code == 200
    assert_matches_contract(listing)
    assert {m["person_email"] for m in listing.json()} == {"alice@example.test", "bob@example.test", "carol@example.test"}

    invited = await c.post(world.url("/members"), json={"email": "erin@example.test", "roles": ["approver"]}, headers=admin)
    assert invited.status_code == 201
    assert_matches_contract(invited)
    assert invited.json()["roles"] == ["approver", "member"] and invited.json()["status"] == "active"

    mid = invited.json()["id"]
    patched = await c.patch(
        world.url(f"/members/{mid}"),
        json={"roles": ["tool_manager", "approver"], "max_concurrent_runs": 3, "max_tokens_per_day": 5000},
        headers=admin,
    )
    assert patched.status_code == 200
    assert_matches_contract(patched)
    assert patched.json()["roles"] == ["approver", "member", "tool_manager"]
    assert (patched.json()["max_concurrent_runs"], patched.json()["max_tokens_per_day"]) == (3, 5000)

    removed = await c.delete(world.url(f"/members/{mid}"), headers=admin)
    assert removed.status_code == 204
    assert_matches_contract(removed)
    assert mid not in {m["id"] for m in (await c.get(world.url("/members"), headers=admin)).json() if m["status"] == "active"}


async def test_only_admins_manage_members(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    c, bob = world.client, world.bob.headers
    r = await c.post(world.url("/members"), json={"email": "x@example.test", "roles": ["member"]}, headers=bob)
    assert r.status_code == 403
    assert_matches_contract(r)
    mid = (await c.get(world.url("/members"), headers=bob)).json()[0]["id"]
    assert (await c.patch(world.url(f"/members/{mid}"), json={"roles": ["admin"]}, headers=bob)).status_code == 403
    assert (await c.delete(world.url(f"/members/{mid}"), headers=bob)).status_code == 403


async def test_the_last_admin_cannot_be_removed_or_demoted(world: World) -> None:
    c, admin = world.client, world.alice.headers
    members = (await c.get(world.url("/members"), headers=admin)).json()
    alice = next(m for m in members if m["person_email"] == "alice@example.test")
    assert (await c.patch(world.url(f"/members/{alice['id']}"), json={"roles": ["member"]}, headers=admin)).status_code == 409
    assert (await c.delete(world.url(f"/members/{alice['id']}"), headers=admin)).status_code == 409


async def test_service_account_token_is_returned_only_at_creation(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    c, admin = world.client, world.alice.headers
    r = await c.post(
        world.url("/service-accounts"),
        json={"name": "ci", "allowed_agent_ids": [world.agent_id], "max_concurrent_runs": 2, "max_tokens_per_day": 10000},
        headers=admin,
    )
    assert r.status_code == 201
    assert_matches_contract(r)
    account = r.json()
    assert account["token"].startswith("hsa_") and account["token"].startswith(account["token_prefix"])
    assert account["status"] == "active" and account["allowed_agent_ids"] == [world.agent_id]

    listing = await c.get(world.url("/service-accounts"), headers=admin)
    assert listing.status_code == 200 and account["token"] not in listing.text
    assert "token" not in listing.json()[0]

    revoked = await c.post(world.url(f"/service-accounts/{account['id']}/revoke"), headers=admin)
    assert revoked.status_code == 204
    assert_matches_contract(revoked)
    assert (await c.get(world.url("/service-accounts"), headers=admin)).json()[0]["status"] == "revoked"


async def test_only_admins_manage_service_accounts(world: World) -> None:
    body = {"name": "ci", "allowed_agent_ids": []}
    assert (await world.client.post(world.url("/service-accounts"), json=body, headers=world.bob.headers)).status_code == 403
    assert (await world.client.get(world.url("/service-accounts"), headers=world.bob.headers)).status_code == 403


async def test_service_account_can_only_be_allowed_agents_of_its_own_group(world: World) -> None:
    r = await world.client.post(
        world.url("/service-accounts"),
        json={"name": "ci", "allowed_agent_ids": [str(uuid.uuid4())]},
        headers=world.alice.headers,
    )
    assert r.status_code == 422


async def test_audit_log_lists_administration_actions(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    await world.client.post(world.url("/members"), json={"email": "erin@example.test", "roles": ["member"]}, headers=world.alice.headers)
    r = await world.client.get(world.url("/audit"), headers=world.alice.headers)
    assert r.status_code == 200
    assert_matches_contract(r)
    actions = [e["action"] for e in r.json()]
    assert "tool.register" in actions and "tool.classify" in actions and "member.invite" in actions
    invite = next(e for e in r.json() if e["action"] == "member.invite")
    assert invite["actor_id"] == str(world.alice.person_id) and invite["target_type"] == "membership"
    assert invite["occurred_at"]
    assert (await world.client.get(world.url("/audit"), headers=world.bob.headers)).status_code == 403
