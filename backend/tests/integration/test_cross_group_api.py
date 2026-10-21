"""Isolation inter-groupes de l'API (FR-022, SC-009) : toute tentative d'un groupe sur un autre est un 404."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from harness.runtime.models import say, tool_call
from tests.conftest import Actor
from tests.memory_helpers import person, seed_entry
from tests.world_fixtures import World

Login = Callable[..., Awaitable[Actor]]
PARAM = re.compile(r"{(\w+)}")


def group_routes(world: World) -> list[tuple[str, str]]:
    """Routes de contenu de groupe, énumérées depuis le schéma OpenAPI de l'application (aucune liste en dur)."""
    routes = []
    for path, operations in world.app.openapi()["paths"].items():
        if path.startswith("/api/v1/groups/{group_id}"):
            for method in sorted(operations):
                routes.append((method.upper(), path.removeprefix("/api/v1")))
    return routes


def body_for(method: str, path: str) -> dict[str, Any]:
    """Corps valide pour la route : l'authentification passe (dan est membre de B), c'est donc la
    recherche de la ressource, dans le groupe B, qui doit répondre 404."""
    if method == "PUT" and path.endswith("/classification"):
        return {"classification": "read_only"}
    if method == "PUT" and "/agents/" in path:
        return {
            "name": "x", "instructions": "x", "model_id": "fake", "allowed_tool_ids": [],
            "limits": {"max_iterations": 1, "max_duration_seconds": 1, "max_tokens": 1},
        }
    if method == "PUT":
        return {"content": "x", "version": 1}
    if path.endswith("/decision") and "proposals" in path:
        return {"decision": "accept"}
    if path.endswith("/decision"):
        return {"decision": "approve"}
    if method == "PATCH":
        return {"roles": ["member"]}
    return {}


def fill(path: str, group_id: uuid.UUID, ids: dict[str, str]) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "group_id":
            return str(group_id)
        return ids.get(name, "x" if name == "operation_name" else str(uuid.uuid4()))

    return PARAM.sub(value, path)


def test_every_route_family_is_enumerated(world: World) -> None:
    paths = {p for _, p in group_routes(world)}
    for expected in ("agents", "tools", "runs", "events", "approvals", "memory", "proposals", "members", "service-accounts", "audit"):
        assert any(expected in p for p in paths), expected
    assert len(paths) >= 20


@pytest.fixture
async def outsider(world: World, make_group: Callable[..., Awaitable[uuid.UUID]], login_as: Login) -> tuple[uuid.UUID, Actor]:
    other = await make_group("autre")
    dan = await login_as("dan@example.test", other, ("admin", "tool_manager", "approver", "group_memory_manager"))
    return other, dan


async def test_a_member_of_group_b_gets_404_on_every_route_of_group_a(
    world: World, outsider: tuple[uuid.UUID, Actor]
) -> None:
    _, dan = outsider
    failures = []
    for method, path in group_routes(world):
        r = await world.client.request(method, fill(path, world.group_id, {}), json={}, headers={**dan.headers, "Accept": "application/json"})
        if r.status_code != 404:
            failures.append(f"{method} {path} -> {r.status_code}")
    assert not failures, failures


async def test_group_b_routes_never_reveal_resources_of_group_a_even_with_their_real_ids(
    world: World, outsider: tuple[uuid.UUID, Actor], login_as: Login
) -> None:
    other, dan = outsider
    # ressources réelles du groupe A
    world.models.set(tool_call("crm_send_message", {"text": "x"}), say("fin"))
    run = await world.launch(world.bob)
    await world.wait_for_event(run["id"], "approval_requested")
    [approval] = await world.pending_approvals()
    mem = await seed_entry(world.group_id, scope="agent", agent_id=uuid.UUID(world.agent_id), path="p.md", content="secret", actor=person(world.alice.person_id))
    sa = (await world.client.post(world.url("/service-accounts"), json={"name": "ci", "allowed_agent_ids": []}, headers=world.alice.headers)).json()
    members = (await world.client.get(world.url("/members"), headers=world.alice.headers)).json()
    ids = {
        "agent_id": world.agent_id, "tool_id": world.tool_id, "run_id": run["id"], "approval_id": approval["id"],
        "entry_id": str(mem.entry.id), "service_account_id": sa["id"], "membership_id": members[0]["id"],
        "proposal_id": str(uuid.uuid4()),
    }
    failures = []
    for method, path in group_routes(world):
        if not set(PARAM.findall(path)) - {"group_id", "operation_name"}:
            continue  # sans identifiant de ressource : couvert par le test précédent
        r = await world.client.request(
            method, fill(path, other, ids), json=body_for(method, path),
            headers={**dan.headers, "Accept": "application/json"},
        )
        if r.status_code != 404:
            failures.append(f"{method} {path} -> {r.status_code}: {r.text[:80]}")
    assert not failures, failures
    # rien n'a bougé dans le groupe A
    assert (await world.run(run["id"], world.alice))["status"] == "awaiting_approval"
    assert world.mcp.calls == [] or all(c[0] != "send_message" for c in world.mcp.calls)


async def test_lists_in_group_b_never_include_group_a_data(world: World, outsider: tuple[uuid.UUID, Actor]) -> None:
    other, dan = outsider
    world.models.set(say("fini"))
    await world.wait((await world.launch())["id"])
    await seed_entry(world.group_id, scope="group", path="faits/a.md", content="commun à A", actor=person(world.alice.person_id))
    for path in ("/agents", "/tools", "/runs", "/approvals", "/group-memory", "/members", "/service-accounts", "/audit"):
        r = await world.client.get(f"/groups/{other}{path}", headers=dan.headers)
        body: Any = r.json()
        items = body["items"] if isinstance(body, dict) and "items" in body else body
        assert r.status_code == 200, path
        if path == "/members":
            assert {m["person_email"] for m in items} == {"dan@example.test"}
        else:
            assert items == [], path
