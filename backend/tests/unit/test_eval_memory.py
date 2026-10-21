"""Évaluation de la mémoire (SC-006) : mêmes tâches avec et sans mémoire d'agent, taux comparés."""

from __future__ import annotations

from pathlib import Path

import yaml
from evals.run import load_suite, run_memory_suite, write_memory_report

SUITE = {
    "name": "memory-demo",
    "memory": True,
    "tasks": [
        {
            "id": "code-projet",
            "steps": [
                {"task": "Retiens : le code projet de Globex est ZX-42."},
                {"task": "Quel est le code projet de Globex ?"},
            ],
            "expect": {"result_contains_any": ["ZX-42"]},
            "script": {
                "with_memory": [
                    [
                        {"tool_call": {"name": "write_file", "args": {"file_path": "/memories/globex.md", "content": "Code projet : ZX-42"}}},
                        {"say": "C'est noté."},
                    ],
                    [
                        {"tool_call": {"name": "read_file", "args": {"file_path": "/memories/globex.md"}}},
                        {"say": "Le code projet de Globex est ZX-42."},
                    ],
                ],
                "without_memory": [[{"say": "C'est noté."}], [{"say": "Je ne connais pas ce code."}]],
            },
        },
        {
            "id": "devise",
            "steps": [{"task": "Retiens : on facture Initech en euros."}, {"task": "En quelle devise facturer Initech ?"}],
            "expect": {"result_contains_any": ["euro"]},
            "script": {
                "with_memory": [
                    [
                        {"tool_call": {"name": "write_file", "args": {"file_path": "/memories/initech.md", "content": "Facturation en euros"}}},
                        {"say": "Noté."},
                    ],
                    [
                        {"tool_call": {"name": "read_file", "args": {"file_path": "/memories/initech.md"}}},
                        {"say": "En euros."},
                    ],
                ],
                "without_memory": [[{"say": "Noté."}], [{"say": "En dollars, je suppose."}]],
            },
        },
    ],
}


def _suite(tmp_path: Path) -> Path:
    directory = tmp_path / "suites" / "memory-demo"
    directory.mkdir(parents=True)
    (directory / "tasks.yaml").write_text(yaml.safe_dump(SUITE))
    return directory


async def test_the_same_tasks_run_with_and_without_memory_and_memory_wins(tmp_path: Path) -> None:
    without, with_memory = await run_memory_suite(load_suite(_suite(tmp_path)), provider="fake")
    assert [(r.task_id, r.passed) for r in without.results] == [("code-projet", False), ("devise", False)]
    assert [(r.task_id, r.passed) for r in with_memory.results] == [("code-projet", True), ("devise", True)]
    assert with_memory.success_rate - without.success_rate == 1.0


async def test_memory_persists_between_the_steps_of_a_scenario_but_not_between_scenarios(tmp_path: Path) -> None:
    suite = load_suite(_suite(tmp_path))
    suite.tasks[1]["script"]["with_memory"][1] = [
        {"tool_call": {"name": "read_file", "args": {"file_path": "/memories/globex.md"}}},  # celle d'un autre scénario
        {"say": "Je ne sais pas."},
    ]
    _, with_memory = await run_memory_suite(suite, provider="fake")
    assert dict((r.task_id, r.passed) for r in with_memory.results) == {"code-projet": True, "devise": False}


async def test_report_compares_both_modes_and_states_the_sc_006_verdict(tmp_path: Path) -> None:
    without, with_memory = await run_memory_suite(load_suite(_suite(tmp_path)), provider="fake")
    out = tmp_path / "memory.md"
    write_memory_report(without, with_memory, out)
    text = out.read_text()
    assert text.startswith("# Rapport d'évaluation de la mémoire : memory-demo")
    assert "Sans mémoire : 0.0 %" in text and "Avec mémoire : 100.0 %" in text
    assert "| code-projet | ❌ | ✅ |" in text
    assert "SC-006" in text and "atteint" in text
