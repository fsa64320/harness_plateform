"""Cycle de vie des membres et des groupes : retrait, rôles par groupe, désactivation, audit (US4, FR-022b, FR-024)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from harness.db.models.audit import AdminAuditEntry
from harness.db.session import get_sessionmaker, group_context, platform_context
from harness.runtime.models import say, tool_call
from tests.conftest import Actor
from tests.world_fixtures import World

Login = Callable[..., Awaitable[Actor]]


async def _member_id(world: World, email: str) -> str:
    members = (await world.client.get(world.url("/members"), headers=world.alice.headers)).json()
    return next(m["id"] for m in members if m["person_email"] == email)


async def test_removing_a_member_stops_their_running_runs_and_denies_access(world: World) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    run = await world.launch(world.bob)
    await world.wait_for_event(run["id"], "tool_call_started")

    r = await world.client.delete(world.url(f"/members/{await _member_id(world, 'bob@example.test')}"), headers=world.alice.headers)
    assert r.status_code == 204

    await asyncio.wait_for(world.runner.wait(uuid.UUID(run["id"])), 5)
    assert (await world.run(run["id"], world.alice))["status"] == "stopped"
    assert (await world.client.get(world.url("/agents"), headers=world.bob.headers)).status_code == 404  # US4-4
    assert (await world.client.get("/me", headers=world.bob.headers)).json()["memberships"] == []
    # les runs des autres membres ne sont pas touchés
    assert (await world.client.get(world.url("/agents"), headers=world.carol.headers)).status_code == 200


async def test_a_removed_member_can_be_invited_again(world: World) -> None:
    mid = await _member_id(world, "bob@example.test")
    await world.client.delete(world.url(f"/members/{mid}"), headers=world.alice.headers)
    r = await world.client.post(world.url("/members"), json={"email": "bob@example.test", "roles": ["approver"]}, headers=world.alice.headers)
    assert r.status_code == 201 and r.json()["id"] == mid and r.json()["roles"] == ["approver", "member"]
    assert (await world.client.get(world.url("/agents"), headers=world.bob.headers)).status_code == 200


async def test_inviting_an_active_member_is_a_conflict(world: World) -> None:
    r = await world.client.post(world.url("/members"), json={"email": "bob@example.test", "roles": ["member"]}, headers=world.alice.headers)
    assert r.status_code == 409


async def test_a_person_in_two_groups_has_only_the_roles_of_each(
    world: World, make_group: Callable[..., Awaitable[uuid.UUID]], login_as: Login
) -> None:
    other = await make_group("autre")
    await login_as("bob@example.test", other, ("tool_manager",))  # bob : simple membre de demo, gestionnaire d'outils dans autre
    me = (await world.client.get("/me", headers=world.bob.headers)).json()
    assert {m["group_name"]: m["roles"] for m in me["memberships"]} == {"demo": ["member"], "autre": ["member", "tool_manager"]}

    body = {"kind": "mcp_server", "name": "x", "mcp": {"url": world.mcp.url}}
    in_demo = await world.client.post(world.url("/tools"), json=body, headers=world.bob.headers)
    in_other = await world.client.post(f"/groups/{other}/tools", json=body, headers=world.bob.headers)
    assert in_demo.status_code == 403  # pas gestionnaire d'outils ici
    assert in_other.status_code == 201  # mais il l'est dans l'autre groupe


async def test_disabling_a_group_stops_its_runs_blocks_access_and_keeps_data(world: World, login_as: Login) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    run = await world.launch(world.bob)
    await world.wait_for_event(run["id"], "tool_call_started")
    root = await login_as("root@example.test", None, platform_admin=True)

    r = await world.client.post(f"/admin/groups/{world.group_id}/disable", headers=root.headers)
    assert r.status_code == 204
    await world.runner.wait(uuid.UUID(run["id"]))

    for actor in (world.alice, world.bob):
        assert (await world.client.get(world.url("/agents"), headers=actor.headers)).status_code == 404
    assert (await world.client.get("/me", headers=world.alice.headers)).json()["memberships"] == []
    async with get_sessionmaker()() as s, s.begin():  # les données sont conservées
        await group_context(s, world.group_id)
        from harness.db.models.agents import Agent
        from harness.db.models.runs import Run

        assert (await s.execute(select(Run.status).where(Run.id == uuid.UUID(run["id"])))).scalar_one() == "stopped"
        assert (await s.execute(select(Agent.id))).scalars().all() != []


async def test_every_admin_action_creates_an_audit_entry_with_author_and_date(world: World, login_as: Login) -> None:
    c, admin = world.client, world.alice.headers
    created = (await c.post(world.url("/members"), json={"email": "erin@example.test", "roles": ["approver"]}, headers=admin)).json()
    await c.patch(world.url(f"/members/{created['id']}"), json={"roles": ["tool_manager"]}, headers=admin)
    await c.delete(world.url(f"/members/{created['id']}"), headers=admin)
    sa = (await c.post(world.url("/service-accounts"), json={"name": "ci", "allowed_agent_ids": []}, headers=admin)).json()
    await c.post(world.url(f"/service-accounts/{sa['id']}/revoke"), headers=admin)

    entries = (await c.get(world.url("/audit"), headers=admin)).json()
    actions = [e["action"] for e in entries]
    for expected in ("member.invite", "member.update", "member.remove", "service_account.create", "service_account.revoke"):
        assert expected in actions, expected
    mine = [e for e in entries if e["action"].startswith(("member.", "service_account."))]
    assert all(e["actor_id"] == str(world.alice.person_id) and e["occurred_at"] for e in mine)
    update = next(e for e in entries if e["action"] == "member.update")
    assert update["details"]["roles"] == {"from": ["approver", "member"], "to": ["member", "tool_manager"]}
    assert "token" not in str(entries)  # jamais de secret dans le journal


async def test_platform_actions_are_audited_without_group_content_access(client: Any, login_as: Login, owner_session: Any) -> None:
    root = await login_as("root@example.test", None, platform_admin=True)
    r = await client.post("/admin/groups", json={"name": "equipe-b", "admin_emails": ["zed@example.test"]}, headers=root.headers)
    group_id = uuid.UUID(r.json()["id"])
    await client.post(f"/admin/groups/{group_id}/disable", headers=root.headers)
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group_id)
        await platform_context(s)
        entries = (await s.execute(select(AdminAuditEntry).order_by(AdminAuditEntry.occurred_at))).scalars().all()
    assert [e.action for e in entries] == ["group.create", "group.disable"]
    assert all(e.actor_id is not None for e in entries)
