"""Aides de test pour la mémoire : entrées, propositions et acteurs, créés par les services réels."""

from __future__ import annotations

import uuid
from typing import Any

from harness.db.session import get_sessionmaker, group_context
from harness.memory import service
from harness.memory.service import Actor, WriteOutcome


def person(person_id: uuid.UUID) -> Actor:
    return Actor("person", person_id)


async def seed_entry(
    group_id: uuid.UUID,
    *,
    scope: str,
    path: str,
    content: str,
    agent_id: uuid.UUID | None = None,
    actor: Actor | None = None,
    expected_version: int | None = None,
    run_id: uuid.UUID | None = None,
) -> WriteOutcome:
    async with get_sessionmaker()() as session, session.begin():
        await group_context(session, group_id)
        return await service.write_entry(
            session,
            group_id=group_id,
            scope=scope,
            agent_id=agent_id,
            path=path,
            content=content,
            expected_version=expected_version,
            actor=actor or Actor("person", uuid.uuid4()),
            source_run_id=run_id,
        )


async def entries_of(group_id: uuid.UUID, *, scope: str, agent_id: uuid.UUID | None = None) -> list[Any]:
    async with get_sessionmaker()() as session, session.begin():
        await group_context(session, group_id)
        return await service.list_entries(session, scope=scope, agent_id=agent_id)


async def revisions_of(group_id: uuid.UUID, entry_id: uuid.UUID) -> list[Any]:
    async with get_sessionmaker()() as session, session.begin():
        await group_context(session, group_id)
        return await service.list_revisions(session, entry_id)
