"""Liste blanche d'outils : l'agent ne voit et n'exécute que les outils autorisés (FR-006, SC-004).

Appliquée deux fois : à la liaison au modèle (il ne connaît que les outils autorisés) et à l'exécution
(tout appel hors liste, ou à un nom inventé, est refusé et consigné)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.runtime.context import RunContext


def tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("function", {}).get("name", ""))
    return str(tool.name)


class AllowlistMiddleware(AgentMiddleware):
    def __init__(self, ctx: RunContext) -> None:
        super().__init__()
        self.ctx = ctx

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        allowed = self.ctx.allowed_tools
        return await handler(request.override(tools=[t for t in request.tools if tool_name(t) in allowed]))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool_call["name"]
        if name in self.ctx.allowed_tools and request.tool is not None:
            return await handler(request)

        # Un nom absent des outils liés est « inconnu » (souvent inventé par le modèle) ; un outil
        # présent mais hors liste (outil du framework, p. ex. `task` désactivé) est « non autorisé ».
        reason = "unknown_tool" if request.tool is None else "not_allowed"
        call_id = request.tool_call.get("id")
        await self.ctx.sink.emit("tool_call_refused", {"call_id": call_id, "tool": name, "reason": reason})
        return ToolMessage(
            content=f"Appel refusé : l'outil « {name} » n'est pas autorisé pour cet agent ({reason}).",
            tool_call_id=call_id or "",
            name=name,
            status="error",
        )
