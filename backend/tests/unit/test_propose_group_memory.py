"""Outil `propose_group_memory` : propose sans jamais écrire dans la mémoire de groupe (FR-017a, SC-010)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from harness.db.models.agents import Agent
from harness.db.models.memory import GroupMemoryProposal, MemoryEntry
from harness.db.session import get_sessionmaker, group_context
from harness.memory.proposals import make_propose_tool
from tests.support import ListSink


async def test_the_tool_creates_a_pending_proposal_and_emits_an_event_without_writing_group_memory(
    make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any
) -> None:
    group = await make_group()
    async with owner_session() as s, s.begin():
        agent = Agent(
            group_id=group, name="a", instructions="x", model_id="fake",
            max_iterations=5, max_duration_seconds=30, max_tokens=1000,
        )
        s.add(agent)
        await s.flush()
        agent_id = agent.id
    sink = ListSink()
    tool = make_propose_tool(get_sessionmaker(), group_id=group, agent_id=agent_id, run_id=None, sink=sink)

    assert tool.name == "propose_group_memory"
    result = await tool.ainvoke({"path": "faits/tarifs.md", "content": "Tarif 2026 : 12 €"})
    assert "validation" in str(result)

    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group)
        [proposal] = (await s.execute(select(GroupMemoryProposal))).scalars().all()
        entries = (await s.execute(select(MemoryEntry))).scalars().all()
    assert proposal.status == "pending" and proposal.agent_id == agent_id
    assert (proposal.proposed_path, proposal.proposed_content) == ("faits/tarifs.md", "Tarif 2026 : 12 €")
    assert entries == []  # rien dans la mémoire de groupe tant que personne n'a validé
    assert sink.events == [("group_memory_proposed", {"proposal_id": str(proposal.id), "path": "faits/tarifs.md"})]


async def test_the_tool_refuses_an_unsafe_path(make_group: Callable[..., Awaitable[uuid.UUID]]) -> None:
    group = await make_group()
    tool = make_propose_tool(get_sessionmaker(), group_id=group, agent_id=uuid.uuid4(), run_id=None, sink=ListSink())
    result = await tool.ainvoke({"path": "../evasion.md", "content": "x"})
    assert "invalide" in str(result).lower()
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group)
        assert (await s.execute(select(GroupMemoryProposal))).scalars().all() == []
