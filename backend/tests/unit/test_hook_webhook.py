"""Hooks de type service externe : requête signée, délai de 10 s, échec sûr = blocage (FR-012b, FR-013)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import pytest

from harness.catalog.hook_rules import HookEventData
from harness.catalog.hook_webhook import WebhookRequestContext, call_webhook
from harness.catalog.hooks import Hook

SECRET = "signing-secret"  # noqa: S105
CTX = WebhookRequestContext(group_id=uuid.uuid4(), run_id=uuid.uuid4())


def hook(**overrides: Any) -> Hook:
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()), "name": "validation", "events": ["before_tool_call"], "hook_type": "webhook",
        "url": "https://hooks.example.test/decide", "timeout_seconds": 10, "priority": 100,
    }
    return Hook(**{**base, **overrides})


def data(**overrides: Any) -> HookEventData:
    base: dict[str, Any] = {
        "event": "before_tool_call", "agent_id": str(uuid.uuid4()), "agent_name": "veille", "tool": "crm_create_ticket",
        "operation": "create_ticket", "classification": "external_effect", "triggered_by_type": "person",
        "triggered_by_id": str(uuid.uuid4()), "arguments": {"title": "Panne"},
    }
    return HookEventData(**{**base, **overrides})


def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(decision: str = "allow", **extra: Any) -> httpx.Response:
    return httpx.Response(200, json={"decision": decision, **extra})


async def test_request_carries_the_contract_headers_and_a_valid_hmac_signature() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok()

    await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(handler))
    request = seen[0]
    assert request.method == "POST" and str(request.url) == "https://hooks.example.test/decide"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-harness-event"] == "before_tool_call"
    uuid.UUID(request.headers["x-harness-delivery"])
    timestamp = request.headers["x-harness-timestamp"]
    assert abs(int(timestamp) - time.time()) < 5
    expected = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-harness-signature"] == f"sha256={expected}"


async def test_body_matches_the_contract_and_holds_no_credential_and_no_memory() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok()

    event = data(arguments={"title": "Panne", "api_key": "sk-ant-api03-abcdefghijklmnopqrstuvwx", "memory": "x"})
    await call_webhook(hook(), event, CTX, secret=SECRET, http=client(handler), known_secrets=["topsecret"])
    body = json.loads(seen[0].content)
    assert body["event"] == "before_tool_call"
    assert body["group_id"] == str(CTX.group_id) and body["run_id"] == str(CTX.run_id)
    assert body["agent"] == {"id": event.agent_id, "name": "veille"}
    assert body["triggered_by"] == {"type": "person", "id": event.triggered_by_id}
    call = body["tool_call"]
    assert (call["tool"], call["operation"], call["classification"]) == ("crm_create_ticket", "create_ticket", "external_effect")
    assert call["arguments"]["title"] == "Panne"
    assert "sk-ant" not in seen[0].content.decode() and SECRET not in seen[0].content.decode()
    assert set(body) <= {"event", "group_id", "run_id", "agent", "triggered_by", "tool_call", "status", "stop_reason"}


async def test_run_events_have_no_tool_call_and_run_end_adds_status() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return ok()

    await call_webhook(hook(events=["run_start"]), data(event="run_start"), CTX, secret=SECRET, http=client(handler))
    await call_webhook(
        hook(events=["run_end"]), data(event="run_end", status="failed", stop_reason="error"), CTX, secret=SECRET, http=client(handler)
    )
    assert "tool_call" not in seen[0] and "tool_call" not in seen[1]
    assert (seen[1]["status"], seen[1]["stop_reason"]) == ("failed", "error")


@pytest.mark.parametrize("decision", ["allow", "block", "log"])
async def test_valid_decisions_are_returned_with_their_reason(decision: str) -> None:
    result = await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(lambda r: ok(decision, reason="ok chez moi")))
    assert (result.decision, result.reason, result.cause) == (decision, "ok chez moi", None)


async def test_timeout_yields_block_with_cause() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return ok()

    result = await call_webhook(hook(timeout_seconds=0.05), data(), CTX, secret=SECRET, http=client(slow))
    assert result.decision == "block" and result.cause == "timeout"


async def test_timeout_is_capped_at_ten_seconds() -> None:
    from harness.catalog.hook_webhook import MAX_TIMEOUT_SECONDS

    assert MAX_TIMEOUT_SECONDS == 10


async def test_network_error_yields_block() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refusé")

    result = await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(boom))
    assert result.decision == "block" and result.cause == "network_error"


@pytest.mark.parametrize("status", [201, 204, 400, 500, 503])
async def test_non_200_status_yields_block(status: int) -> None:
    result = await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(lambda r: httpx.Response(status, json={"decision": "allow"})))
    assert result.decision == "block" and result.cause == f"http_status_{status}"


@pytest.mark.parametrize("response", [httpx.Response(200, text="pas du json"), httpx.Response(200, json=["allow"]), httpx.Response(200, json={})])
async def test_invalid_body_yields_block(response: httpx.Response) -> None:
    result = await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(lambda r: response))
    assert result.decision == "block" and result.cause == "invalid_body"


async def test_unknown_decision_yields_block() -> None:
    result = await call_webhook(hook(), data(), CTX, secret=SECRET, http=client(lambda r: ok("peut-être")))
    assert result.decision == "block" and result.cause == "unknown_decision"


async def test_non_https_url_is_never_called() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return ok()

    result = await call_webhook(hook(url="http://hooks.example.test/x"), data(), CTX, secret=SECRET, http=client(handler))
    assert result.decision == "block" and result.cause == "insecure_url" and not called
