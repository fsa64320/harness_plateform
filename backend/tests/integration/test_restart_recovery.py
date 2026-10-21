"""Redémarrage de la plateforme (SC-011, FR-003a, FR-003b)."""

from __future__ import annotations

import uuid
from typing import Any

from harness.main import attach_services
from harness.runtime.models import say, tool_call
from tests.world_fixtures import World

SEND = tool_call("crm_send_message", {"text": "salut"})


async def _restart(world: World, checkpointer: Any) -> None:
    """Mort brutale du processus (aucune écriture de fin), puis démarrage d'un nouveau runner."""
    await world.runner.abandon_all()
    attach_services(world.app, provider=world.models.provider, checkpointer=checkpointer)
    await world.runner.recover_on_startup()


async def test_running_runs_become_interrupted_and_awaiting_approval_runs_resume(
    world: World, checkpointer: Any
) -> None:
    running = []
    for _ in range(3):
        world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
        run = await world.launch(world.bob)
        await world.wait_for_event(run["id"], "tool_call_started")
        running.append(run)

    world.models.set(SEND, say("envoyé après redémarrage"))
    waiting = await world.launch(world.bob)
    await world.wait_for_event(waiting["id"], "approval_requested")

    await _restart(world, checkpointer)

    for run in running:  # 100 % des exécutions en cours sont interrompues, trace conservée
        done = await world.run(run["id"])
        assert done["status"] == "interrupted" and done["stop_reason"] == "platform_restart"
        events = await world.events(run["id"])
        assert events[-1]["type"] == "run_ended"
        assert events[-1]["payload"]["status"] == "interrupted"
        assert events[-1]["payload"]["stop_reason"] == "platform_restart"
        assert "tool_call_started" in [e["type"] for e in events]  # la trace avant la coupure est là

    assert (await world.run(waiting["id"]))["status"] == "awaiting_approval"
    pending = await world.pending_approvals()
    assert [a["run_id"] for a in pending] == [waiting["id"]]

    r = await world.client.post(
        world.url(f"/approvals/{pending[0]['id']}/decision"),
        json={"decision": "approve"},
        headers=world.bob.headers,
    )
    assert r.status_code == 200
    done = await world.wait(waiting["id"])
    assert done["status"] == "succeeded" and done["result"] == "envoyé après redémarrage"
    assert ("send_message", {"text": "salut"}) in world.mcp.calls


async def test_an_interrupted_run_never_resumes_by_itself(world: World, checkpointer: Any) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    run = await world.launch(world.bob)
    await world.wait_for_event(run["id"], "tool_call_started")
    await _restart(world, checkpointer)
    await world.runner.recover_on_startup()  # idempotent

    assert (await world.run(run["id"]))["status"] == "interrupted"
    assert not world.runner.is_active(uuid.UUID(run["id"]))


async def test_relaunch_creates_a_new_linked_run_and_keeps_the_old_trace(world: World, checkpointer: Any) -> None:
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    old = await world.launch(world.bob, task="tâche d'origine")
    await world.wait_for_event(old["id"], "tool_call_started")
    await _restart(world, checkpointer)

    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("repris de zéro"))
    new = await world.launch(world.bob, task="tâche d'origine", relaunch_of=old["id"])
    assert new["id"] != old["id"] and new["relaunched_from"] == old["id"]
    done = await world.wait(new["id"])
    assert done["status"] == "succeeded"

    started = (await world.events(new["id"]))[0]["payload"]
    assert started["relaunched_from"] == old["id"]
    assert "tool_call_started" in await world.event_types(old["id"])  # trace de l'ancienne conservée


async def test_only_an_interrupted_run_can_be_relaunched(world: World) -> None:
    world.models.set(say("fini"))
    run = await world.launch(world.bob)
    await world.wait(run["id"])
    r = await world.client.post(
        world.url("/runs"),
        json={"agent_id": world.agent_id, "task": "x", "relaunch_of": run["id"]},
        headers=world.bob.headers,
    )
    assert r.status_code == 422
