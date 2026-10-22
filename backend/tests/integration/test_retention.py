"""Rétention : les événements de trace et les révisions de mémoire plus vieux que le réglage du groupe
sont purgés (assumption de la spec : 90 jours par défaut, réglable)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, text, update

from harness.db.models.identity import Group
from harness.db.models.memory import MemoryEntry, MemoryRevision
from harness.db.models.runs import Run, TraceEvent
from harness.db.session import get_sessionmaker, group_context
from harness.runtime.models import say
from tests.memory_helpers import person, seed_entry
from tests.world_fixtures import World


def ago(days: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


async def _age_trace(owner_session: Any, run_id: str, days: float) -> None:
    async with owner_session() as s, s.begin():
        await s.execute(update(TraceEvent).where(TraceEvent.run_id == uuid.UUID(run_id)).values(occurred_at=ago(days)))


async def _event_count(group_id: uuid.UUID, run_id: str) -> int:
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group_id)
        return len((await s.execute(select(TraceEvent.id).where(TraceEvent.run_id == uuid.UUID(run_id)))).scalars().all())


async def test_trace_events_older_than_the_retention_are_purged_but_runs_remain(world: World, owner_session: Any) -> None:
    world.models.set(say("ancien"))
    old = await world.launch()
    await world.wait(old["id"])
    world.models.set(say("récent"))
    recent = await world.launch()
    await world.wait(recent["id"])
    await _age_trace(owner_session, old["id"], 100)  # au-delà des 90 jours par défaut
    await _age_trace(owner_session, recent["id"], 10)

    await world.runner.purge_expired()

    assert await _event_count(world.group_id, old["id"]) == 0
    assert await _event_count(world.group_id, recent["id"]) > 0
    assert (await world.run(old["id"]))["status"] == "succeeded"  # la liste des exécutions reste consultable
    assert (await world.run(old["id"]))["result"] == "ancien"


async def test_the_retention_is_a_per_group_setting(world: World, owner_session: Any, make_group: Any, login_as: Any) -> None:
    world.models.set(say("a"))
    run = await world.launch()
    await world.wait(run["id"])
    await _age_trace(owner_session, run["id"], 40)
    async with owner_session() as s, s.begin():
        await s.execute(update(Group).where(Group.id == world.group_id).values(trace_retention_days=30))
    other = await make_group("autre", trace_retention_days=60)
    async with owner_session() as s, s.begin():  # trace du même âge, mais dans un groupe qui garde 60 jours
        await s.execute(
            text(
                "INSERT INTO runs (id, group_id, task, status, triggered_by_type, triggered_by_id, launch_mode, thread_id) "
                "VALUES (:id, :g, 't', 'succeeded', 'person', :p, 'manual', 't')"
            ),
            {"id": (rid := uuid.uuid4()), "g": other, "p": uuid.uuid4()},
        )
        await s.execute(
            text(
                "INSERT INTO trace_events (id, group_id, run_id, seq, occurred_at, type, payload) "
                "VALUES (:id, :g, :r, 1, :t, 'run_started', '{}')"
            ),
            {"id": uuid.uuid4(), "g": other, "r": rid, "t": ago(40)},
        )

    await world.runner.purge_expired()

    assert await _event_count(world.group_id, run["id"]) == 0  # 40 j > 30 j
    assert await _event_count(other, str(rid)) == 1  # 40 j < 60 j : conservé


async def test_memory_revisions_older_than_the_retention_are_purged_but_entries_remain(world: World, owner_session: Any) -> None:
    outcome = await seed_entry(world.group_id, scope="group", path="faits/a.md", content="v1", actor=person(world.alice.person_id))
    await seed_entry(world.group_id, scope="group", path="faits/a.md", content="v2", actor=person(world.alice.person_id))
    async with owner_session() as s, s.begin():
        await s.execute(
            update(MemoryRevision).where(MemoryRevision.version == 1).values(changed_at=ago(100))
        )

    await world.runner.purge_expired()

    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        versions = (await s.execute(select(MemoryRevision.version).where(MemoryRevision.memory_entry_id == outcome.entry.id))).scalars().all()
        entry = (await s.execute(select(MemoryEntry).where(MemoryEntry.id == outcome.entry.id))).scalar_one()
    assert versions == [2]  # l'historique ancien est purgé
    assert entry.content == "v2" and entry.version == 2  # mais l'entrée elle-même est conservée


async def test_purge_is_idempotent_and_does_nothing_when_nothing_is_old(world: World) -> None:
    world.models.set(say("a"))
    run = await world.launch()
    await world.wait(run["id"])
    before = await _event_count(world.group_id, run["id"])
    await world.runner.purge_expired()
    await world.runner.purge_expired()
    assert await _event_count(world.group_id, run["id"]) == before
    assert Run.__tablename__ == "runs"
