"""Limites d'exécution : itérations, durée, tokens (FR-003, constitution V)."""

from __future__ import annotations

from harness.runtime.models import say, tool_call
from tests.support import make_rig, make_snapshot


def _loop(n: int, **kwargs: int) -> list:
    return [tool_call("crm_lookup", {"query": str(i)}, **kwargs) for i in range(n)] + [say("fini")]


async def test_max_iterations_stops_the_run_with_limit_reached() -> None:
    rig = make_rig(_loop(10), make_snapshot(max_iterations=2))
    result = await rig.run()
    assert rig.ctx.limit_hit == "iterations"
    event = rig.store.of("limit_reached")[0]
    assert event.payload == {"limit": "iterations", "value": 2, "max": 2}
    assert len(rig.calls) == 2  # pas de troisième itération
    assert result["messages"][-1].content != "fini"


async def test_max_tokens_stops_the_run_with_limit_reached() -> None:
    rig = make_rig(_loop(10, input_tokens=60, output_tokens=50), make_snapshot(max_tokens=100))
    await rig.run()
    assert rig.ctx.limit_hit == "tokens"
    event = rig.store.of("limit_reached")[0]
    assert event.payload["limit"] == "tokens"
    assert event.payload["value"] >= 110 and event.payload["max"] == 100
    assert len(rig.calls) == 1


async def test_max_duration_stops_the_run_with_limit_reached() -> None:
    script = [tool_call("crm_lookup", {"query": "a"}, delay=1.1), tool_call("crm_lookup", {"query": "b"}), say("fini")]
    rig = make_rig(script, make_snapshot(max_duration_seconds=1))
    await rig.run()
    assert rig.ctx.limit_hit == "duration"
    event = rig.store.of("limit_reached")[0]
    assert event.payload["limit"] == "duration" and event.payload["max"] == 1
    assert event.payload["value"] >= 1


async def test_counters_are_exposed_on_the_context() -> None:
    rig = make_rig([tool_call("crm_lookup", {"query": "a"}, input_tokens=7, output_tokens=3), say("ok", input_tokens=5, output_tokens=2)])
    await rig.run()
    assert rig.ctx.iterations == 2
    assert rig.ctx.input_tokens == 12 and rig.ctx.output_tokens == 5
    assert rig.ctx.limit_hit is None
    assert rig.store.of("limit_reached") == []


async def test_a_run_within_its_limits_ends_normally() -> None:
    rig = make_rig([say("direct")], make_snapshot(max_iterations=1))
    result = await rig.run()
    assert result["messages"][-1].content == "direct"
    assert rig.ctx.limit_hit is None
