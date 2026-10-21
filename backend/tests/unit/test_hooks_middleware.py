"""Hooks pendant l'exécution : début, fin, avant et après chaque appel d'outil (FR-012, FR-013)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from harness.catalog.hooks import Hook
from harness.runtime.models import say, tool_call
from tests.support import make_rig, make_snapshot


def rule(decision: str, *, events: tuple[str, ...] = ("before_tool_call",), condition: dict | None = None, priority: int = 100) -> Hook:
    return Hook(
        id=str(uuid.uuid4()), name=f"hook-{decision}", events=list(events), hook_type="rule",
        condition=condition or {}, decision=decision, priority=priority,
    )


def lookup_then_done() -> list:
    return [tool_call("crm_lookup", {"query": "x"}), say("fini")]


def evaluated(rig: Any) -> list[dict[str, Any]]:
    return [e.payload for e in rig.store.of("hook_evaluated")]


async def test_hooks_fire_on_the_four_events_in_order() -> None:
    hooks = [rule("log", events=("run_start", "run_end", "before_tool_call", "after_tool_call"))]
    rig = make_rig(lookup_then_done(), hooks=hooks)
    await rig.run()
    assert [p["event"] for p in evaluated(rig)] == ["run_start", "before_tool_call", "after_tool_call", "run_end"]
    assert all(p["hook_id"] == hooks[0].id and p["hook_type"] == "rule" and p["decision"] == "log" for p in evaluated(rig))
    assert all("duration_ms" in p and "reason" in p for p in evaluated(rig))


async def test_block_before_a_call_prevents_it_and_names_the_hook() -> None:
    hook = rule("block", condition={"tool": "crm_lookup"})
    rig = make_rig(lookup_then_done(), hooks=[hook])
    result = await rig.run()
    assert rig.calls == []  # le serveur MCP n'a jamais été appelé
    [payload] = evaluated(rig)
    assert payload["decision"] == "block" and payload["hook_id"] == hook.id and payload["event"] == "before_tool_call"
    finished = rig.store.of("tool_call_finished")[0].payload
    assert finished["status"] == "error" and hook.name in finished["error"]
    assert result["messages"][-1].content == "fini"  # le modèle a vu le blocage et poursuivi


async def test_hook_that_does_not_match_lets_the_call_through() -> None:
    rig = make_rig(lookup_then_done(), hooks=[rule("block", condition={"tool": "crm_wipe"})])
    await rig.run()
    assert rig.calls == [("lookup", {"query": "x"})] and evaluated(rig) == []


async def test_block_on_classification_blocks_external_effect_and_unclassified_only() -> None:
    hook = rule("block", condition={"classification": "external_effect"})
    snap = make_snapshot(allowed=["lookup", "send_message"])
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), say("fini")], snap, hooks=[hook])
    await rig.run()
    assert rig.calls == [("lookup", {"query": "x"})]  # read_only passe


async def test_block_on_after_tool_call_and_run_end_is_only_logged() -> None:
    hooks = [rule("block", events=("after_tool_call", "run_end"))]
    rig = make_rig(lookup_then_done(), hooks=hooks)
    result = await rig.run()
    assert rig.calls == [("lookup", {"query": "x"})]  # l'appel passé n'est pas annulé
    assert result["messages"][-1].content == "fini"
    after, end = evaluated(rig)
    assert after["decision"] == "block" and "consigné" in after["reason"]
    assert end["decision"] == "block" and "consigné" in end["reason"]
    assert rig.store.of("tool_call_finished")[0].payload["status"] == "ok"


async def test_block_on_run_start_ends_the_run_before_any_model_call() -> None:
    rig = make_rig(lookup_then_done(), hooks=[rule("block", events=("run_start",))])
    await rig.run()
    assert rig.ctx.start_blocked_by is not None
    assert rig.store.of("model_step") == []


async def test_a_webhook_hook_decides_through_the_http_client() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"decision": "block", "reason": "interdit par la politique"})

    webhook = Hook(
        id=str(uuid.uuid4()), name="externe", events=["before_tool_call"], hook_type="webhook",
        url="https://hooks.example.test/d", timeout_seconds=10, priority=1,
    )
    rig = make_rig(lookup_then_done(), hooks=[webhook], webhook_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await rig.run()
    assert len(seen) == 1 and rig.calls == []
    [payload] = evaluated(rig)
    assert payload["hook_type"] == "webhook" and payload["decision"] == "block" and "politique" in payload["reason"]


async def test_a_failing_webhook_blocks_by_default_and_traces_the_cause() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injoignable")

    webhook = Hook(
        id=str(uuid.uuid4()), name="externe", events=["before_tool_call"], hook_type="webhook",
        url="https://hooks.example.test/d", timeout_seconds=10, priority=1,
    )
    rig = make_rig(lookup_then_done(), hooks=[webhook], webhook_http=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    await rig.run()
    assert rig.calls == []
    [payload] = evaluated(rig)
    assert payload["decision"] == "block" and "network_error" in payload["reason"]
