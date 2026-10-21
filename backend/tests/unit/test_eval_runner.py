"""Évaluations (constitution V) : suites versionnées, rejeu déterministe, rapport avant/après."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from evals.run import compare_reports, load_suite, run_suite, write_report


def _suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suites" / "demo"
    suite.mkdir(parents=True)
    (suite / "tasks.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "tasks": [
                    {
                        "id": "lookup-ok",
                        "task": "cherche acme",
                        "script": [
                            {"tool_call": {"name": "crm_lookup", "args": {"query": "acme"}}},
                            {"say": "trouvé"},
                        ],
                        "expect": {"status": "succeeded", "tools_called": ["crm_lookup"], "result_contains": "trouvé"},
                    },
                    {
                        "id": "refuse-send",
                        "task": "envoie un message",
                        "allowed": ["lookup"],
                        "script": [
                            {"tool_call": {"name": "crm_send_message", "args": {"text": "x"}}},
                            {"say": "refusé"},
                        ],
                        "expect": {"status": "succeeded", "tools_called": [], "events": ["tool_call_refused"]},
                    },
                    {
                        "id": "doit-echouer",
                        "task": "attendu faux",
                        "script": [{"say": "autre chose"}],
                        "expect": {"status": "succeeded", "result_contains": "introuvable"},
                    },
                ],
            }
        )
    )
    return suite


async def test_suite_runs_deterministically_against_the_fake_provider(tmp_path: Path) -> None:
    suite = load_suite(_suite(tmp_path))
    first = await run_suite(suite, provider="fake")
    second = await run_suite(suite, provider="fake")

    assert [(r.task_id, r.passed) for r in first.results] == [
        ("lookup-ok", True),
        ("refuse-send", True),
        ("doit-echouer", False),
    ]
    assert [(r.task_id, r.passed) for r in first.results] == [(r.task_id, r.passed) for r in second.results]
    assert first.success_rate == pytest.approx(2 / 3)
    assert "introuvable" in first.results[2].failures[0]


async def test_report_is_markdown_with_the_success_rate_per_task(tmp_path: Path) -> None:
    suite = load_suite(_suite(tmp_path))
    report = await run_suite(suite, provider="fake")
    out = tmp_path / "report.md"
    write_report(report, out)
    text = out.read_text()
    assert text.startswith("# Rapport d'évaluation : demo")
    assert "Taux de réussite : 66.7 %" in text or "Taux de réussite : 66,7 %" in text
    assert "| lookup-ok | ✅ |" in text and "| doit-echouer | ❌ |" in text


async def test_baseline_comparison_shows_before_and_after(tmp_path: Path) -> None:
    directory = _suite(tmp_path)
    before = await run_suite(load_suite(directory), provider="fake")
    baseline = tmp_path / "baseline.md"
    write_report(before, baseline)

    # « Après » : le comportement de la tâche ratée est corrigé.
    data = yaml.safe_load((directory / "tasks.yaml").read_text())
    data["tasks"][2]["expect"]["result_contains"] = "autre chose"
    (directory / "tasks.yaml").write_text(yaml.safe_dump(data))
    after = await run_suite(load_suite(directory), provider="fake")

    out = tmp_path / "after.md"
    write_report(after, out, baseline=baseline)
    text = out.read_text()
    assert "## Comparaison avec la référence" in text
    assert "doit-echouer | ❌ | ✅" in text
    assert compare_reports(before, after)["doit-echouer"] == (False, True)
