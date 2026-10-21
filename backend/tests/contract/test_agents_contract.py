"""Contrat : agents (FR-001, FR-001a) : lecture, lancement et duplication ouverts à tout membre."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx

from tests.world_fixtures import World


def _body(world: World, **overrides: object) -> dict[str, object]:
    return {
        "name": "veille",
        "instructions": "Surveille la concurrence.",
        "model_id": "fake",
        "allowed_tool_ids": [world.ops["lookup"]],
        "limits": {"max_iterations": 5, "max_duration_seconds": 30, "max_tokens": 5000},
        **overrides,
    }


async def test_agent_crud_matches_the_contract(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    c, h = world.client, world.bob.headers
    r = await c.post(world.url("/agents"), json=_body(world), headers=h)
    assert r.status_code == 201
    assert_matches_contract(r)
    agent = r.json()
    assert agent["created_by"] == str(world.bob.person_id)
    assert agent["agent_memory_enabled"] is True and agent["group_memory_enabled"] is False
    assert agent["subagents_enabled"] is False and agent["config_changed_at"] is None

    listing = await c.get(world.url("/agents"), headers=h)
    assert_matches_contract(listing)
    assert agent["id"] in {a["id"] for a in listing.json()}

    got = await c.get(world.url(f"/agents/{agent['id']}"), headers=h)
    assert_matches_contract(got)

    upd = await c.put(world.url(f"/agents/{agent['id']}"), json=_body(world, name="veille v2"), headers=h)
    assert upd.status_code == 200 and upd.json()["name"] == "veille v2"
    assert_matches_contract(upd)

    dele = await c.delete(world.url(f"/agents/{agent['id']}"), headers=h)
    assert dele.status_code == 204
    assert_matches_contract(dele)
    assert (await c.get(world.url(f"/agents/{agent['id']}"), headers=h)).status_code == 404


async def test_duplicate_copies_the_configuration(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    r = await world.client.post(world.url(f"/agents/{world.agent_id}/duplicate"), headers=world.carol.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    copy = r.json()
    original = (await world.client.get(world.url(f"/agents/{world.agent_id}"), headers=world.bob.headers)).json()
    assert copy["id"] != original["id"] and copy["created_by"] == str(world.carol.person_id)
    for key in ("instructions", "model_id", "allowed_tool_ids", "limits", "subagents_enabled"):
        assert copy[key] == original[key]


async def test_fr_001a_any_member_reads_launches_and_duplicates_but_only_author_or_admin_edits(world: World) -> None:
    c = world.client
    agent = (await c.post(world.url("/agents"), json=_body(world), headers=world.bob.headers)).json()
    base = world.url(f"/agents/{agent['id']}")

    assert (await c.get(base, headers=world.carol.headers)).status_code == 200  # lire
    assert (await c.post(base + "/duplicate", headers=world.carol.headers)).status_code == 201  # dupliquer
    launched = await c.post(
        world.url("/runs"), json={"agent_id": agent["id"], "task": "x"}, headers=world.carol.headers
    )
    assert launched.status_code == 202  # lancer

    assert (await c.put(base, json=_body(world, name="piraté"), headers=world.carol.headers)).status_code == 403
    assert (await c.delete(base, headers=world.carol.headers)).status_code == 403
    assert (await c.put(base, json=_body(world, name="par l'admin"), headers=world.alice.headers)).status_code == 200
    assert (await c.delete(base, headers=world.alice.headers)).status_code == 204


async def test_model_without_tool_calling_is_rejected_with_422(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    from harness.runtime.models import FakeProvider

    world.app.state.model_provider = FakeProvider([], tool_calling=False)
    r = await world.client.post(world.url("/agents"), json=_body(world), headers=world.bob.headers)
    assert r.status_code == 422
    assert_matches_contract(r)
    assert "appel d'outils" in r.json()["message"]


async def test_tool_outside_the_group_catalog_is_rejected_with_422(world: World) -> None:
    r = await world.client.post(
        world.url("/agents"), json=_body(world, allowed_tool_ids=[str(uuid.uuid4())]), headers=world.bob.headers
    )
    assert r.status_code == 422


async def test_invalid_limits_are_rejected_with_422(world: World) -> None:
    bad = _body(world, limits={"max_iterations": 0, "max_duration_seconds": 30, "max_tokens": 5000})
    assert (await world.client.post(world.url("/agents"), json=bad, headers=world.bob.headers)).status_code == 422
