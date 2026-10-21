"""Fournisseurs de modèles : FakeProvider (tests) et OllamaProvider (client mocké)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from harness.runtime import models
from harness.runtime.models import FakeProvider, OllamaProvider, say, tool_call


async def test_fake_provider_replays_messages_and_tool_calls() -> None:
    provider = FakeProvider([tool_call("lookup", {"q": "a"}), say("fin")])
    model = provider.get_chat_model("fake")

    first = await model.ainvoke([HumanMessage("go")])
    assert first.tool_calls[0]["name"] == "lookup"
    assert first.tool_calls[0]["args"] == {"q": "a"}
    assert first.tool_calls[0]["id"]  # identifiant généré

    second = await model.ainvoke([HumanMessage("suite")])
    assert second.content == "fin"
    assert second.tool_calls == []


async def test_fake_provider_reports_token_usage() -> None:
    model = FakeProvider([say("ok", input_tokens=7, output_tokens=3)]).get_chat_model("fake")
    reply = await model.ainvoke([HumanMessage("x")])
    assert reply.usage_metadata is not None
    assert reply.usage_metadata["input_tokens"] == 7
    assert reply.usage_metadata["output_tokens"] == 3


async def test_fake_provider_bind_tools_keeps_the_script() -> None:
    model = FakeProvider([say("ok")]).get_chat_model("fake")
    bound = model.bind_tools([])
    assert (await bound.ainvoke([HumanMessage("x")])).content == "ok"


async def test_fake_provider_fails_loudly_when_script_is_exhausted() -> None:
    model = FakeProvider([say("seul")]).get_chat_model("fake")
    await model.ainvoke([HumanMessage("1")])
    with pytest.raises(RuntimeError, match="script"):
        await model.ainvoke([HumanMessage("2")])


async def test_fake_provider_supports_tool_calling_check() -> None:
    assert await FakeProvider([]).check_tool_calling("fake") is True
    assert await FakeProvider([], tool_calling=False).check_tool_calling("fake") is False


class _StubOllama:
    reply: AIMessage = AIMessage(content="")
    last_init: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_init = kwargs

    def bind_tools(self, tools: Any, **_: Any) -> _StubOllama:
        return self

    async def ainvoke(self, *_: Any, **__: Any) -> AIMessage:
        return type(self).reply


async def test_ollama_check_true_when_reply_contains_a_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _StubOllama.reply = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "1"}])
    monkeypatch.setattr(models, "ChatOllama", _StubOllama)
    provider = OllamaProvider(base_url="http://ollama:11434")
    assert await provider.check_tool_calling("qwen3") is True
    assert _StubOllama.last_init["model"] == "qwen3"
    assert _StubOllama.last_init["base_url"] == "http://ollama:11434"


async def test_ollama_check_false_when_reply_has_no_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _StubOllama.reply = AIMessage(content="Je ne sais pas appeler d'outil")
    monkeypatch.setattr(models, "ChatOllama", _StubOllama)
    assert await OllamaProvider(base_url="http://x").check_tool_calling("tiny") is False
