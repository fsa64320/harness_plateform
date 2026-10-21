"""Hooks de type règle : conditions et décisions évaluées en Python pur (FR-012, FR-012a)."""

from __future__ import annotations

import uuid

from harness.catalog.hook_rules import HookEventData, evaluate_rules, rule_matches
from harness.catalog.hooks import Hook

AGENT_ID, PERSON_ID = str(uuid.uuid4()), str(uuid.uuid4())


def event(**overrides: object) -> HookEventData:
    base: dict[str, object] = {
        "event": "before_tool_call",
        "agent_id": AGENT_ID,
        "agent_name": "veille",
        "tool": "crm_send_message",
        "operation": "send_message",
        "classification": "unclassified",
        "triggered_by_type": "person",
        "triggered_by_id": PERSON_ID,
        "arguments": {"text": "Bonjour, voici le mot de passe: hunter2", "to": "alice"},
    }
    return HookEventData(**{**base, **overrides})  # type: ignore[arg-type]


def rule(condition: dict, decision: str = "block", priority: int = 100, events: tuple[str, ...] = ("before_tool_call",)) -> Hook:
    return Hook(
        id=str(uuid.uuid4()), name="h", events=list(events), hook_type="rule",
        condition=condition, decision=decision, priority=priority,
    )


def test_empty_condition_matches_everything() -> None:
    assert rule_matches({}, event())


def test_condition_on_agent_by_id_or_name() -> None:
    assert rule_matches({"agent": AGENT_ID}, event())
    assert rule_matches({"agent": "veille"}, event())
    assert rule_matches({"agent": ["autre", "veille"]}, event())
    assert not rule_matches({"agent": "autre"}, event())


def test_condition_on_tool_by_bound_name_operation_or_server() -> None:
    assert rule_matches({"tool": "crm_send_message"}, event())
    assert rule_matches({"tool": "send_message"}, event())
    assert rule_matches({"tool": "crm"}, event())
    assert not rule_matches({"tool": "wiki"}, event())


def test_classification_treats_unclassified_as_external_effect() -> None:
    assert rule_matches({"classification": "external_effect"}, event(classification="unclassified"))
    assert rule_matches({"classification": "external_effect"}, event(classification="external_effect"))
    assert not rule_matches({"classification": "external_effect"}, event(classification="read_only"))
    assert rule_matches({"classification": "read_only"}, event(classification="read_only"))


def test_condition_on_the_triggerer() -> None:
    assert rule_matches({"triggered_by": {"type": "person"}}, event())
    assert rule_matches({"triggered_by": {"id": PERSON_ID}}, event())
    assert not rule_matches({"triggered_by": {"type": "service_account"}}, event())
    assert rule_matches({"triggered_by": {"type": "service_account"}}, event(triggered_by_type="service_account"))


def test_condition_on_an_argument_pattern() -> None:
    assert rule_matches({"argument_pattern": {"argument": "text", "regex": r"mot de passe"}}, event())
    assert not rule_matches({"argument_pattern": {"argument": "text", "regex": r"^secret"}}, event())
    assert not rule_matches({"argument_pattern": {"argument": "absent", "regex": ".*"}}, event())
    assert rule_matches({"argument_pattern": {"regex": r"alice"}}, event())  # tous les arguments


def test_all_conditions_must_hold() -> None:
    cond = {"tool": "crm", "classification": "external_effect", "triggered_by": {"type": "person"}}
    assert rule_matches(cond, event())
    assert not rule_matches({**cond, "agent": "autre"}, event())


def test_invalid_regex_never_matches_and_never_raises() -> None:
    assert not rule_matches({"argument_pattern": {"regex": "("}}, event())


def test_block_wins_over_allow_whatever_the_priority() -> None:
    verdict = evaluate_rules([rule({}, "block", priority=50), rule({}, "allow", priority=1)], event())
    assert verdict.decision == "block"
    verdict = evaluate_rules([rule({}, "allow", priority=50), rule({}, "block", priority=99)], event())
    assert verdict.decision == "block"


def test_allow_and_log_and_no_match_do_not_block() -> None:
    assert evaluate_rules([rule({}, "allow")], event()).decision == "allow"
    assert evaluate_rules([rule({}, "log")], event()).decision == "allow"
    assert evaluate_rules([rule({"tool": "wiki"}, "block")], event()).decision == "allow"
    assert evaluate_rules([], event()).decision == "allow"


def test_evaluations_are_returned_in_priority_order_with_each_decision() -> None:
    low, high, off = rule({}, "log", priority=20), rule({}, "allow", priority=5), rule({"tool": "wiki"}, "block", priority=1)
    verdict = evaluate_rules([low, high, off], event())
    assert [e.hook_id for e in verdict.evaluations] == [high.id, low.id]
    assert [e.decision for e in verdict.evaluations] == ["allow", "log"]


def test_a_hook_is_only_evaluated_for_its_own_events() -> None:
    only_end = rule({}, "block", events=("run_end",))
    assert evaluate_rules([only_end], event(event="before_tool_call")).decision == "allow"
    assert evaluate_rules([only_end], event(event="run_end")).decision == "block"
