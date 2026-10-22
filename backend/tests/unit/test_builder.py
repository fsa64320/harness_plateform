"""Construction d'un agent Deep Agents à partir de sa configuration figée (FR-005a, FR-006, FR-006a)."""

from __future__ import annotations

from harness.runtime.builder import (
    INTERNAL_TOOLS,
    build_agent_config,
    compute_interrupt_on,
    resolve_allowed_operations,
)
from harness.runtime.models import say, tool_call
from tests.support import fake_mcp_tools, make_rig, make_snapshot

APPROVE_REJECT = {"allowed_decisions": ["approve", "reject"]}
EXPECTED_INTERNAL = ["ls", "read_file", "glob", "grep", "write_file", "edit_file", "delete"]


def test_internal_tools_are_exactly_the_fr_006a_list() -> None:
    assert list(INTERNAL_TOOLS) == EXPECTED_INTERNAL


def test_resolve_allowed_operations_supports_server_ids_and_operation_ids() -> None:
    snap = make_snapshot(allowed=["lookup"])
    assert [o.bound_name for o in resolve_allowed_operations(snap)] == ["crm_lookup"]

    server_id = snap["tools"][0]["id"]
    snap["allowed_tool_ids"] = [server_id]  # le serveur entier : toutes ses opérations
    names = {o.bound_name for o in resolve_allowed_operations(snap)}
    assert names == {"crm_lookup", "crm_send_message", "crm_wipe"}


def test_interrupt_on_gates_external_effect_and_unclassified_but_not_read_only() -> None:
    snap = make_snapshot(allowed=["lookup", "send_message", "wipe"])
    interrupt_on = compute_interrupt_on(resolve_allowed_operations(snap))
    assert interrupt_on == {"crm_send_message": APPROVE_REJECT, "crm_wipe": APPROVE_REJECT}


async def test_only_allowed_tools_are_bound_to_the_model() -> None:
    rig = make_rig([say("fini")], make_snapshot(allowed=["lookup"]))
    await rig.run()
    bound = rig.model.bound_tool_names[0]
    assert "crm_lookup" in bound
    assert "crm_send_message" not in bound and "crm_wipe" not in bound
    assert set(bound) == {*EXPECTED_INTERNAL, "crm_lookup"}


async def test_task_tool_is_bound_only_when_subagents_enabled() -> None:
    off = make_rig([say("ok")], make_snapshot(subagents=False))
    await off.run()
    assert "task" not in off.model.bound_tool_names[0]

    on = make_rig([say("ok")], make_snapshot(subagents=True))
    await on.run()
    assert "task" in on.model.bound_tool_names[0]


async def test_no_other_framework_tool_is_bound() -> None:
    rig = make_rig([say("ok")], make_snapshot(subagents=True))
    await rig.run()
    bound = set(rig.model.bound_tool_names[0])
    assert bound <= {*EXPECTED_INTERNAL, "task", "crm_lookup"}
    assert "execute" not in bound and "write_todos" not in bound


def test_effective_internal_tools_are_recorded_in_the_context() -> None:
    off = make_rig([], make_snapshot(subagents=False))
    assert list(off.ctx.internal_tools) == EXPECTED_INTERNAL
    on = make_rig([], make_snapshot(subagents=True))
    assert list(on.ctx.internal_tools) == [*EXPECTED_INTERNAL, "task"]


def test_subagents_inherit_tools_middleware_and_interrupt_on() -> None:
    snap = make_snapshot(allowed=["lookup", "send_message"], subagents=True)
    calls: list = []
    config = build_agent_config(snap, fake_mcp_tools(calls), ctx=make_rig([], snap).ctx)
    assert len(config.subagents) == 1
    sub = config.subagents[0]
    assert sub["tools"] == config.tools
    assert [type(m) for m in sub["middleware"]] == [type(m) for m in config.middleware]
    assert all(a is b for a, b in zip(sub["middleware"], config.middleware, strict=True))
    assert sub["interrupt_on"] == config.interrupt_on == {"crm_send_message": APPROVE_REJECT}


def test_no_subagent_spec_when_disabled() -> None:
    snap = make_snapshot(subagents=False)
    config = build_agent_config(snap, fake_mcp_tools([]), ctx=make_rig([], snap).ctx)
    assert config.subagents == []


def test_catalog_tools_outside_the_allowlist_are_not_passed_to_the_framework() -> None:
    snap = make_snapshot(allowed=["lookup"])
    config = build_agent_config(snap, fake_mcp_tools([]), ctx=make_rig([], snap).ctx)
    assert [t.name for t in config.tools] == ["crm_lookup"]


def test_system_prompt_contains_instructions_and_untrusted_data_rule() -> None:
    snap = make_snapshot(instructions="Réponds en français.")
    config = build_agent_config(snap, [], ctx=make_rig([], snap).ctx)
    assert "Réponds en français." in config.system_prompt
    assert "untrusted" in config.system_prompt.lower()


async def test_agent_calls_an_allowed_read_only_tool_without_approval() -> None:
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), say("trouvé")], make_snapshot(allowed=["lookup"]))
    result = await rig.run()
    assert rig.calls == [("lookup", {"query": "x"})]
    assert result["messages"][-1].content == "trouvé"
    assert "__interrupt__" not in result
