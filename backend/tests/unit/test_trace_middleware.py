"""Trace d'exécution : événements, `seq` sans trou, masquage, troncature (FR-007, FR-009, constitution IV)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.messages import BaseMessage

from harness.runtime.builder import compose_system_prompt
from harness.runtime.models import say, tool_call
from harness.traces.writer import MAX_TEXT_BYTES
from tests.support import make_rig, make_snapshot


def _serialize(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    from harness.traces.writer import serialize_messages

    return serialize_messages(messages)


async def test_model_and_tool_events_have_contiguous_seq_from_one() -> None:
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), say("fini")])
    await rig.run()
    assert rig.store.types() == ["model_step", "tool_call_started", "tool_call_finished", "model_step"]
    assert [e.seq for e in rig.store.events()] == [1, 2, 3, 4]


async def test_model_step_reports_iteration_tokens_and_content() -> None:
    rig = make_rig([say("Bonjour", input_tokens=11, output_tokens=4)])
    await rig.run()
    step = rig.store.of("model_step")[0].payload
    assert step["iteration"] == 1
    assert step["content"] == "Bonjour"
    assert (step["input_tokens"], step["output_tokens"]) == (11, 4)
    assert step["duration_ms"] >= 0


async def test_tool_events_carry_call_id_classification_and_result() -> None:
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), say("fini")])
    await rig.run()
    started, finished = rig.store.of("tool_call_started")[0], rig.store.of("tool_call_finished")[0]
    assert started.payload["call_id"] == finished.payload["call_id"]
    assert started.payload["tool"] == "crm_lookup"
    assert started.payload["classification"] == "read_only"
    assert started.payload["arguments"] == {"query": "x"}
    assert finished.payload["status"] == "ok"
    assert "résultat de lookup" in finished.payload["result"]


async def test_tool_failure_is_traced_with_an_explicit_error_and_the_run_continues() -> None:
    rig = make_rig(
        [tool_call("crm_lookup", {"query": "x"}), say("repli")],
        tool_results={"lookup": ConnectionError("serveur MCP injoignable")},
    )
    result = await rig.run()
    finished = rig.store.of("tool_call_finished")[0].payload
    assert finished["status"] == "error"
    assert "injoignable" in finished["error"]
    assert result["messages"][-1].content == "repli"


async def test_payloads_are_redacted_with_known_secrets_and_patterns() -> None:
    secret = "hunter2-super-secret"  # noqa: S105
    rig = make_rig(
        [tool_call("crm_lookup", {"query": f"mot de passe {secret}"}), say("ok")],
        tool_results={"lookup": f"contact alice@example.com jeton {secret}"},
        known_secrets=[secret],
    )
    await rig.run()
    dumped = json.dumps([e.payload for e in rig.store.events()], ensure_ascii=False)
    assert secret not in dumped
    assert "alice@example.com" not in dumped
    assert "«masqué»" in dumped


async def test_long_text_is_truncated_at_64kb_with_sha256_of_the_full_content() -> None:
    big = "a" * (MAX_TEXT_BYTES * 2)
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), say("ok")], tool_results={"lookup": big})
    await rig.run()
    finished = rig.store.of("tool_call_finished")[0].payload
    assert len(finished["result"].encode()) <= MAX_TEXT_BYTES
    assert finished["truncated"] is True
    assert finished["sha256"] == hashlib.sha256(big.encode()).hexdigest()


async def test_concatenated_input_messages_reproduce_the_model_context_at_each_step() -> None:
    snapshot = make_snapshot()
    rig = make_rig([tool_call("crm_lookup", {"query": "x"}), tool_call("crm_lookup", {"query": "y"}), say("fini")], snapshot)
    await rig.run("la tâche")
    steps = [e.payload for e in rig.store.of("model_step")]
    received = rig.model.received
    assert len(steps) == len(received) == 3

    system = compose_system_prompt(snapshot)
    for n in range(len(steps)):
        context = [m for step in steps[: n + 1] for m in step["input_messages"]]
        sent = received[n]
        assert sent[0].type == "system" and sent[0].content == system
        assert context == _serialize(sent[1:]), f"contexte divergent à l'étape {n + 1}"
