"""Contrat : exécutions (FR-002, FR-002b, FR-004, FR-008)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import httpx

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def test_launch_get_and_events_match_the_contract(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("fini"))
    r = await world.client.post(
        world.url("/runs"), json={"agent_id": world.agent_id, "task": "cherche"}, headers=world.bob.headers
    )
    assert r.status_code == 202
    assert_matches_contract(r)
    run = r.json()
    assert run["task"] == "cherche" and run["triggered_by_id"] == str(world.bob.person_id)
    assert run["triggered_by_type"] == "person" and run["launch_mode"] == "manual"

    await world.wait(run["id"])
    got = await world.client.get(world.url(f"/runs/{run['id']}"), headers=world.bob.headers)
    assert got.status_code == 200
    assert_matches_contract(got)
    assert got.json()["status"] == "succeeded"

    events = await world.client.get(
        world.url(f"/runs/{run['id']}/events"), headers={**world.bob.headers, "Accept": "application/json"}
    )
    assert events.status_code == 200
    assert_matches_contract(events)
    assert events.json()[0]["type"] == "run_started"


async def test_events_are_paginated_by_after_seq(world: World) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("fini"))
    run = await world.launch()
    await world.wait(run["id"])
    r = await world.client.get(
        world.url(f"/runs/{run['id']}/events?after_seq=2"), headers={**world.bob.headers, "Accept": "application/json"}
    )
    seqs = [e["seq"] for e in r.json()]
    assert seqs and seqs[0] == 3 and seqs == sorted(seqs)


async def test_list_runs_supports_the_fr_008_filters(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    other = await world.create_agent(name="autre agent")
    world.models.set(say("a"))
    mine = await world.launch(world.bob)
    await world.wait(mine["id"])
    world.models.set(say("b"))
    theirs = await world.launch(world.carol, agent_id=other["id"])
    await world.wait(theirs["id"])
    h = world.alice.headers

    async def ids(query: str) -> set[str]:
        r = await world.client.get(world.url(f"/runs?{query}"), headers=h)
        assert r.status_code == 200
        assert_matches_contract(r)
        return {x["id"] for x in r.json()["items"]}

    assert await ids("") == {mine["id"], theirs["id"]}
    assert await ids(f"agent_id={other['id']}") == {theirs["id"]}
    assert await ids(f"triggered_by={world.bob.person_id}") == {mine["id"]}
    assert await ids("status=succeeded") == {mine["id"], theirs["id"]}
    assert await ids("status=failed") == set()
    now = dt.datetime.now(dt.UTC)
    assert await ids(f"from={(now + dt.timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}") == set()
    assert await ids(f"to={(now + dt.timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}") == {mine["id"], theirs["id"]}


async def test_list_runs_is_paginated_by_cursor(world: World) -> None:
    ids = []
    for i in range(3):
        world.models.set(say(str(i)))
        run = await world.launch()
        await world.wait(run["id"])
        ids.append(run["id"])
    first = (await world.client.get(world.url("/runs?limit=2"), headers=world.bob.headers)).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = (
        await world.client.get(world.url(f"/runs?limit=2&cursor={first['next_cursor']}"), headers=world.bob.headers)
    ).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert {r["id"] for r in first["items"] + second["items"]} == set(ids)


async def test_stop_matches_the_contract(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    run = await world.launch()
    await world.wait_for_event(run["id"], "tool_call_started")
    r = await world.client.post(world.url(f"/runs/{run['id']}/stop"), headers=world.carol.headers)
    assert r.status_code == 202
    assert_matches_contract(r)
    assert (await world.wait(run["id"], timeout=5))["status"] == "stopped"


async def test_stop_of_a_finished_run_is_idempotent(world: World) -> None:
    world.models.set(say("fini"))
    run = await world.launch()
    await world.wait(run["id"])
    r = await world.client.post(world.url(f"/runs/{run['id']}/stop"), headers=world.bob.headers)
    assert r.status_code == 202
    assert (await world.run(run["id"]))["status"] == "succeeded"


async def test_unknown_agent_is_404_and_empty_task_is_422(world: World) -> None:
    import uuid

    r = await world.client.post(
        world.url("/runs"), json={"agent_id": str(uuid.uuid4()), "task": "x"}, headers=world.bob.headers
    )
    assert r.status_code == 404
    r = await world.client.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": ""}, headers=world.bob.headers)
    assert r.status_code == 422


async def test_run_snapshot_is_frozen_at_launch(world: World) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    run = await world.launch()
    await world.wait_for_event(run["id"], "tool_call_started")
    await world.client.put(
        world.url(f"/agents/{world.agent_id}"),
        json={
            "name": "renommé",
            "instructions": "Nouvelles instructions",
            "model_id": "fake",
            "allowed_tool_ids": [world.ops["lookup"]],
            "limits": {"max_iterations": 3, "max_duration_seconds": 10, "max_tokens": 100},
        },
        headers=world.alice.headers,
    )
    started = (await world.events(run["id"]))[0]["payload"]
    assert "Tu es un agent de test." in started["system_prompt"]
    assert started["limits"]["max_iterations"] == 10
    await world.client.post(world.url(f"/runs/{run['id']}/stop"), headers=world.bob.headers)
