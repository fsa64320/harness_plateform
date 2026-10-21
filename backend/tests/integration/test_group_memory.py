"""Mémoire de groupe : alimentée seulement par des propositions validées (US3-6, US3-7, FR-017a, SC-010)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update

from harness.db.models.memory import GroupMemoryProposal
from harness.db.session import get_sessionmaker, group_context
from harness.runtime.models import say, tool_call
from tests.memory_helpers import entries_of
from tests.world_fixtures import World

FACT = {"path": "faits/tarifs.md", "content": "Tarif 2026 : 12 €"}


async def _proposals(world: World, status: str = "pending") -> list[dict[str, Any]]:
    r = await world.client.get(world.url(f"/group-memory/proposals?status={status}"), headers=world.bob.headers)
    return r.json()


async def _agent_with_group_memory(world: World) -> dict[str, Any]:
    return await world.create_agent(group_memory_enabled=True)


async def test_nothing_reaches_group_memory_before_acceptance(world: World) -> None:
    agent = await _agent_with_group_memory(world)
    world.models.set(tool_call("propose_group_memory", FACT), say("proposé"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"

    events = [e for e in await world.events(run["id"]) if e["type"] == "group_memory_proposed"]
    assert len(events) == 1 and events[0]["payload"]["path"] == "faits/tarifs.md"
    [proposal] = await _proposals(world)
    assert proposal["status"] == "pending" and proposal["run_id"] == run["id"] and proposal["agent_id"] == agent["id"]
    assert await entries_of(world.group_id, scope="group") == []  # SC-010 : zéro entrée sans validation

    r = await world.client.post(
        world.url(f"/group-memory/proposals/{proposal['id']}/decision"), json={"decision": "accept"}, headers=world.alice.headers
    )
    assert r.status_code == 200
    [entry] = await entries_of(world.group_id, scope="group")
    assert (entry.path, entry.content, entry.author_type) == ("faits/tarifs.md", "Tarif 2026 : 12 €", "person")
    assert entry.source_run_id == uuid.UUID(run["id"])  # l'origine reste tracée


async def test_agent_cannot_write_group_memory_directly(world: World) -> None:
    agent = await _agent_with_group_memory(world)
    world.models.set(
        tool_call("write_file", {"file_path": "/group-memory/faits/direct.md", "content": "piraté"}),
        tool_call("edit_file", {"file_path": "/group-memory/faits/direct.md", "old_string": "a", "new_string": "b"}),
        say("ok"),
    )
    run = await world.launch(agent_id=agent["id"])
    await world.wait(run["id"])
    assert await entries_of(world.group_id, scope="group") == []
    assert [e["type"] for e in await world.events(run["id"]) if e["type"] == "memory_written"] == []


async def test_an_accepted_entry_is_readable_by_another_agent_with_group_memory_enabled(world: World) -> None:
    proposer = await _agent_with_group_memory(world)
    world.models.set(tool_call("propose_group_memory", FACT), say("proposé"))
    await world.wait((await world.launch(agent_id=proposer["id"]))["id"])
    [proposal] = await _proposals(world)
    await world.client.post(
        world.url(f"/group-memory/proposals/{proposal['id']}/decision"), json={"decision": "accept"}, headers=world.alice.headers
    )

    reader = await _agent_with_group_memory(world)
    world.models.set(tool_call("read_file", {"file_path": "/group-memory/faits/tarifs.md"}), say("12 €"))
    run = await world.launch(agent_id=reader["id"])
    await world.wait(run["id"])
    read = [e["payload"] for e in await world.events(run["id"]) if e["type"] == "memory_read"]
    assert [(r["scope"], r["path"]) for r in read] == [("group", "faits/tarifs.md")]
    prompt = (await world.events(run["id"]))[0]["payload"]["system_prompt"]
    assert "/group-memory/" in prompt and "faits/tarifs.md" in prompt

    disabled = await world.create_agent(group_memory_enabled=False)
    world.models.set(tool_call("read_file", {"file_path": "/group-memory/faits/tarifs.md"}), say("rien"))
    other = await world.launch(agent_id=disabled["id"])
    await world.wait(other["id"])
    assert [e for e in await world.events(other["id"]) if e["type"] == "memory_read"] == []


async def test_an_agent_without_group_memory_cannot_propose(world: World) -> None:
    world.models.set(tool_call("propose_group_memory", FACT), say("ok"))
    run = await world.launch()  # group_memory_enabled = false par défaut
    await world.wait(run["id"])
    assert await _proposals(world) == []
    assert [e["payload"]["reason"] for e in await world.events(run["id"]) if e["type"] == "tool_call_refused"] == [
        "unknown_tool"
    ]


async def test_a_pending_proposal_is_never_used_by_agents(world: World) -> None:
    proposer = await _agent_with_group_memory(world)
    world.models.set(tool_call("propose_group_memory", FACT), say("proposé"))
    await world.wait((await world.launch(agent_id=proposer["id"]))["id"])
    world.models.set(tool_call("read_file", {"file_path": "/group-memory/faits/tarifs.md"}), say("rien"))
    run = await world.launch(agent_id=proposer["id"])
    await world.wait(run["id"])
    assert [e for e in await world.events(run["id"]) if e["type"] == "memory_read"] == []


async def test_proposals_older_than_the_expiry_become_expired_and_stay_unused(world: World, owner_session: Any) -> None:
    proposer = await _agent_with_group_memory(world)
    world.models.set(tool_call("propose_group_memory", FACT), say("proposé"))
    await world.wait((await world.launch(agent_id=proposer["id"]))["id"])
    fresh = (await _proposals(world))[0]["id"]

    world.models.set(tool_call("propose_group_memory", {"path": "faits/vieux.md", "content": "ancien"}), say("proposé"))
    await world.wait((await world.launch(agent_id=proposer["id"]))["id"])
    old = next(p["id"] for p in await _proposals(world) if p["id"] != fresh)
    async with owner_session() as s, s.begin():
        await s.execute(
            update(GroupMemoryProposal)
            .where(GroupMemoryProposal.id == uuid.UUID(old))
            .values(created_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=31))  # délai par défaut : 30 jours
        )

    await world.runner.expire_overdue_proposals()

    assert [p["id"] for p in await _proposals(world, "pending")] == [fresh]
    assert [p["id"] for p in await _proposals(world, "expired")] == [old]
    r = await world.client.post(
        world.url(f"/group-memory/proposals/{old}/decision"), json={"decision": "accept"}, headers=world.alice.headers
    )
    assert r.status_code == 409  # une proposition expirée ne peut plus être acceptée
    assert await entries_of(world.group_id, scope="group") == []


async def test_accepting_a_proposal_for_an_existing_path_versions_the_entry(world: World) -> None:
    proposer = await _agent_with_group_memory(world)
    for content in ("Tarif 2025 : 10 €", "Tarif 2026 : 12 €"):
        world.models.set(tool_call("propose_group_memory", {"path": "faits/tarifs.md", "content": content}), say("ok"))
        await world.wait((await world.launch(agent_id=proposer["id"]))["id"])
    for proposal in sorted(await _proposals(world), key=lambda p: p["proposed_content"]):
        await world.client.post(
            world.url(f"/group-memory/proposals/{proposal['id']}/decision"),
            json={"decision": "accept"},
            headers=world.alice.headers,
        )
    [entry] = await entries_of(world.group_id, scope="group")
    assert entry.version == 2 and entry.content == "Tarif 2026 : 12 €"
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        accepted = (await s.execute(select(GroupMemoryProposal).where(GroupMemoryProposal.status == "accepted"))).scalars().all()
    assert len(accepted) == 2
