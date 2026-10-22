"""Tout résultat d'outil est une donnée non fiable, jamais une instruction (FR-018, constitution II)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.runtime.context import RunContext
from harness.traces.writer import message_text

TAG = "untrusted_tool_result"


def wrap_untrusted(text: str, source: str) -> str:
    """Encadre `text` ; une balise fermante glissée dans le contenu est neutralisée."""
    safe = text.replace(f"</{TAG}", f"<\\/{TAG}")
    return f'<{TAG} tool="{source}">\n{safe}\n</{TAG}>'


class UntrustedContentMiddleware(AgentMiddleware):
    def __init__(self, ctx: RunContext) -> None:
        super().__init__()
        self.ctx = ctx

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        name = request.tool_call["name"]
        path = str(request.tool_call.get("args", {}).get("file_path", ""))
        if name == "read_file" and path.startswith("/skills/") and result.status != "error":
            # Les skills sont rédigées par les gestionnaires d'outils du groupe : ce sont des instructions
            # de confiance du catalogue, pas du contenu externe (elles restent lisibles seulement).
            return result.model_copy(update={"content": f'<skill_file path="{path}">\n{message_text(result)}\n</skill_file>'})
        return result.model_copy(update={"content": wrap_untrusted(message_text(result), name)})
