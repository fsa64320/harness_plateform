"""Propositions de mémoire de groupe : un agent propose, un gestionnaire valide (FR-017a, SC-010).

Seule une proposition `accepted` écrit dans la mémoire de groupe, par le service de mémoire ; l'outil de
l'agent ne fait que créer une proposition en attente. Elle expire après `Group.proposal_expiry_days`."""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import AsyncIterator

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harness.auth.permissions import GroupAccess
from harness.db.models.identity import Group
from harness.db.models.memory import GroupMemoryProposal, MemoryScope, ProposalStatus
from harness.db.session import group_context
from harness.memory import service as memory
from harness.memory.service import Actor
from harness.runtime.context import EventSink

TOOL_NAME = "propose_group_memory"


class ProposalNotFound(Exception):
    pass


class ProposalConflict(Exception):
    """Déjà décidée ou expirée (réponse 409)."""


def make_propose_tool(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    run_id: uuid.UUID | None,
    sink: EventSink,
) -> BaseTool:
    @contextlib.asynccontextmanager
    async def txn() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session, session.begin():
            await group_context(session, group_id)
            yield session

    async def propose_group_memory(path: str, content: str) -> str:
        """Propose une information utile à tout le groupe pour la mémoire de groupe."""
        try:
            key = memory.normalize_path(path)
        except memory.MemoryPathInvalid as exc:
            return f"Proposition refusée : {exc}"
        async with txn() as session:
            proposal = GroupMemoryProposal(
                group_id=group_id, run_id=run_id, agent_id=agent_id, proposed_path=key,
                proposed_content=content, status=ProposalStatus.PENDING,
            )
            session.add(proposal)
            await session.flush()
            proposal_id = proposal.id
        await sink.emit("group_memory_proposed", {"proposal_id": str(proposal_id), "path": key})
        return (
            "Proposition enregistrée : elle n'entrera dans la mémoire de groupe qu'après validation par "
            "un gestionnaire de la mémoire de groupe."
        )

    return StructuredTool.from_function(
        coroutine=propose_group_memory,
        name=TOOL_NAME,
        description=(
            "Propose une entrée pour la mémoire de groupe (faits communs à tous les agents du groupe). "
            "La proposition reste en attente tant qu'une personne habilitée ne l'a pas validée."
        ),
    )


async def decide(
    session: AsyncSession, access: GroupAccess, proposal_id: uuid.UUID, decision: str
) -> GroupMemoryProposal:
    proposal = (
        await session.execute(select(GroupMemoryProposal).where(GroupMemoryProposal.id == proposal_id).with_for_update())
    ).scalar_one_or_none()
    if proposal is None:
        raise ProposalNotFound(str(proposal_id))
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalConflict()
    if decision == "accept":
        await memory.write_entry(
            session,
            group_id=access.group_id,
            scope=MemoryScope.GROUP,
            agent_id=None,
            path=proposal.proposed_path,
            content=proposal.proposed_content,
            expected_version=None,
            actor=Actor("person", access.principal.id),
            source_run_id=proposal.run_id,
        )
        proposal.status = ProposalStatus.ACCEPTED
    else:
        proposal.status = ProposalStatus.REJECTED
    proposal.decided_by = access.principal.id
    proposal.decided_at = dt.datetime.now(dt.UTC)
    await session.flush()
    return proposal


async def list_proposals(session: AsyncSession, status: str | None) -> list[GroupMemoryProposal]:
    query = select(GroupMemoryProposal).order_by(GroupMemoryProposal.created_at, GroupMemoryProposal.id)
    if status:
        query = query.where(GroupMemoryProposal.status == status)
    return list((await session.execute(query)).scalars())


async def expire_overdue(session: AsyncSession, group: Group) -> int:
    """Les propositions en attente depuis plus de `proposal_expiry_days` expirent et ne servent jamais."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=group.proposal_expiry_days)
    rows = (
        await session.execute(
            select(GroupMemoryProposal)
            .where(GroupMemoryProposal.status == ProposalStatus.PENDING, GroupMemoryProposal.created_at <= cutoff)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for proposal in rows:
        proposal.status = ProposalStatus.EXPIRED
    return len(rows)
