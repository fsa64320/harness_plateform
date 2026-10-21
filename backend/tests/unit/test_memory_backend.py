"""Backend de fichiers de l'agent : routes, droits et isolation (R-06, FR-006a, FR-017, FR-017a)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from deepagents.backends import CompositeBackend, StateBackend

from harness.db.models.agents import Agent
from harness.db.models.catalog import Tool
from harness.db.session import get_sessionmaker, group_context
from harness.memory.backends import READ_ONLY_ERROR, SqlBackend, build_run_backend
from tests.memory_helpers import seed_entry
from tests.support import ListSink


async def _agent(owner_session: Any, group_id: uuid.UUID, name: str = "a") -> uuid.UUID:
    async with owner_session() as s, s.begin():
        agent = Agent(
            group_id=group_id, name=name, instructions="x", model_id="fake",
            max_iterations=5, max_duration_seconds=30, max_tokens=1000,
        )
        s.add(agent)
        await s.flush()
        return agent.id


async def _skill(owner_session: Any, group_id: uuid.UUID) -> uuid.UUID:
    async with owner_session() as s, s.begin():
        tool = Tool(
            group_id=group_id, kind="skill", name="redaction", skill_name="redaction", skill_description="d",
            files={"SKILL.md": "---\nname: redaction\ndescription: d\n---\ncorps"},
        )
        s.add(tool)
        await s.flush()
        return tool.id


def backend_for(
    group_id: uuid.UUID, agent_id: uuid.UUID, *, agent_memory: bool = True, group_memory: bool = True,
    skills: list[uuid.UUID] | None = None, sink: ListSink | None = None,
) -> CompositeBackend:
    backend = build_run_backend(
        get_sessionmaker(), group_id=group_id, agent_id=agent_id, run_id=None, sink=sink,
        agent_memory=agent_memory, group_memory=group_memory, skill_tool_ids=skills or [],
    )
    assert isinstance(backend, CompositeBackend)
    return backend


async def test_routes_follow_the_agent_flags(make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    skill = await _skill(owner_session, group)

    full = backend_for(group, agent, skills=[skill])
    assert set(full.routes) == {"/memories/", "/group-memory/", "/skills/"}
    assert all(isinstance(b, SqlBackend) for b in full.routes.values())
    assert isinstance(full.default, StateBackend)  # les autres chemins : espace de travail éphémère

    only_agent = backend_for(group, agent, group_memory=False)
    assert set(only_agent.routes) == {"/memories/"}
    only_group = backend_for(group, agent, agent_memory=False)
    assert set(only_group.routes) == {"/group-memory/"}
    assert build_run_backend(
        get_sessionmaker(), group_id=group, agent_id=agent, run_id=None, sink=None,
        agent_memory=False, group_memory=False, skill_tool_ids=[],
    ) is None


async def test_memories_route_is_read_write_and_scoped_to_the_current_agent(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    mine, other = await _agent(owner_session, group, "mine"), await _agent(owner_session, group, "other")
    backend = backend_for(group, mine)

    written = await backend.awrite("/memories/notes/a.md", "préférence : tutoiement")
    assert written.error is None
    read = await backend.aread("/memories/notes/a.md")
    assert read.error is None and read.file_data is not None and "tutoiement" in read.file_data["content"]
    listing = await backend.als("/memories/notes/")
    assert [e["path"] for e in listing.entries or []] == ["/memories/notes/a.md"]

    # un autre agent du même groupe ne voit jamais cette mémoire
    foreign = await backend_for(group, other).aread("/memories/notes/a.md")
    assert foreign.error is not None and foreign.file_data is None
    assert (await backend_for(group, other).als("/memories/")).entries == []


async def test_group_memory_route_is_readable_but_rejects_every_write(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    await seed_entry(group, scope="group", path="faits/tarifs.md", content="12 €")
    backend = backend_for(group, agent)

    read = await backend.aread("/group-memory/faits/tarifs.md")
    assert read.file_data is not None and "12 €" in read.file_data["content"]
    assert [e["path"] for e in (await backend.als("/group-memory/faits/")).entries or []] == ["/group-memory/faits/tarifs.md"]
    assert (await backend.awrite("/group-memory/faits/x.md", "piraté")).error == READ_ONLY_ERROR
    assert (await backend.aedit("/group-memory/faits/tarifs.md", "12", "99")).error == READ_ONLY_ERROR
    assert (await backend.adelete("/group-memory/faits/tarifs.md")).error == READ_ONLY_ERROR
    again = await backend.aread("/group-memory/faits/tarifs.md")
    assert again.file_data is not None and "12 €" in again.file_data["content"]


async def test_skills_route_is_read_only(make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    skill = await _skill(owner_session, group)
    backend = backend_for(group, agent, skills=[skill])
    assert (await backend.aread("/skills/redaction/SKILL.md")).error is None
    assert (await backend.awrite("/skills/redaction/SKILL.md", "x")).error == READ_ONLY_ERROR
    assert (await backend.adelete("/skills/redaction/SKILL.md")).error == READ_ONLY_ERROR


async def test_a_read_under_another_group_returns_nothing(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group_a, group_b = await make_group(), await make_group()
    agent_a = await _agent(owner_session, group_a)
    await seed_entry(group_a, scope="agent", agent_id=agent_a, path="secret.md", content="confidentiel")
    await seed_entry(group_a, scope="group", path="faits/a.md", content="commun à A")

    # même identifiant d'agent, mais le contexte de groupe est celui de B : la RLS ne montre rien
    foreign = backend_for(group_b, agent_a)
    assert (await foreign.aread("/memories/secret.md")).error is not None
    assert (await foreign.als("/memories/")).entries == []
    assert (await foreign.als("/group-memory/faits/")).entries == []
    assert (await foreign.agrep("confidentiel", "/memories/")).matches == []


async def test_reads_and_writes_emit_trace_events_with_their_scope(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    agent = await _agent(owner_session, group)
    await seed_entry(group, scope="group", path="g.md", content="commun")
    sink = ListSink()
    backend = backend_for(group, agent, sink=sink)

    await backend.awrite("/memories/a.md", "un")
    await backend.aread("/memories/a.md")
    await backend.aedit("/memories/a.md", "un", "deux")
    await backend.aread("/group-memory/g.md")

    assert [(t, p.get("scope"), p["path"]) for t, p in sink.events] == [
        ("memory_written", "agent", "a.md"),
        ("memory_read", "agent", "a.md"),
        ("memory_written", "agent", "a.md"),
        ("memory_read", "group", "g.md"),
    ]
    assert sink.events[0][1]["change_type"] == "create" and sink.events[0][1]["version"] == 1
    assert sink.events[2][1]["change_type"] == "update" and sink.events[2][1]["version"] == 2


@pytest.mark.parametrize("bad", ["/memories/../x.md", "/memories//a.md", "/memories/" + "a" * 600])
async def test_unsafe_paths_are_refused(bad: str, make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any) -> None:
    group = await make_group()
    backend = backend_for(group, await _agent(owner_session, group))
    assert (await backend.awrite(bad, "x")).error is not None
    assert await _count(group) == 0


async def _count(group: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from harness.db.models.memory import MemoryEntry

    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group)
        return int((await s.execute(select(func.count()).select_from(MemoryEntry))).scalar_one())
