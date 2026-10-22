"""Contrat : catalogue d'outils, serveurs MCP et classification (FR-005a, FR-010, FR-011)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from tests.world_fixtures import World


async def test_register_list_and_get_an_mcp_server(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    body = {"kind": "mcp_server", "name": "autre", "description": "d", "mcp": {"url": world.mcp.url}}
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    tool = r.json()
    assert tool["kind"] == "mcp_server" and tool["status"] == "active" and tool["has_credential"] is False
    assert {o["name"] for o in tool["operations"]} == {"lookup", "send_message", "wait", "whoami"}
    assert {o["classification"] for o in tool["operations"]} == {"unclassified"}  # défaut sûr

    listing = await world.client.get(world.url("/tools?kind=mcp_server"), headers=world.bob.headers)
    assert listing.status_code == 200
    assert_matches_contract(listing)
    assert {t["name"] for t in listing.json()} == {"crm", "autre"}

    detail = await world.client.get(world.url(f"/tools/{tool['id']}"), headers=world.bob.headers)
    assert detail.status_code == 200
    assert_matches_contract(detail)


async def test_classify_an_operation(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    r = await world.client.put(
        world.url(f"/tools/{world.tool_id}/operations/send_message/classification"),
        json={"classification": "external_effect"},
        headers=world.alice.headers,
    )
    assert r.status_code == 200
    assert_matches_contract(r)
    assert r.json()["name"] == "send_message" and r.json()["classification"] == "external_effect"


async def test_only_a_tool_manager_can_register_or_classify(world: World) -> None:
    body = {"kind": "mcp_server", "name": "x", "mcp": {"url": world.mcp.url}}
    assert (await world.client.post(world.url("/tools"), json=body, headers=world.bob.headers)).status_code == 403
    r = await world.client.put(
        world.url(f"/tools/{world.tool_id}/operations/lookup/classification"),
        json={"classification": "read_only"},
        headers=world.bob.headers,
    )
    assert r.status_code == 403


async def test_unreachable_server_is_rejected_with_422(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    body = {"kind": "mcp_server", "name": "mort", "mcp": {"url": "https://localhost:1/mcp"}}
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 422
    assert_matches_contract(r)


async def test_non_https_url_is_rejected_with_422(world: World) -> None:
    body = {"kind": "mcp_server", "name": "clair", "mcp": {"url": "http://localhost:9/mcp"}}
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 422


async def test_registering_and_classifying_are_audited(world: World) -> None:
    from sqlalchemy import select

    from harness.db.models.audit import AdminAuditEntry
    from harness.db.session import get_sessionmaker, group_context

    await world.client.put(
        world.url(f"/tools/{world.tool_id}/operations/send_message/classification"),
        json={"classification": "read_only"},
        headers=world.alice.headers,
    )
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        actions = [a.action for a in (await s.execute(select(AdminAuditEntry))).scalars()]
    assert "tool.register" in actions and "tool.classify" in actions


async def test_a_member_of_another_group_gets_404(
    world: World, make_group, login_as  # type: ignore[no-untyped-def]
) -> None:
    other = await make_group("autre")
    dan = await login_as("dan@example.test", other, ("tool_manager",))
    for method, path in (("GET", "/tools"), ("GET", f"/tools/{world.tool_id}")):
        r = await world.client.request(method, world.url(path), headers=dan.headers)
        assert r.status_code == 404
