"""Flux SSE de la trace : latence < 2 s, reprise par Last-Event-ID, fermeture après run_ended (SC-002)."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio
import uvicorn

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


@pytest_asyncio.fixture
async def live_url(world: World) -> AsyncIterator[str]:
    """Serveur HTTP réel dans la boucle du test (le transport ASGI de httpx ne diffuse pas en flux)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(world.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}/api/v1"
    server.should_exit = True
    await task


async def _read_sse(
    url: str, headers: dict[str, str], *, stop_after: int | None = None
) -> list[tuple[dict[str, Any], float]]:
    """Lit un flux SSE ; renvoie (événement, secondes écoulées depuis `occurred_at`) pour chacun."""
    out: list[tuple[dict[str, Any], float]] = []
    async with httpx.AsyncClient(timeout=30) as c, c.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                event = json.loads("".join(data_lines))
                data_lines = []
                lag = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(event["occurred_at"])).total_seconds()
                out.append((event, lag))
                if stop_after is not None and len(out) >= stop_after:
                    break
    return out


async def test_each_event_is_delivered_within_two_seconds_and_the_stream_closes_at_run_ended(
    world: World, live_url: str
) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "x"}, delay=0.4), say("fin", delay=0.4))
    run = await world.launch(world.bob)

    received = await _read_sse(
        f"{live_url}/groups/{world.group_id}/runs/{run['id']}/events",
        {**world.bob.headers, "Accept": "text/event-stream"},
    )
    events = [e for e, _ in received]
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_ended"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert max(lag for _, lag in received) < 2.0


async def test_reconnecting_with_last_event_id_loses_nothing_and_duplicates_nothing(
    world: World, live_url: str
) -> None:
    world.models.set(
        tool_call("crm_lookup", {"query": "a"}, delay=0.5),
        tool_call("crm_lookup", {"query": "b"}, delay=0.5),
        say("fin", delay=0.5),
    )
    run = await world.launch(world.bob)
    url = f"{live_url}/groups/{world.group_id}/runs/{run['id']}/events"
    headers = {**world.bob.headers, "Accept": "text/event-stream"}

    first = [e for e, _ in await _read_sse(url, headers, stop_after=2)]
    assert [e["seq"] for e in first] == [1, 2]

    rest = [e for e, _ in await _read_sse(url, {**headers, "Last-Event-ID": "2"})]
    seqs = [e["seq"] for e in first + rest]
    assert seqs == list(range(1, len(seqs) + 1))
    assert rest[-1]["type"] == "run_ended"


async def test_sse_of_a_finished_run_replays_everything_then_closes(world: World, live_url: str) -> None:
    world.models.set(say("fini"))
    run = await world.launch(world.bob)
    await world.wait(run["id"])
    url = f"{live_url}/groups/{world.group_id}/runs/{run['id']}/events"
    events = [e for e, _ in await _read_sse(url, {**world.bob.headers, "Accept": "text/event-stream"})]
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_ended"
