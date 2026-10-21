"""Trace de l'exécution : une entrée par étape du modèle et par appel d'outil (FR-007, constitution IV).

Les entrées sont masquées et tronquées à l'écriture (`TraceWriter`). `input_messages` ne porte que les
messages nouveaux depuis l'étape précédente : leur concaténation, après `run_started.system_prompt`,
redonne exactement le contexte envoyé au modèle."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from harness.runtime.context import RunContext
from harness.traces.writer import message_text, serialize_messages


def _common_prefix(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _ai_messages(response: Any) -> list[AIMessage]:
    if isinstance(response, AIMessage):
        return [response]
    result = getattr(response, "result", None)
    if result is None and hasattr(response, "model_response"):
        result = response.model_response.result
    return [m for m in (result or []) if isinstance(m, AIMessage)]


class TraceMiddleware(AgentMiddleware):
    def __init__(self, ctx: RunContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._previous: list[dict[str, Any]] = []
        self._system_text: str | None = None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        started = time.monotonic()
        current = serialize_messages(request.messages)
        prefix = _common_prefix(self._previous, current)
        system_text = message_text(request.system_message) if request.system_message else ""

        response = await handler(request)

        payload: dict[str, Any] = {
            "iteration": self.ctx.iterations,
            "input_messages": current[prefix:],
        }
        if prefix < len(self._previous):
            payload["context_rewritten_from"] = prefix  # résumé ou purge du contexte par le framework
        if self._system_text is not None and system_text != self._system_text:
            payload["system_prompt"] = system_text
        self._previous, self._system_text = current, system_text

        for message in _ai_messages(response)[:1]:
            usage: dict[str, Any] = dict(message.usage_metadata or {})
            payload.update(
                content=message_text(message),
                tool_calls=[{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls],
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            )
        payload["duration_ms"] = int((time.monotonic() - started) * 1000)
        await self.ctx.sink.emit("model_step", payload)
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name, call_id = call["name"], call.get("id")
        await self.ctx.sink.emit(
            "tool_call_started",
            {
                "call_id": call_id,
                "tool": name,
                "operation": self.ctx.operations.get(name, name),
                "classification": self.ctx.classification_of(name),
                "arguments": call.get("args", {}),
            },
        )
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            result = await handler(request)
        except (GraphBubbleUp, asyncio.CancelledError):
            raise  # interruption (approbation) ou arrêt : pas une erreur d'outil
        except Exception as exc:  # noqa: BLE001 - l'erreur est consignée et rendue au modèle
            error = f"{type(exc).__name__}: {exc}"
            await self.ctx.sink.emit(
                "tool_call_finished",
                {"call_id": call_id, "status": "error", "result": None, "duration_ms": elapsed(), "error": error},
            )
            return ToolMessage(
                content=f"Erreur de l'outil {name} : {error}", tool_call_id=call_id or "", name=name, status="error"
            )

        text = message_text(result) if isinstance(result, ToolMessage) else ""
        failed = isinstance(result, ToolMessage) and result.status == "error"
        await self.ctx.sink.emit(
            "tool_call_finished",
            {
                "call_id": call_id,
                "status": "error" if failed else "ok",
                "result": text,
                "duration_ms": elapsed(),
                "error": text if failed else None,
            },
        )
        return result
