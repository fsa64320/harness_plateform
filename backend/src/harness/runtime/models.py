"""Fournisseurs de modèles derrière une interface remplaçable (constitution : modèle local Ollama,
double de test pour toute la suite unitaire)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from pydantic import PrivateAttr


class ModelProviderError(Exception):
    """Le fournisseur de modèle est injoignable ou a répondu par une erreur."""


class ModelProvider(Protocol):
    def get_chat_model(self, model_id: str) -> BaseChatModel: ...

    async def check_tool_calling(self, model_id: str) -> bool:
        """Vrai si le modèle sait appeler un outil (condition d'acceptation d'un agent)."""
        ...


@tool
def _ping(value: str) -> str:
    """Outil de sonde : à appeler avec la valeur demandée."""
    return value


class OllamaProvider:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def get_chat_model(self, model_id: str) -> BaseChatModel:
        return ChatOllama(model=model_id, base_url=self._base_url)

    async def check_tool_calling(self, model_id: str) -> bool:
        """Appel de test : le modèle doit répondre par un appel de l'outil `ping`."""
        probe = ChatOllama(model=model_id, base_url=self._base_url, temperature=0).bind_tools([_ping])
        try:
            reply = await probe.ainvoke("Appelle l'outil ping avec la valeur « ok ».")
        except Exception as exc:  # noqa: BLE001 - toute erreur client devient une erreur explicite
            raise ModelProviderError(f"Ollama ({self._base_url}) : {exc}") from exc
        return bool(getattr(reply, "tool_calls", None))


# --- Double de test -------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """Une réponse scriptée du faux modèle, avec un délai optionnel avant de répondre."""

    message: AIMessage
    delay: float = 0.0


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def say(
    text: str, *, input_tokens: int = 10, output_tokens: int = 5, delay: float = 0.0
) -> Step:
    return Step(
        AIMessage(content=text, usage_metadata=_usage(input_tokens, output_tokens)),
        delay,
    )


def tool_calls(
    *calls: tuple[str, dict[str, Any]],
    content: str = "",
    input_tokens: int = 10,
    output_tokens: int = 5,
    delay: float = 0.0,
) -> Step:
    return Step(
        AIMessage(
            content=content,
            tool_calls=[
                {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
                for name, args in calls
            ],
            usage_metadata=_usage(input_tokens, output_tokens),
        ),
        delay,
    )


def tool_call(name: str, args: dict[str, Any] | None = None, **kwargs: Any) -> Step:
    return tool_calls((name, args or {}), **kwargs)


class ScriptedChatModel(BaseChatModel):
    """Rejoue un script de réponses ; `bind_tools` renvoie le même modèle (les appels d'outils
    viennent du script, pas du modèle). Les réponses ne passent jamais par le streaming."""

    _steps: list[Step] = PrivateAttr(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _seen: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, steps: Sequence[Step]) -> None:
        super().__init__()
        self._steps = list(steps)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def received(self) -> list[list[BaseMessage]]:
        """Messages reçus à chaque appel : permet de vérifier ce que le modèle a vu."""
        return self._seen

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> Step:
        self._seen.append(list(messages))
        if self._cursor >= len(self._steps):
            raise RuntimeError(
                f"Le script du faux modèle est épuisé après {len(self._steps)} réponse(s)"
            )
        step = self._steps[self._cursor]
        self._cursor += 1
        return step

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        step = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=step.message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        step = self._next(messages)
        if step.delay:
            await asyncio.sleep(step.delay)
        return ChatResult(generations=[ChatGeneration(message=step.message)])


class FakeProvider:
    """Fournisseur de test. `script` est consommé dans l'ordre par tous les modèles créés ;
    `factory` produit au contraire un script neuf à chaque `get_chat_model` (exécutions
    concurrentes)."""

    def __init__(
        self,
        script: Sequence[Step] | None = None,
        *,
        factory: Callable[[], Sequence[Step]] | None = None,
        tool_calling: bool = True,
    ) -> None:
        self._shared = ScriptedChatModel(script or [])
        self._factory = factory
        self._tool_calling = tool_calling
        self.models: list[ScriptedChatModel] = []

    def get_chat_model(self, model_id: str) -> ScriptedChatModel:
        model = ScriptedChatModel(self._factory()) if self._factory else self._shared
        self.models.append(model)
        return model

    async def check_tool_calling(self, model_id: str) -> bool:
        return self._tool_calling
