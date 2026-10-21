"""Budget d'exécution : itérations, durée et tokens ; le dépassement arrête l'exécution proprement
(FR-003, constitution V)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from harness.runtime.context import RunContext

_LABELS = {"iterations": "d'itérations", "duration": "de durée", "tokens": "de tokens"}


def _ai_messages(response: Any) -> list[AIMessage]:
    if isinstance(response, AIMessage):
        return [response]
    result = getattr(response, "result", None)
    if result is None and hasattr(response, "model_response"):
        result = response.model_response.result
    return [m for m in (result or []) if isinstance(m, AIMessage)]


class BudgetMiddleware(AgentMiddleware):
    def __init__(self, ctx: RunContext) -> None:
        super().__init__()
        self.ctx = ctx

    def _exceeded(self) -> tuple[str, int, int] | None:
        ctx, limits = self.ctx, self.ctx.limits
        if ctx.iterations >= limits.max_iterations:
            return "iterations", ctx.iterations, limits.max_iterations
        if ctx.total_tokens >= limits.max_tokens:
            return "tokens", ctx.total_tokens, limits.max_tokens
        if ctx.active_seconds >= limits.max_duration_seconds:
            return "duration", int(ctx.active_seconds), limits.max_duration_seconds
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        hit = self._exceeded()
        if hit is None:
            return None
        limit, value, maximum = hit
        if self.ctx.limit_hit is None:
            self.ctx.limit_hit = limit
            await self.ctx.sink.emit("limit_reached", {"limit": limit, "value": value, "max": maximum})
        notice = f"Exécution arrêtée : la limite {_LABELS[limit]} est atteinte ({value}/{maximum})."
        return {"jump_to": "end", "messages": [AIMessage(content=notice)]}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        response = await handler(request)
        self.ctx.iterations += 1
        for message in _ai_messages(response):
            usage: dict[str, Any] = dict(message.usage_metadata or {})
            self.ctx.input_tokens += int(usage.get("input_tokens", 0))
            self.ctx.output_tokens += int(usage.get("output_tokens", 0))
        return response
