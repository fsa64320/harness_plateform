"""Hooks de type service externe : appel HTTPS signé, avec échec sûr (FR-012b, FR-013).

Contrat : specs/001-agent-harness-platform/contracts/hook-webhook.md. Toute défaillance (délai de 10 s,
erreur réseau, statut autre que 200, corps ou décision invalides) vaut `block`, avec sa cause."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from harness.catalog.hook_rules import HookEventData
from harness.traces.redaction import redact

if TYPE_CHECKING:
    from harness.catalog.hooks import Hook

MAX_TIMEOUT_SECONDS = 10
DECISIONS = {"allow", "block", "log"}


@dataclass(frozen=True)
class WebhookRequestContext:
    group_id: uuid.UUID
    run_id: uuid.UUID


@dataclass(frozen=True)
class WebhookResult:
    decision: str
    reason: str
    cause: str | None  # None si le service a répondu normalement
    duration_ms: int


def build_body(data: HookEventData, ctx: WebhookRequestContext, known_secrets: Iterable[str]) -> dict[str, Any]:
    """Informations minimales pour décider : jamais d'identifiant d'outil ni de contenu de mémoire."""
    body: dict[str, Any] = {
        "event": data.event,
        "group_id": str(ctx.group_id),
        "run_id": str(ctx.run_id),
        "agent": {"id": data.agent_id, "name": data.agent_name},
        "triggered_by": {"type": data.triggered_by_type, "id": data.triggered_by_id},
    }
    if data.event in ("before_tool_call", "after_tool_call"):
        body["tool_call"] = {
            "tool": data.tool,
            "operation": data.operation,
            "classification": data.classification,
            "arguments": redact(data.arguments or {}, known_secrets),
        }
    if data.event == "run_end":
        body["status"], body["stop_reason"] = data.status, data.stop_reason
    return body


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def call_webhook(
    hook: Hook,
    data: HookEventData,
    ctx: WebhookRequestContext,
    *,
    secret: str,
    http: httpx.AsyncClient,
    known_secrets: Iterable[str] = (),
) -> WebhookResult:
    started = time.monotonic()

    def blocked(cause: str, detail: str = "") -> WebhookResult:
        return WebhookResult(
            "block", f"hook « {hook.name} » en échec ({cause}){': ' + detail if detail else ''}", cause,
            int((time.monotonic() - started) * 1000),
        )

    if urlsplit(hook.url or "").scheme != "https":
        return blocked("insecure_url")
    body = json.dumps(build_body(data, ctx, [secret, *known_secrets]), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Harness-Event": data.event,
        "X-Harness-Delivery": str(uuid.uuid4()),
        "X-Harness-Timestamp": timestamp,
        "X-Harness-Signature": sign(secret, timestamp, body),
    }
    timeout = min(float(hook.timeout_seconds), MAX_TIMEOUT_SECONDS)
    try:
        async with asyncio.timeout(timeout):
            response = await http.post(hook.url or "", content=body, headers=headers)
    except TimeoutError:
        return blocked("timeout")
    except httpx.HTTPError as exc:
        return blocked("network_error", f"{type(exc).__name__}: {exc}"[:160])

    if response.status_code != 200:
        return blocked(f"http_status_{response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return blocked("invalid_body")
    if not isinstance(payload, dict) or "decision" not in payload:
        return blocked("invalid_body")
    decision = payload["decision"]
    if decision not in DECISIONS:
        return blocked("unknown_decision", str(decision)[:40])
    reason = str(payload.get("reason") or f"service externe « {hook.name} » : {decision}")
    return WebhookResult(decision, reason, None, int((time.monotonic() - started) * 1000))
