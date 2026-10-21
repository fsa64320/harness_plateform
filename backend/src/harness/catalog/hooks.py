"""Hooks du catalogue : définition et exécuteur d'une exécution (FR-012, FR-013)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from harness.catalog.hook_rules import Evaluation, HookEventData, Verdict, combine, rule_matches
from harness.catalog.hook_webhook import WebhookRequestContext, call_webhook

EVENTS = ("run_start", "run_end", "before_tool_call", "after_tool_call")


@dataclass(frozen=True)
class Hook:
    id: str
    name: str
    events: list[str]
    hook_type: str  # rule | webhook
    condition: dict[str, Any] | None = None
    decision: str | None = None
    url: str | None = None
    timeout_seconds: float = 10
    priority: int = 100

    @classmethod
    def from_snapshot(cls, data: Mapping[str, Any]) -> Hook:
        return cls(
            id=data["id"],
            name=data["name"],
            events=list(data["events"]),
            hook_type=data["hook_type"],
            condition=data.get("condition"),
            decision=data.get("decision"),
            url=data.get("url"),
            timeout_seconds=data.get("timeout_seconds", 10),
            priority=data.get("priority", 100),
        )


@dataclass
class HookRunner:
    """Évalue les hooks autorisés pour l'agent. Règles et services externes sont triés par priorité ;
    `block` l'emporte. Un service externe en échec bloque (échec sûr)."""

    hooks: Sequence[Hook]
    group_id: uuid.UUID
    run_id: uuid.UUID
    secrets: Mapping[str, str] = field(default_factory=dict)  # id de hook -> secret de signature
    known_secrets: Iterable[str] = ()
    http: httpx.AsyncClient | None = None

    def applies_to(self, event: str) -> bool:
        return any(event in h.events for h in self.hooks)

    async def evaluate(self, data: HookEventData) -> Verdict:
        evaluations: list[Evaluation] = []
        client = self.http or httpx.AsyncClient(follow_redirects=False)
        try:
            for hook in sorted(self.hooks, key=lambda h: h.priority):
                if data.event not in hook.events:
                    continue
                if hook.hook_type == "rule":
                    if rule_matches(hook.condition, data):
                        started = time.monotonic()
                        decision = hook.decision or "log"
                        evaluations.append(
                            Evaluation(
                                hook.id, "rule", data.event, decision, f"règle « {hook.name} » : {decision}",
                                int((time.monotonic() - started) * 1000),
                            )
                        )
                    continue
                result = await call_webhook(
                    hook,
                    data,
                    WebhookRequestContext(self.group_id, self.run_id),
                    secret=self.secrets.get(hook.id, ""),
                    http=client,
                    known_secrets=self.known_secrets,
                )
                evaluations.append(
                    Evaluation(hook.id, "webhook", data.event, result.decision, result.reason, result.duration_ms)
                )
        finally:
            if self.http is None:
                await client.aclose()
        return Verdict(combine([e.decision for e in evaluations]), evaluations)
