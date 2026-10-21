"""Hooks autour de l'exécution et de chaque appel d'outil (FR-012, FR-013).

`run_start` et `before_tool_call` peuvent bloquer ; `after_tool_call` et `run_end` interviennent après
coup : un `block` y est consigné mais n'annule rien. Les sous-agents ne redéclenchent pas `run_start` /
`run_end` : un événement d'agent qui survient pendant un appel d'outil est celui d'un sous-agent."""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, ToolCallRequest, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from harness.catalog.hook_rules import HookEventData, Verdict
from harness.catalog.hooks import HookRunner
from harness.runtime.context import RunContext

_in_tool_call: contextvars.ContextVar[bool] = contextvars.ContextVar("harness_in_tool_call", default=False)
INFORMATIVE_ONLY = " (consigné seulement : l'action est déjà passée)"


class HooksMiddleware(AgentMiddleware):
    def __init__(self, ctx: RunContext, runner: HookRunner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner

    def _event(self, event: str, **fields: Any) -> HookEventData:
        return HookEventData(
            event=event,
            agent_id=self.ctx.agent_id,
            agent_name=self.ctx.agent_name,
            triggered_by_type=self.ctx.triggered_by_type,
            triggered_by_id=self.ctx.triggered_by_id,
            **fields,
        )

    async def _evaluate(self, data: HookEventData, *, can_block: bool) -> Verdict:
        verdict = await self.runner.evaluate(data)
        for evaluation in verdict.evaluations:
            informative = not can_block and evaluation.decision == "block"
            await self.ctx.sink.emit(
                "hook_evaluated",
                {
                    "hook_id": evaluation.hook_id,
                    "hook_type": evaluation.hook_type,
                    "event": evaluation.event,
                    "decision": evaluation.decision,
                    "reason": evaluation.reason + (INFORMATIVE_ONLY if informative else ""),
                    "duration_ms": evaluation.duration_ms,
                },
            )
        return verdict

    def _blocker(self, verdict: Verdict) -> tuple[str, str]:
        block = next(e for e in verdict.evaluations if e.decision == "block")
        name = next((h.name for h in self.runner.hooks if h.id == block.hook_id), block.hook_id)
        return name, block.reason

    # --- run_start / run_end ----------------------------------------------------------------

    async def abefore_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        if _in_tool_call.get() or not self.runner.applies_to("run_start"):
            return None
        verdict = await self._evaluate(self._event("run_start"), can_block=True)
        if verdict.decision == "block":
            name, _ = self._blocker(verdict)
            self.ctx.start_blocked_by = name
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        if self.ctx.start_blocked_by is None:
            return None
        notice = f"Exécution bloquée au démarrage par le hook « {self.ctx.start_blocked_by} »."
        return {"jump_to": "end", "messages": [AIMessage(content=notice)]}

    async def aafter_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        if _in_tool_call.get() or self.ctx.run_end_fired or not self.runner.applies_to("run_end"):
            return None
        self.ctx.run_end_fired = True
        status = "limit_reached" if self.ctx.limit_hit else "succeeded"
        await self._evaluate(self._event("run_end", status=status, stop_reason=self.ctx.limit_hit), can_block=False)
        return None

    # --- appels d'outils --------------------------------------------------------------------

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name = call["name"]
        fields = {
            "tool": name,
            "operation": self.ctx.operations.get(name, name),
            "classification": self.ctx.classification_of(name),
            "arguments": call.get("args", {}),
        }
        token = _in_tool_call.set(True)
        try:
            if self.runner.applies_to("before_tool_call"):
                verdict = await self._evaluate(self._event("before_tool_call", **fields), can_block=True)
                if verdict.decision == "block":
                    hook_name, reason = self._blocker(verdict)
                    return ToolMessage(
                        content=f"Appel bloqué par le hook « {hook_name} » : {reason}",
                        tool_call_id=call.get("id") or "",
                        name=name,
                        status="error",
                    )
            result = await handler(request)
            if self.runner.applies_to("after_tool_call"):
                await self._evaluate(self._event("after_tool_call", **fields), can_block=False)
            return result
        finally:
            _in_tool_call.reset(token)
