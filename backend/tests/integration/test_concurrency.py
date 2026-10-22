"""20 exécutions simultanées dans un groupe (SC-007) : chaque étape visible en moins de 2 s (SC-002) et
arrêt en moins de 5 s dans 99 % des cas (SC-003). Le modèle est simulé : on mesure la plateforme."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from typing import Any

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World

N = 20


async def _raise_quota(world: World) -> None:
    members = (await world.client.get(world.url("/members"), headers=world.alice.headers)).json()
    mid = next(m["id"] for m in members if m["person_email"] == "bob@example.test")
    r = await world.client.patch(world.url(f"/members/{mid}"), json={"max_concurrent_runs": 50}, headers=world.alice.headers)
    assert r.status_code == 200


async def _launch_all(world: World, n: int) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(world.launch(world.bob, task=f"tâche {i}") for i in range(n))))


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct))]


async def test_twenty_concurrent_runs_complete_and_every_step_is_visible_within_two_seconds(world: World) -> None:
    await _raise_quota(world)
    world.models.per_run(
        lambda: [
            tool_call("crm_lookup", {"query": "a"}, delay=0.3),
            tool_call("crm_lookup", {"query": "b"}, delay=0.3),
            say("fini", delay=0.3),
        ]
    )
    lags: list[float] = []

    async def watch(run_id: uuid.UUID) -> None:
        with world.app.state.trace_bus.subscribe(run_id) as queue:
            while True:
                record = await queue.get()
                lags.append((dt.datetime.now(dt.UTC) - record.occurred_at).total_seconds())
                if record.type == "run_ended":
                    return

    # les abonnements précèdent le lancement : on connaît les identifiants avant de démarrer
    original_launch = world.runner.launch
    watchers: list[asyncio.Task[None]] = []

    async def launch_and_watch(**kwargs: Any) -> Any:
        run = await original_launch(**kwargs)
        watchers.append(asyncio.create_task(watch(run.id)))
        return run

    world.runner.launch = launch_and_watch  # type: ignore[method-assign]
    started = time.monotonic()
    runs = await _launch_all(world, N)
    results = await asyncio.gather(*(world.wait(r["id"], timeout=60) for r in runs))
    await asyncio.wait_for(asyncio.gather(*watchers), 10)

    assert [r["status"] for r in results] == ["succeeded"] * N
    assert len({r["id"] for r in runs}) == N
    assert time.monotonic() - started < 30
    assert len(lags) >= N * 3  # au moins les étapes de chaque exécution
    assert _percentile(lags, 0.99) < 2.0, f"latence p99 = {_percentile(lags, 0.99):.2f}s"

    for run in runs:  # traces complètes, sans trou ni mélange entre exécutions
        events = await world.events(run["id"])
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert {e["run_id"] for e in events} == {run["id"]}
        assert events[0]["payload"]["task"] == run["task"]
    assert len([c for c in world.mcp.calls if c[0] == "lookup"]) == 2 * N


async def test_twenty_concurrent_runs_stop_within_five_seconds(world: World) -> None:
    await _raise_quota(world)
    world.models.per_run(lambda: [tool_call("crm_wait", {"seconds": 30}), say("jamais")])
    runs = await _launch_all(world, N)
    await asyncio.gather(*(world.wait_for_event(r["id"], "tool_call_started", timeout=20) for r in runs))

    async def stop(run: dict[str, Any]) -> float:
        started = time.monotonic()
        r = await world.client.post(world.url(f"/runs/{run['id']}/stop"), headers=world.bob.headers)
        assert r.status_code == 202
        await asyncio.wait_for(world.runner.wait(uuid.UUID(run["id"])), 30)
        return time.monotonic() - started

    delays = await asyncio.gather(*(stop(r) for r in runs))
    assert sum(d < 5.0 for d in delays) / N >= 0.99, f"arrêts : max {max(delays):.2f}s"
    statuses = [(await world.run(r["id"]))["status"] for r in runs]
    assert statuses == ["stopped"] * N


async def test_the_global_limit_queues_the_excess_instead_of_dropping_runs(world: World) -> None:
    await _raise_quota(world)
    world.runner._semaphore = asyncio.Semaphore(5)  # noqa: SLF001 - limite globale réduite pour le test
    world.models.per_run(lambda: [tool_call("crm_lookup", {"query": "x"}, delay=0.2), say("fini")])
    runs = await _launch_all(world, 12)
    results = await asyncio.gather(*(world.wait(r["id"], timeout=60) for r in runs))
    assert [r["status"] for r in results] == ["succeeded"] * 12
