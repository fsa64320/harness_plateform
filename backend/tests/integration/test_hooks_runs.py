"""Hooks dans de vraies exécutions : règle, webhook muet, début d'exécution (US2-3, FR-012, FR-013)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from harness.runtime.models import say, tool_call
from tests.fixtures.silent_https_server import WebhookTestServer
from tests.world_fixtures import World

SEND = tool_call("crm_send_message", {"text": "salut"})


async def _hook(world: World, name: str, **hook: Any) -> dict[str, Any]:
    r = await world.client.post(
        world.url("/tools"), json={"kind": "hook", "name": name, "hook": hook}, headers=world.alice.headers
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def webhook_server(monkeypatch: pytest.MonkeyPatch, mcp_server: Any, tmp_path: Path) -> Iterator[Any]:
    servers: list[WebhookTestServer] = []

    def make(**kwargs: Any) -> WebhookTestServer:
        server = WebhookTestServer(**kwargs).start()
        servers.append(server)
        # un seul SSL_CERT_FILE : on fait confiance aux certificats du serveur MCP et du webhook
        bundle = tmp_path / "ca-bundle.pem"
        bundle.write_text(mcp_server.cert_path.read_text() + server.cert_path.read_text())
        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        return server

    yield make
    for s in servers:
        s.close()


async def test_a_rule_hook_blocks_external_effect_calls_without_asking_an_approver(world: World) -> None:
    hook = await _hook(
        world, "bloque-effets-externes", events=["before_tool_call"], hook_type="rule",
        condition={"classification": "external_effect"}, decision="block",
    )
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"], world.ops["send_message"], hook["id"]])
    world.models.set(SEND, say("compris, je n'envoie rien"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])

    assert done["status"] == "succeeded"  # jamais « en attente d'approbation » : le hook a tranché
    assert not [c for c in world.mcp.calls if c[0] == "send_message"]
    assert await world.pending_approvals() == []
    events = await world.events(run["id"])
    [evaluated] = [e["payload"] for e in events if e["type"] == "hook_evaluated"]
    assert evaluated["hook_id"] == hook["id"] and evaluated["decision"] == "block" and evaluated["event"] == "before_tool_call"
    assert "approval_requested" not in [e["type"] for e in events]
    decided = [e["payload"] for e in events if e["type"] == "approval_decided"]
    assert decided and decided[0]["decision"] == "reject" and decided[0]["decided_by"] is None


async def test_hook_only_applies_to_agents_that_allow_it(world: World) -> None:
    hook = await _hook(
        world, "bloque-tout", events=["before_tool_call"], hook_type="rule", condition={}, decision="block"
    )
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("ok"))
    run = await world.launch()  # l'agent de base n'a pas ce hook
    await world.wait(run["id"])
    assert ("lookup", {"query": "x"}) in world.mcp.calls
    assert "hook_evaluated" not in await world.event_types(run["id"])
    assert hook["id"] not in str((await world.events(run["id"]))[0]["payload"])


async def test_a_silent_webhook_blocks_after_its_timeout_and_traces_the_cause(
    world: World, webhook_server: Any
) -> None:
    server = webhook_server(delay=30)  # ne répond jamais dans le délai
    hook = await _hook(
        world, "validation-externe", events=["before_tool_call"], hook_type="webhook", url=server.url,
        signing_secret="whsec-test", timeout_seconds=1,
    )
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"], hook["id"]])
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("fini"))

    started = time.monotonic()
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert time.monotonic() - started < 8
    assert done["status"] == "succeeded"
    assert world.mcp.calls == []  # l'appel est bloqué
    [evaluated] = [e["payload"] for e in await world.events(run["id"]) if e["type"] == "hook_evaluated"]
    assert evaluated["decision"] == "block" and evaluated["hook_type"] == "webhook" and "timeout" in evaluated["reason"]
    assert len(server.requests) == 1  # appelé une seule fois : pas de nouvelle tentative


async def test_a_responsive_webhook_can_allow_and_receives_a_signed_minimal_request(
    world: World, webhook_server: Any
) -> None:
    server = webhook_server(decision="allow")
    hook = await _hook(
        world, "validation-externe", events=["before_tool_call"], hook_type="webhook", url=server.url,
        signing_secret="whsec-test",
    )
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"], hook["id"]])
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("fini"))
    run = await world.launch(agent_id=agent["id"])
    await world.wait(run["id"])

    assert ("lookup", {"query": "x"}) in world.mcp.calls
    [request] = server.requests
    assert request["headers"]["x-harness-event"] == "before_tool_call"
    assert request["headers"]["x-harness-signature"].startswith("sha256=")
    assert "whsec-test" not in request["body"]
    evaluated = [e["payload"] for e in await world.events(run["id"]) if e["type"] == "hook_evaluated"]
    assert [e["decision"] for e in evaluated] == ["allow"]


async def test_a_run_start_hook_can_block_the_run(world: World) -> None:
    hook = await _hook(world, "pas-aujourdhui", events=["run_start"], hook_type="rule", condition={}, decision="block")
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"], hook["id"]])
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("fini"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "failed" and done["stop_reason"] == "blocked_by_hook"
    assert world.mcp.calls == []
    types = await world.event_types(run["id"])
    assert "hook_evaluated" in types and "model_step" not in types and types[-1] == "run_ended"


async def test_run_end_hook_is_evaluated_once_even_when_the_run_fails(world: World) -> None:
    hook = await _hook(world, "journal-fin", events=["run_end"], hook_type="rule", condition={}, decision="log")
    agent = await world.create_agent(allowed_tool_ids=[world.ops["lookup"], hook["id"]])
    world.models.set()  # script vide : l'exécution échoue
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "failed"
    events = await world.events(run["id"])
    evaluated = [e for e in events if e["type"] == "hook_evaluated"]
    assert len(evaluated) == 1 and evaluated[0]["payload"]["event"] == "run_end"
    assert events[-1]["type"] == "run_ended"  # `run_ended` reste le dernier événement
