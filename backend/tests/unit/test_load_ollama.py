"""Mesure de charge Ollama : débit (tokens/s) et latence de N générations en parallèle (research R-10)."""

from __future__ import annotations

import httpx
from evals.load_ollama import LoadReport, render_markdown, run_load


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"eval_count": 50, "eval_duration": 2_000_000_000, "total_duration": 2_500_000_000, "response": "ok"},
    )


async def test_throughput_and_latency_are_aggregated_over_parallel_generations() -> None:
    transport = httpx.MockTransport(_handler)
    report = await run_load("qwen3", parallel=4, prompt="bonjour", num_predict=50, base_url="http://ollama", transport=transport)
    assert isinstance(report, LoadReport)
    assert report.parallel == 4 and report.failures == 0 and len(report.samples) == 4
    assert all(s.tokens == 50 and abs(s.tokens_per_second - 25.0) < 1e-6 for s in report.samples)
    assert abs(report.tokens_per_second_per_request - 25.0) < 1e-6
    assert abs(report.aggregate_tokens_per_second - report.total_tokens / report.wall_seconds) < 1e-6
    assert report.latency_p50 > 0 and report.latency_max >= report.latency_p50


async def test_failures_are_counted_and_do_not_hide_successes() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] % 2 else _handler(request)

    report = await run_load("qwen3", parallel=4, prompt="x", num_predict=10, base_url="http://ollama", transport=httpx.MockTransport(flaky))
    assert report.failures == 2 and len(report.samples) == 2


def test_markdown_report_states_the_verdict_for_twenty_concurrent_runs() -> None:
    import asyncio

    report = asyncio.run(
        run_load("qwen3", parallel=2, prompt="x", num_predict=50, base_url="http://ollama", transport=httpx.MockTransport(_handler))
    )
    text = render_markdown([report], model="qwen3", host="http://ollama")
    assert text.startswith("# Charge Ollama")
    assert "| 2 |" in text and "qwen3" in text and "OLLAMA_NUM_PARALLEL" in text
