"""Retrait d'un outil du catalogue : les agents sont signalés et s'exécutent sans lui (US2-4)."""

from __future__ import annotations

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def _agent_for(world: World, *ids: str) -> dict:
    return await world.create_agent(allowed_tool_ids=list(ids))


async def test_retiring_a_tool_flags_the_agents_that_use_it_and_only_them(world: World) -> None:
    uses = await _agent_for(world, world.tool_id)  # le serveur entier
    uses_op = await _agent_for(world, world.ops["lookup"])  # une seule de ses opérations
    unrelated = await _agent_for(world)
    assert uses["config_changed_at"] is None

    r = await world.client.delete(world.url(f"/tools/{world.tool_id}"), headers=world.alice.headers)
    assert r.status_code == 204

    async def changed(agent: dict) -> str | None:
        got = await world.client.get(world.url(f"/agents/{agent['id']}"), headers=world.bob.headers)
        return got.json()["config_changed_at"]

    assert await changed(uses) is not None
    assert await changed(uses_op) is not None
    assert await changed(unrelated) is None


async def test_next_run_executes_without_the_tool_and_emits_a_config_warning(world: World) -> None:
    agent = await _agent_for(world, world.ops["lookup"])
    await world.client.delete(world.url(f"/tools/{world.tool_id}"), headers=world.alice.headers)

    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("je continue sans"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"

    events = await world.events(run["id"])
    warnings = [e["payload"]["message"] for e in events if e["type"] == "config_warning"]
    assert len(warnings) == 1 and "crm" in warnings[0] and "retiré" in warnings[0]
    assert world.mcp.calls == []  # l'outil retiré n'a jamais été appelé
    assert [e["payload"]["tool"] for e in events if e["type"] == "tool_call_refused"] == ["crm_lookup"]
    assert events.index(next(e for e in events if e["type"] == "config_warning")) < events.index(
        next(e for e in events if e["type"] == "model_step")
    )


async def test_saving_the_agent_again_clears_the_warning(world: World) -> None:
    agent = await _agent_for(world, world.tool_id, world.ops["wait"])
    await world.client.delete(world.url(f"/tools/{world.tool_id}"), headers=world.alice.headers)
    body = {k: agent[k] for k in ("name", "instructions", "model_id", "limits")}
    r = await world.client.put(
        world.url(f"/agents/{agent['id']}"), json={**body, "allowed_tool_ids": []}, headers=world.alice.headers
    )
    assert r.status_code == 200 and r.json()["config_changed_at"] is None


async def test_a_retired_tool_cannot_be_given_to_an_agent(world: World) -> None:
    await world.client.delete(world.url(f"/tools/{world.tool_id}"), headers=world.alice.headers)
    r = await world.client.post(
        world.url("/agents"),
        json={
            "name": "x", "instructions": "x", "model_id": "fake", "allowed_tool_ids": [world.ops["lookup"]],
            "limits": {"max_iterations": 3, "max_duration_seconds": 10, "max_tokens": 100},
        },
        headers=world.bob.headers,
    )
    assert r.status_code == 422
