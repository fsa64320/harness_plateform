"""Hooks de type règle : une condition déclarative et une décision, évaluées en Python pur.

Aucun code fourni par un membre n'est exécuté (FR-012a). Condition (tous les champs sont facultatifs et
se cumulent) : `agent` (id ou nom), `tool` (nom lié, opération ou serveur), `classification`
(un outil non classé compte comme « à effet externe »), `triggered_by` (`type` et/ou `id`),
`argument_pattern` (`regex` sur un argument nommé ou sur tous). Plusieurs valeurs : une liste."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.catalog.hooks import Hook


@dataclass(frozen=True)
class HookEventData:
    """Ce qu'un hook peut connaître d'un événement : le strict nécessaire à sa décision (FR-012b)."""

    event: str  # run_start | run_end | before_tool_call | after_tool_call
    agent_id: str
    agent_name: str
    triggered_by_type: str
    triggered_by_id: str
    tool: str | None = None  # nom lié, ex. crm_send_message
    operation: str | None = None
    classification: str | None = None
    arguments: dict[str, Any] | None = None
    status: str | None = None  # run_end seulement
    stop_reason: str | None = None


@dataclass(frozen=True)
class Evaluation:
    hook_id: str
    hook_type: str
    event: str
    decision: str
    reason: str
    duration_ms: int = 0


@dataclass(frozen=True)
class Verdict:
    decision: str  # allow | block (log n'a pas d'effet sur l'action)
    evaluations: list[Evaluation]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _effective(classification: str | None) -> str:
    return "external_effect" if classification in (None, "unclassified") else classification


def _matches_pattern(spec: dict[str, Any], arguments: dict[str, Any] | None) -> bool:
    try:
        regex = re.compile(str(spec["regex"]))
    except (KeyError, re.error):
        return False
    arguments = arguments or {}
    name = spec.get("argument")
    if name is not None:
        if name not in arguments:
            return False
        values = [arguments[name]]
    else:
        values = list(arguments.values())
    return any(regex.search(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for v in values)


def rule_matches(condition: dict[str, Any] | None, data: HookEventData) -> bool:
    cond = condition or {}
    if "agent" in cond and not {data.agent_id, data.agent_name} & set(map(str, _as_list(cond["agent"]))):
        return False
    if "tool" in cond:
        server = (data.tool or "").split("_", 1)[0] if data.tool else ""
        names = {data.tool, data.operation, server} - {None, ""}
        if not names & set(map(str, _as_list(cond["tool"]))):
            return False
    if "classification" in cond:
        if data.classification is None:
            return False
        if _effective(data.classification) not in {_effective(c) for c in _as_list(cond["classification"])}:
            return False
    if "triggered_by" in cond:
        who = cond["triggered_by"]
        if "type" in who and who["type"] != data.triggered_by_type:
            return False
        if "id" in who and who["id"] != data.triggered_by_id:
            return False
    return not ("argument_pattern" in cond and not _matches_pattern(cond["argument_pattern"], data.arguments))


def combine(decisions: list[str]) -> str:
    """`block` l'emporte sur `allow` ; `log` n'a pas d'effet ; sans règle applicable : `allow`."""
    return "block" if "block" in decisions else "allow"


def evaluate_rules(hooks: list[Hook], data: HookEventData) -> Verdict:
    evaluations: list[Evaluation] = []
    for hook in sorted(hooks, key=lambda h: h.priority):
        if hook.hook_type != "rule" or data.event not in hook.events:
            continue
        if rule_matches(hook.condition, data):
            decision = hook.decision or "log"
            evaluations.append(
                Evaluation(hook.id, "rule", data.event, decision, f"règle « {hook.name} » : {decision}")
            )
    return Verdict(combine([e.decision for e in evaluations]), evaluations)
