"""Écritures concurrentes sur une même entrée : aucune écriture n'est perdue (cas limite de la spec)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from harness.db.models.agents import Agent
from harness.db.session import get_sessionmaker
from harness.memory.backends import build_run_backend
from tests.memory_helpers import entries_of, person, revisions_of, seed_entry
from tests.support import ListSink


async def _agent(owner_session: Any, group_id: uuid.UUID) -> uuid.UUID:
    async with owner_session() as s, s.begin():
        agent = Agent(
            group_id=group_id, name="a", instructions="x", model_id="fake",
            max_iterations=5, max_duration_seconds=30, max_tokens=1000,
        )
        s.add(agent)
        await s.flush()
        return agent.id


def run_backend(group: uuid.UUID, agent: uuid.UUID, sink: ListSink) -> Any:
    return build_run_backend(
        get_sessionmaker(), group_id=group, agent_id=agent, run_id=None, sink=sink,
        agent_memory=True, group_memory=False, skill_tool_ids=[],
    )


async def test_the_second_of_two_concurrent_writes_gets_a_conflict_and_nothing_is_lost(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    await seed_entry(group, scope="agent", agent_id=agent, path="clients.md", content="Acme : 10 €", actor=person(uuid.uuid4()))

    sink_1, sink_2 = ListSink(), ListSink()
    run_1, run_2 = run_backend(group, agent, sink_1), run_backend(group, agent, sink_2)
    # les deux exécutions lisent la version 1 avant d'écrire
    await run_1.aread("/memories/clients.md")
    await run_2.aread("/memories/clients.md")

    first = await run_1.aedit("/memories/clients.md", "10 €", "12 €")
    second = await run_2.aedit("/memories/clients.md", "10 €", "15 €")

    assert first.error is None
    assert second.error is not None and "conflit" in second.error.lower()
    assert ("memory_conflict", {"path": "clients.md", "expected_version": 1, "actual_version": 2}) in sink_2.events

    [entry] = await entries_of(group, scope="agent", agent_id=agent)
    assert entry.content == "Acme : 12 €" and entry.version == 2  # la première écriture est conservée
    revisions = await revisions_of(group, entry.id)
    assert [(r.version, r.change_type) for r in revisions] == [(1, "create"), (2, "update"), (2, "conflict")]
    conflict = revisions[-1]
    assert "15 €" in (conflict.new_content or "")  # l'écriture refusée reste visible dans l'historique


async def test_a_write_by_an_execution_that_never_read_the_entry_is_applied_and_versioned(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    await seed_entry(group, scope="agent", agent_id=agent, path="a.md", content="v1")
    sink = ListSink()
    result = await run_backend(group, agent, sink).awrite("/memories/a.md", "v2")
    assert result.error is None
    [entry] = await entries_of(group, scope="agent", agent_id=agent)
    assert entry.version == 2 and entry.content == "v2"
    assert [(r.version, r.change_type) for r in await revisions_of(group, entry.id)] == [(1, "create"), (2, "update")]


async def test_after_a_conflict_the_execution_can_reread_and_retry(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    await seed_entry(group, scope="agent", agent_id=agent, path="a.md", content="base")
    loser, winner = run_backend(group, agent, ListSink()), run_backend(group, agent, ListSink())
    await loser.aread("/memories/a.md")
    await winner.aread("/memories/a.md")
    assert (await winner.aedit("/memories/a.md", "base", "gagnant")).error is None
    assert (await loser.aedit("/memories/a.md", "gagnant", "perdant")).error is not None

    await loser.aread("/memories/a.md")  # il relit la version courante…
    retry = await loser.aedit("/memories/a.md", "gagnant", "fusion")  # …et réessaie
    assert retry.error is None
    [entry] = await entries_of(group, scope="agent", agent_id=agent)
    assert entry.content == "fusion" and entry.version == 3
