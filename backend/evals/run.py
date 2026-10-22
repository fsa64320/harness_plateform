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
from deepagents.backends.utils import create_file_data, file_data_to_string
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from harness.memory.backends import build_backend
from harness.runtime.builder import MemoryItem, build_agent, make_run_context, memory_section
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
    memory: bool = False  # suite de scénarios joués avec et sans mémoire d'agent


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
    return Suite(name=data["name"], tasks=data["tasks"], directory=directory, memory=bool(data.get("memory")))


class InMemoryFileStore:
    """Mémoire d'agent d'un scénario d'évaluation : dans le processus, partagée entre ses étapes."""

    read_only = False

    def __init__(self) -> None:
        self.files: dict[str, Any] = {}

    async def load(self) -> dict[str, Any]:
        return dict(self.files)

    async def save(self, path: str, content: str) -> str | None:
        self.files[path] = create_file_data(content)
        return None

    async def remove(self, path: str) -> str | None:
        return None if self.files.pop(path, None) is not None else f"Fichier introuvable : {path}"


def _snapshot(task: dict[str, Any], *, memory: bool = False) -> dict[str, Any]:
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
        "agent_memory_enabled": memory,
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


_INFRA_ERRORS = ("ConnectError", "ResponseError", "RemoteProtocolError", "ReadTimeout", "ModelProviderError")


async def run_task(
    task: dict[str, Any],
    provider: ModelProvider,
    *,
    instruction: str | None = None,
    store: InMemoryFileStore | None = None,
    attempts: int = 3,
) -> TaskResult:
    """Joue une tâche (ou une étape d'un scénario : `store` porte alors la mémoire d'agent). Une panne du
    serveur de modèle (connexion, EOF) n'est pas un échec de l'agent : on rejoue, jusqu'à `attempts` fois."""
    for attempt in range(1, attempts + 1):
        result = await _run_task_once(task, provider, instruction=instruction, store=store)
        infra = result.status == "failed" and any(name in " ".join(result.failures) for name in _INFRA_ERRORS)
        if not infra or attempt == attempts:
            return result
        await asyncio.sleep(2 * attempt)
    raise AssertionError("inatteignable")


async def _run_task_once(
    task: dict[str, Any],
    provider: ModelProvider,
    *,
    instruction: str | None,
    store: InMemoryFileStore | None,
) -> TaskResult:
    task = {**task, "task": instruction or task["task"]}
    snapshot = _snapshot(task, memory=store is not None)
    trace_store = MemoryTraceStore()
    run_id, group_id = uuid.uuid4(), uuid.uuid4()
    sink = RunEventSink(TraceWriter(trace_store, TraceBus()), group_id=group_id, run_id=run_id)
    ctx = make_run_context(snapshot, run_id=run_id, group_id=group_id, sink=sink)
    calls: list[str] = []
    backend = build_backend({"/memories/": store}) if store is not None else None  # type: ignore[dict-item]
    section = (
        memory_section(
            snapshot,
            [MemoryItem(p.lstrip("/"), file_data_to_string(d), 1) for p, d in sorted(store.files.items())],
            [],
        )
        if store is not None
        else None
    )
    graph = build_agent(
        snapshot, provider, _tools(calls), checkpointer=InMemorySaver(), backend_factory=lambda _ctx: backend, ctx=ctx,
        system_sections=[section] if section else [],
    )
    status, final = "succeeded", ""
    infra_failure = ""
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
        infra_failure = final

    expect = task.get("expect", {})
    failures: list[str] = []
    if "result_contains_any" in expect and not any(w.lower() in final.lower() for w in expect["result_contains_any"]):
        failures.append(f"result_contains_any: aucun de {expect['result_contains_any']} dans « {final[:80]} »")
    if "status" in expect and status != expect["status"]:
        failures.append(f"status: attendu {expect['status']}, obtenu {status}")
    if "tools_called" in expect and calls != expect["tools_called"]:
        failures.append(f"tools_called: attendu {expect['tools_called']}, obtenu {calls}")
    if "result_contains" in expect and expect["result_contains"] not in final:
        failures.append(f"result_contains: « {expect['result_contains']} » introuvable dans « {final[:80]} »")
    for event in expect.get("events", []):
        if event not in trace_store.types():
            failures.append(f"events: {event} absent de la trace")
    if infra_failure:
        failures.append(infra_failure)
    return TaskResult(task["id"], not failures, status, failures)


async def run_memory_suite(
    suite: Suite, provider: str = "fake", model: str | None = None, base_url: str = "http://localhost:11434"
) -> tuple[Report, Report]:
    """Chaque scénario (plusieurs étapes) est joué sans mémoire d'agent, puis avec : la mémoire d'un
    scénario n'est jamais partagée avec un autre. Seule la dernière étape est évaluée (SC-006)."""
    reports: dict[str, list[TaskResult]] = {"without_memory": [], "with_memory": []}
    for mode in reports:
        for task in suite.tasks:
            store = InMemoryFileStore() if mode == "with_memory" else None
            result = TaskResult(task["id"], False)
            for index, step in enumerate(task["steps"]):
                if provider == "fake":
                    llm: ModelProvider = FakeProvider(_script(task["script"][mode][index]))
                else:
                    llm = _OllamaFixedModel(OllamaProvider(base_url), model or "qwen3")
                last = index == len(task["steps"]) - 1
                outcome = await run_task(
                    {**task, "allowed": [], "expect": task["expect"] if last else {}, "id": task["id"]},  # pas d'outil concurrent
                    llm,
                    instruction=step["task"],
                    store=store,
                )
                if last:
                    result = outcome
            reports[mode].append(result)
    return (
        Report(suite.name, provider, reports["without_memory"], model),
        Report(suite.name, provider, reports["with_memory"], model),
    )


def write_memory_report(without: Report, with_memory: Report, path: Path) -> None:
    """Compare les taux de réussite avec et sans mémoire sur les mêmes tâches (SC-006 : +20 points)."""
    gain = (with_memory.success_rate - without.success_rate) * 100
    lines = [
        f"# Rapport d'évaluation de la mémoire : {without.suite}",
        "",
        f"- Fournisseur : {with_memory.provider}" + (f" (modèle {with_memory.model})" if with_memory.model else ""),
        f"- Sans mémoire : {without.success_rate * 100:.1f} %",
        f"- Avec mémoire : {with_memory.success_rate * 100:.1f} %",
        f"- Écart : {gain:+.1f} points",
        f"- SC-006 (au moins +20 points) : {'atteint' if gain >= 20 else 'NON atteint'}",
        "",
        "| Tâche | Sans mémoire | Avec mémoire |",
        "|---|---|---|",
        *(f"| {b.task_id} | {_mark(b.passed)} | {_mark(a.passed)} |" for b, a in zip(without.results, with_memory.results, strict=True)),
        "",
        "Détail des échecs avec mémoire : "
        + ("aucun" if all(r.passed for r in with_memory.results) else "; ".join(f"{r.task_id} ({'; '.join(r.failures)})" for r in with_memory.results if not r.passed)),
    ]
    path.write_text("\n".join(lines) + "\n")


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
    if suite.memory:
        without, with_memory = await run_memory_suite(suite, provider, args.model, args.ollama_url)
        out = Path(args.out) if args.out else EVALS_DIR / "reports" / f"{args.suite}.md"
        write_memory_report(without, with_memory, out)
        print(f"{suite.name} : sans mémoire {without.success_rate * 100:.1f} %, avec {with_memory.success_rate * 100:.1f} % -> {out}")
        return 0
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
