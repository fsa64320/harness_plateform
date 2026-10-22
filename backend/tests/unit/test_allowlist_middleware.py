"""Liste blanche d'outils : refus consigné d'un outil non autorisé ou inconnu (FR-006, SC-004)."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from harness.runtime.middleware.allowlist import AllowlistMiddleware
from harness.runtime.models import say, tool_call
from tests.support import make_rig, make_snapshot


def _request(name: str, tool: Any) -> ToolCallRequest:
    call = {"name": name, "args": {}, "id": f"call_{uuid.uuid4().hex[:6]}", "type": "tool_call"}
    return ToolCallRequest(tool_call=call, tool=tool, state={}, runtime=None)  # type: ignore[arg-type]


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: "ok", name=name, description=name)


async def _handler(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(content="exécuté", tool_call_id=request.tool_call["id"], name=request.tool_call["name"])


async def test_allowed_internal_tool_is_executed() -> None:
    rig = make_rig([], make_snapshot(allowed=["lookup"]))
    mw = rig.middleware(AllowlistMiddleware)
    result = await mw.awrap_tool_call(_request("read_file", _tool("read_file")), _handler)
    assert isinstance(result, ToolMessage) and result.content == "exécuté"
    assert rig.store.of("tool_call_refused") == []


async def test_tool_outside_the_allowlist_is_refused_and_traced() -> None:
    rig = make_rig([], make_snapshot(allowed=["lookup"]))
    mw = rig.middleware(AllowlistMiddleware)
    # `execute` n'est pas dans la liste des outils internes : un outil du framework hors liste.
    result = await mw.awrap_tool_call(_request("execute", _tool("execute")), _handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error" and "exécuté" not in str(result.content)
    event = rig.store.of("tool_call_refused")[0]
    assert event.payload["reason"] == "not_allowed"
    assert event.payload["tool"] == "execute"


async def test_unknown_tool_name_is_refused_and_traced() -> None:
    rig = make_rig([], make_snapshot(allowed=["lookup"]))
    mw = rig.middleware(AllowlistMiddleware)
    result = await mw.awrap_tool_call(_request("outil_invente", None), _handler)
    assert result.status == "error"  # type: ignore[union-attr]
    event = rig.store.of("tool_call_refused")[0]
    assert event.payload["reason"] == "unknown_tool"


async def test_catalog_tool_not_allowed_for_the_agent_is_never_executed_in_a_run() -> None:
    rig = make_rig(
        [tool_call("crm_send_message", {"query": "salut"}), say("tant pis")],
        make_snapshot(allowed=["lookup"]),
    )
    await rig.run()
    assert rig.calls == []  # le serveur MCP n'a jamais été appelé
    refused = rig.store.of("tool_call_refused")
    assert [e.payload["tool"] for e in refused] == ["crm_send_message"]
    assert refused[0].payload["reason"] in {"not_allowed", "unknown_tool"}
    assert rig.store.of("tool_call_started") == []


async def test_task_tool_is_refused_when_subagents_are_disabled() -> None:
    rig = make_rig(
        [tool_call("task", {"description": "x", "subagent_type": "general-purpose"}), say("ok")],
        make_snapshot(subagents=False),
    )
    await rig.run()
    assert [e.payload["tool"] for e in rig.store.of("tool_call_refused")] == ["task"]
