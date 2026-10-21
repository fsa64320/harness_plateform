"""Évaluations d'agents (constitution V) : suites versionnées, rejeu et rapport avant/après.

    uv run python -m evals.run --suite core --model qwen3 [--baseline evals/reports/core-baseline.md]

Une suite (`evals/suites/<nom>/tasks.yaml`) décrit des tâches, leur issue attendue et, pour le
fournisseur `fake`, le script du modèle (rejeu déterministe). Avec `--model`, le vrai modèle Ollama joue
les mêmes tâches contre des outils factices déterministes.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from harness.runtime.builder import build_agent, make_run_context
from harness.runtime.models import FakeProvider, ModelProvider, OllamaProvider, Step, say, tool_call
from harness.traces.bus import TraceBus
from harness.traces.memory import MemoryTraceStore
from harness.traces.writer import RunEventSink, TraceWriter, message_text

EVALS_DIR = Path(__file__).resolve().parent
OPERATIONS = {"lookup": "read_only", "send_message": "unclassified", "wipe": "external_effect"}


@dataclass
class Suite:
    name: str
    tasks: list[dict[str, Any]]
    directory: Path


@dataclass
class TaskResult:
    task_id: str
    passed: bool
    status: str = ""
    failures: list[str] = field(default_factory=list)


@dataclass
class Report:
    suite: str
    provider: str
    results: list[TaskResult]
    model: str | None = None

    @property
    def success_rate(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0


def load_suite(directory: Path) -> Suite:
    data = yaml.safe_load((directory / "tasks.yaml").read_text())
    return Suite(name=data["name"], tasks=data["tasks"], directory=directory)


def _snapshot(task: dict[str, Any]) -> dict[str, Any]:
    allowed = set(task.get("allowed", ["lookup"]))
    ops = [{"id": str(uuid.uuid4()), "name": n, "classification": c} for n, c in OPERATIONS.items()]
    limits = {"max_iterations": 12, "max_duration_seconds": 120, "max_tokens": 200_000, **task.get("limits", {})}
    return {
        "agent_id": str(uuid.uuid4()),
        "name": "agent-eval",
        "instructions": task.get("instructions", "Tu es un assistant. Utilise les outils quand c'est utile."),
        "model_id": "eval",
        "allowed_tool_ids": [o["id"] for o in ops if o["name"] in allowed],
        "tools": [{"id": str(uuid.uuid4()), "kind": "mcp_server", "name": "crm", "operations": ops}],
        "agent_memory_enabled": False,
        "group_memory_enabled": False,
        "subagents_enabled": False,
        "limits": limits,
    }


def _tools(calls: list[str]) -> list[BaseTool]:
    def make(name: str) -> BaseTool:
        async def run(query: str = "", text: str = "") -> str:
            calls.append(f"crm_{name}")
            return f"résultat de {name} pour « {query or text} »"

        return StructuredTool.from_function(
            coroutine=run,
            name=f"crm_{name}",
            description=f"Opération {name} du CRM",
            args_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "text": {"type": "string"}},
            },
        )

    return [make(n) for n in OPERATIONS]


def _script(raw: list[dict[str, Any]]) -> list[Step]:
    steps: list[Step] = []
    for item in raw:
        delay = float(item.get("delay", 0))
        if "say" in item:
            steps.append(say(item["say"], delay=delay))
        else:
            call = item["tool_call"]
            steps.append(tool_call(call["name"], call.get("args", {}), delay=delay))
    return steps


async def run_task(task: dict[str, Any], provider: ModelProvider) -> TaskResult:
    snapshot = _snapshot(task)
    store = MemoryTraceStore()
    run_id, group_id = uuid.uuid4(), uuid.uuid4()
    sink = RunEventSink(TraceWriter(store, TraceBus()), group_id=group_id, run_id=run_id)
    ctx = make_run_context(snapshot, run_id=run_id, group_id=group_id, sink=sink)
    calls: list[str] = []
    graph = build_agent(
        snapshot, provider, _tools(calls), checkpointer=InMemorySaver(), backend_factory=None, ctx=ctx
    )
    status, final = "succeeded", ""
    try:
        # `stop_after` simule la demande d'arrêt d'un membre au bout de N secondes.
        out = await asyncio.wait_for(
            graph.ainvoke(
                {"messages": [HumanMessage(task["task"])]},
                config={"configurable": {"thread_id": str(run_id)}, "recursion_limit": 100},
            ),
            timeout=task.get("stop_after"),
        )
        if out.get("__interrupt__"):
            status = "awaiting_approval"
        elif ctx.limit_hit:
            status = "limit_reached"
        final = next((message_text(m) for m in reversed(out["messages"]) if isinstance(m, AIMessage)), "")
    except TimeoutError:
        status = "stopped"
    except Exception as exc:  # noqa: BLE001 - une erreur d'exécution est un résultat d'évaluation
        status, final = "failed", f"{type(exc).__name__}: {exc}"

    expect = task.get("expect", {})
    failures: list[str] = []
    if "status" in expect and status != expect["status"]:
        failures.append(f"status: attendu {expect['status']}, obtenu {status}")
    if "tools_called" in expect and calls != expect["tools_called"]:
        failures.append(f"tools_called: attendu {expect['tools_called']}, obtenu {calls}")
    if "result_contains" in expect and expect["result_contains"] not in final:
        failures.append(f"result_contains: « {expect['result_contains']} » introuvable dans « {final[:80]} »")
    for event in expect.get("events", []):
        if event not in store.types():
            failures.append(f"events: {event} absent de la trace")
    return TaskResult(task["id"], not failures, status, failures)


async def run_suite(suite: Suite, provider: str = "fake", model: str | None = None, base_url: str = "http://localhost:11434") -> Report:
    results = []
    for task in suite.tasks:
        if provider == "fake":
            fake: ModelProvider = FakeProvider(_script(task.get("script", [])))
        else:
            fake = _OllamaFixedModel(OllamaProvider(base_url), model or "qwen3")
        results.append(await run_task(task, fake))
    return Report(suite.name, provider, results, model)


class _OllamaFixedModel:
    """Fournisseur Ollama dont le modèle est imposé par la ligne de commande."""

    def __init__(self, inner: OllamaProvider, model: str) -> None:
        self._inner, self._model = inner, model

    def get_chat_model(self, model_id: str) -> Any:
        return self._inner.get_chat_model(self._model)

    async def check_tool_calling(self, model_id: str) -> bool:
        return await self._inner.check_tool_calling(self._model)


def compare_reports(before: Report, after: Report) -> dict[str, tuple[bool | None, bool | None]]:
    old = {r.task_id: r.passed for r in before.results}
    new = {r.task_id: r.passed for r in after.results}
    return {t: (old.get(t), new.get(t)) for t in [*new, *(t for t in old if t not in new)]}


_ROW = re.compile(r"^\| (\S+) \| (✅|❌) \|", re.MULTILINE)


def parse_report(path: Path) -> Report:
    text = path.read_text()
    head = text.split("## Comparaison")[0]
    suite = re.search(r"^# Rapport d'évaluation : (.+)$", head, re.MULTILINE)
    results = [TaskResult(m.group(1), m.group(2) == "✅") for m in _ROW.finditer(head)]
    return Report(suite.group(1) if suite else "?", "?", results)


def _mark(value: bool | None) -> str:
    return "—" if value is None else ("✅" if value else "❌")


def write_report(report: Report, path: Path, baseline: Path | None = None) -> None:
    passed = sum(r.passed for r in report.results)
    lines = [
        f"# Rapport d'évaluation : {report.suite}",
        "",
        f"- Fournisseur : {report.provider}" + (f" (modèle {report.model})" if report.model else ""),
        f"- Taux de réussite : {report.success_rate * 100:.1f} % ({passed}/{len(report.results)})",
        "",
        "| Tâche | Résultat | Détail |",
        "|---|---|---|",
        *(
            f"| {r.task_id} | {_mark(r.passed)} | {'; '.join(r.failures).replace('|', '/')} |"
            for r in report.results
        ),
    ]
    if baseline is not None:
        before = parse_report(baseline)
        lines += [
            "",
            "## Comparaison avec la référence",
            "",
            f"Taux de réussite : {before.success_rate * 100:.1f} % → {report.success_rate * 100:.1f} %",
            "",
            "| Tâche | Avant | Après |",
            "|---|---|---|",
            *(f"| {t} | {_mark(b)} | {_mark(a)} |" for t, (b, a) in compare_reports(before, report).items()),
        ]
    path.write_text("\n".join(lines) + "\n")


async def _main(args: argparse.Namespace) -> int:
    suite = load_suite(EVALS_DIR / "suites" / args.suite)
    provider = args.provider or ("ollama" if args.model else "fake")
    report = await run_suite(suite, provider, args.model, args.ollama_url)
    out = Path(args.out) if args.out else EVALS_DIR / "reports" / f"{args.suite}.md"
    write_report(report, out, Path(args.baseline) if args.baseline else None)
    print(f"{suite.name} : {report.success_rate * 100:.1f} % -> {out}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--model", help="modèle Ollama (sinon rejeu avec le faux modèle)")
    parser.add_argument("--provider", choices=["fake", "ollama"])
    parser.add_argument("--baseline", help="rapport de référence à comparer")
    parser.add_argument("--out")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
