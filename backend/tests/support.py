"""Aides de test : faux outils MCP, magasin de trace en mémoire, assemblage d'agent sans base."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from harness.catalog.hooks import Hook, HookRunner
from harness.runtime.builder import AgentConfig, build_agent, build_agent_config, make_run_context
from harness.runtime.context import RunContext
from harness.runtime.models import FakeProvider, Step
from harness.traces.bus import TraceBus
from harness.traces.memory import MemoryTraceStore
from harness.traces.writer import RunEventSink, TraceWriter


def make_snapshot(
    *,
    allowed: Sequence[str] = ("lookup",),
    operations: dict[str, str] | None = None,
    subagents: bool = False,
    max_iterations: int = 10,
    max_duration_seconds: int = 60,
    max_tokens: int = 100_000,
    instructions: str = "Tu es un agent de test.",
) -> dict[str, Any]:
    """Configuration figée d'un agent avec un serveur MCP `crm` (opérations classées).

    `allowed` liste les opérations autorisées (leur nom, sans le préfixe du serveur)."""
    operations = operations or {
        "lookup": "read_only",
        "send_message": "unclassified",
        "wipe": "external_effect",
    }
    ops = [
        {"id": str(uuid.uuid4()), "name": name, "classification": classification}
        for name, classification in operations.items()
    ]
    tool = {"id": str(uuid.uuid4()), "kind": "mcp_server", "name": "crm", "operations": ops}
    allowed_ids = [o["id"] for o in ops if o["name"] in allowed]
    return {
        "agent_id": str(uuid.uuid4()),
        "name": "agent-test",
        "instructions": instructions,
        "model_id": "fake",
        "allowed_tool_ids": allowed_ids,
        "tools": [tool],
        "agent_memory_enabled": False,
        "group_memory_enabled": False,
        "subagents_enabled": subagents,
        "limits": {
            "max_iterations": max_iterations,
            "max_duration_seconds": max_duration_seconds,
            "max_tokens": max_tokens,
        },
    }


def fake_mcp_tools(calls: list[tuple[str, dict[str, Any]]], results: dict[str, Any] | None = None) -> list[BaseTool]:
    """Les outils d'un serveur MCP `crm`, tels que `langchain-mcp-adapters` les nomme (préfixe)."""
    results = results or {}

    def make(name: str) -> BaseTool:
        async def run(**kwargs: Any) -> str:
            calls.append((name, kwargs))
            outcome = results.get(name, f"résultat de {name}")
            if isinstance(outcome, Exception):
                raise outcome
            return str(outcome)

        return StructuredTool.from_function(
            coroutine=run,
            name=f"crm_{name}",
            description=f"Opération {name}",
            args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )

    return [make("lookup"), make("send_message"), make("wipe")]


@dataclass
class Rig:
    graph: Any
    ctx: RunContext
    store: MemoryTraceStore
    provider: FakeProvider
    checkpointer: InMemorySaver
    config: AgentConfig
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def middleware(self, cls: type[Any]) -> Any:
        """Instance de middleware du type donné (partage le contexte et la trace du graphe)."""
        return next(m for m in self.config.middleware if isinstance(m, cls))

    async def run(self, task: str = "go", thread_id: str = "t1") -> dict[str, Any]:
        return await self.graph.ainvoke(
            {"messages": [HumanMessage(task)]},
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 60},
        )

    async def resume(self, decisions: list[dict[str, Any]], thread_id: str = "t1") -> dict[str, Any]:
        from langgraph.types import Command

        return await self.graph.ainvoke(
            Command(resume={"decisions": decisions}),
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 60},
        )

    @property
    def model(self) -> Any:
        return self.provider.models[-1] if self.provider.models else self.provider._shared


def make_rig(
    script: Iterable[Step],
    snapshot: dict[str, Any] | None = None,
    *,
    tool_results: dict[str, Any] | None = None,
    known_secrets: Sequence[str] = (),
    hooks: Sequence[Hook] = (),
    webhook_http: httpx.AsyncClient | None = None,
    webhook_secret: str = "signing-secret",  # noqa: S107
) -> Rig:
    snapshot = snapshot or make_snapshot()
    provider = FakeProvider(list(script))
    store = MemoryTraceStore()
    writer = TraceWriter(store, TraceBus())
    run_id, group_id = uuid.uuid4(), uuid.uuid4()
    sink = RunEventSink(writer, group_id=group_id, run_id=run_id, known_secrets=known_secrets)
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = fake_mcp_tools(calls, tool_results)
    ctx = make_run_context(snapshot, run_id=run_id, group_id=group_id, sink=sink)
    checkpointer = InMemorySaver()
    hook_runner = HookRunner(
        list(hooks), group_id, run_id, secrets={h.id: webhook_secret for h in hooks}, http=webhook_http
    )
    graph = build_agent(
        snapshot,
        provider,
        tools,
        checkpointer=checkpointer,
        backend_factory=None,
        ctx=ctx,
        hook_runner=hook_runner,
    )
    config = build_agent_config(snapshot, tools, ctx=ctx, hook_runner=hook_runner)
    return Rig(graph, ctx, store, provider, checkpointer, config, calls)


def iso(value: datetime) -> str:
    return value.isoformat()
