"""Cycle de vie d'une exécution : succès, arrêt, limites, refus, panne MCP (US1, FR-002 à FR-007)."""

from __future__ import annotations

import time

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def test_run_succeeds_with_a_complete_trace(world: World) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "acme"}), say("Voici le résultat."))
    run = await world.launch(task="cherche acme")
    assert run["status"] in {"queued", "running"} and run["launch_mode"] == "manual"

    done = await world.wait(run["id"])
    assert done["status"] == "succeeded" and done["stop_reason"] is None
    assert done["result"] == "Voici le résultat."
    assert done["iterations"] == 2 and done["input_tokens"] > 0 and done["ended_at"] is not None

    events = await world.events(run["id"])
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "run_ended"
    assert {"model_step", "tool_call_started", "tool_call_finished"} <= set(types)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    started = events[0]["payload"]
    assert started["task"] == "cherche acme"
    assert started["triggered_by_type"] == "person" and started["launch_mode"] == "manual"
    assert "Tu es un agent de test." in started["system_prompt"]
    assert started["internal_tools"] == ["ls", "read_file", "glob", "grep", "write_file", "edit_file", "delete"]
    assert started["limits"]["max_iterations"] == 10
    ended = events[-1]["payload"]
    assert ended["status"] == "succeeded" and ended["iterations"] == 2 and ended["result"] == "Voici le résultat."
    assert ("lookup", {"query": "acme"}) in world.mcp.calls


async def test_stop_request_stops_a_running_agent_within_five_seconds(world: World) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais atteint"))
    run = await world.launch()
    await world.wait_for_event(run["id"], "tool_call_started")

    t0 = time.monotonic()
    r = await world.client.post(world.url(f"/runs/{run['id']}/stop"), headers=world.bob.headers)
    assert r.status_code == 202
    done = await world.wait(run["id"], timeout=5)
    assert time.monotonic() - t0 < 5
    assert done["status"] == "stopped" and done["stop_reason"] == "manual"
    ended = (await world.events(run["id"]))[-1]
    assert ended["type"] == "run_ended" and ended["payload"]["stop_reason"] == "manual"


async def test_max_iterations_ends_the_run_with_limit_reached(world: World) -> None:
    agent = await world.create_agent(limits={"max_iterations": 1, "max_duration_seconds": 60, "max_tokens": 100000})
    world.models.set(tool_call("crm_lookup", {"query": "a"}), tool_call("crm_lookup", {"query": "b"}), say("fin"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "limit_reached" and done["stop_reason"] == "iterations"
    types = await world.event_types(run["id"])
    assert "limit_reached" in types and types[-1] == "run_ended"


async def test_tool_outside_the_allowlist_is_refused_and_traced(world: World) -> None:
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"]])
    world.models.set(tool_call("crm_send_message", {"text": "salut"}), say("compris"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"
    refused = [e for e in await world.events(run["id"]) if e["type"] == "tool_call_refused"]
    assert [e["payload"]["tool"] for e in refused] == ["crm_send_message"]
    assert refused[0]["payload"]["reason"] in {"not_allowed", "unknown_tool"}
    assert not [c for c in world.mcp.calls if c[0] == "send_message"]


async def test_mcp_server_going_down_mid_run_is_an_explicit_error_and_does_not_hang(world: World) -> None:
    world.models.set(
        tool_call("crm_lookup", {"query": "avant la panne"}),
        tool_call("crm_lookup", {"query": "pendant la panne"}, delay=1.5),
        say("j'ai continué sans l'outil"),
    )
    run = await world.launch()
    await world.wait_for_event(run["id"], "tool_call_finished")
    world.mcp.stop()

    done = await world.wait(run["id"], timeout=20)
    assert done["status"] == "succeeded"
    finished = [e["payload"] for e in await world.events(run["id"]) if e["type"] == "tool_call_finished"]
    assert [f["status"] for f in finished] == ["ok", "error"]
    assert finished[1]["error"]


async def test_runner_failure_is_reported_as_failed_with_an_explicit_message(world: World) -> None:
    world.models.set()  # script vide : le faux modèle échoue dès le premier appel
    run = await world.launch()
    done = await world.wait(run["id"])
    assert done["status"] == "failed"
    ended = (await world.events(run["id"]))[-1]
    assert ended["type"] == "run_ended" and ended["payload"]["status"] == "failed"
    assert "script" in (ended["payload"]["result"] or "")
