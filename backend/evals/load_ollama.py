"""Mesure la charge supportée par l'hôte Ollama cible : débit (tokens/s) et latence de N générations
en parallèle. Sert à dimensionner le matériel, ou à décider d'un serveur plus adapté (vLLM) derrière la
même interface de fournisseur, si SC-007 (20 exécutions simultanées) n'est pas tenu (research R-10).

    uv run python -m evals.load_ollama --model qwen3 --parallel 1 2 4 8 --out evals/reports/load-ollama.md
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_PROMPT = "Explique en quelques phrases ce qu'est un agent logiciel et à quoi servent ses outils."


@dataclass
class Sample:
    tokens: int
    seconds: float  # durée de génération côté Ollama
    latency: float  # durée vue du client, file d'attente comprise

    @property
    def tokens_per_second(self) -> float:
        return self.tokens / self.seconds if self.seconds else 0.0


@dataclass
class LoadReport:
    parallel: int
    wall_seconds: float
    samples: list[Sample] = field(default_factory=list)
    failures: int = 0

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.samples)

    @property
    def aggregate_tokens_per_second(self) -> float:
        return self.total_tokens / self.wall_seconds if self.wall_seconds else 0.0

    @property
    def tokens_per_second_per_request(self) -> float:
        return statistics.fmean(s.tokens_per_second for s in self.samples) if self.samples else 0.0

    @property
    def latency_p50(self) -> float:
        return statistics.median(s.latency for s in self.samples) if self.samples else 0.0

    @property
    def latency_max(self) -> float:
        return max((s.latency for s in self.samples), default=0.0)


async def _generate(
    client: httpx.AsyncClient, base_url: str, model: str, prompt: str, num_predict: int
) -> Sample | None:
    started = time.monotonic()
    try:
        r = await client.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": num_predict}},
        )
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return Sample(
        tokens=int(body.get("eval_count", 0)),
        seconds=int(body.get("eval_duration", 0)) / 1e9,
        latency=time.monotonic() - started,
    )


async def run_load(
    model: str,
    *,
    parallel: int,
    prompt: str = DEFAULT_PROMPT,
    num_predict: int = 128,
    base_url: str = "http://localhost:11434",
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoadReport:
    async with httpx.AsyncClient(timeout=600, transport=transport) as client:
        started = time.monotonic()
        results = await asyncio.gather(*(_generate(client, base_url, model, prompt, num_predict) for _ in range(parallel)))
        wall = time.monotonic() - started
    samples = [r for r in results if r is not None]
    return LoadReport(parallel, wall, samples, failures=len(results) - len(samples))


def render_markdown(reports: list[LoadReport], *, model: str, host: str) -> str:
    lines = [
        "# Charge Ollama",
        "",
        f"- Hôte : {host}",
        f"- Modèle : {model}",
        "",
        "| Générations en parallèle | Débit par génération (tokens/s) | Débit total (tokens/s) | Latence p50 (s) | Latence max (s) | Échecs |",
        "|---|---|---|---|---|---|",
        *(
            f"| {r.parallel} | {r.tokens_per_second_per_request:.1f} | {r.aggregate_tokens_per_second:.1f} | "
            f"{r.latency_p50:.1f} | {r.latency_max:.1f} | {r.failures} |"
            for r in reports
        ),
        "",
        "Lecture : SC-007 demande 20 exécutions simultanées par groupe. Le serveur Ollama ne traite en parallèle "
        "que `OLLAMA_NUM_PARALLEL` générations (les autres attendent) : si la latence max à 20 générations est "
        "trop haute pour l'usage visé, augmenter ce réglage et la mémoire GPU, ou remplacer le serveur de "
        "modèle (vLLM) derrière la même interface `ModelProvider`.",
    ]
    return "\n".join(lines) + "\n"


async def _main(args: argparse.Namespace) -> int:
    reports = []
    for n in args.parallel:
        report = await run_load(
            args.model, parallel=n, prompt=args.prompt, num_predict=args.num_predict, base_url=args.ollama_url
        )
        print(
            f"{n:>3} en parallèle : {report.tokens_per_second_per_request:.1f} tokens/s par génération, "
            f"{report.aggregate_tokens_per_second:.1f} au total, latence max {report.latency_max:.1f}s, "
            f"{report.failures} échec(s)"
        )
        reports.append(report)
    if args.out:
        Path(args.out).write_text(render_markdown(reports, model=args.model, host=args.ollama_url))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.load_ollama")
    parser.add_argument("--model", required=True)
    parser.add_argument("--parallel", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--num-predict", type=int, default=128)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--out")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
