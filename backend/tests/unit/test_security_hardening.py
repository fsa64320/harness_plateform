"""Corrections issues de la revue de sécurité (T128) : une régression par constat."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import HTTPException

from harness.auth.oidc import validate_claims
from harness.db.session import get_sessionmaker
from harness.memory import service as memory
from harness.memory.backends import build_run_backend
from harness.memory.service import Actor, MemoryTooLarge
from tests.support import ListSink


def test_oidc_claims_need_subject_and_email() -> None:
    assert validate_claims({"sub": "s1", "email": "Alice@Example.test", "name": "Alice"}) == ("s1", "alice@example.test", "Alice")
    for claims in ({}, {"sub": "s1"}, {"email": "a@example.test"}):
        with pytest.raises(HTTPException) as exc:
            validate_claims(claims)
        assert exc.value.status_code == 400


def test_an_unverified_email_is_rejected_so_an_invitation_cannot_be_hijacked() -> None:
    """Une invitation se rattache à une personne par son e-mail : un e-mail non vérifié par le fournisseur
    d'identité permettrait de se faire passer pour la personne invitée."""
    with pytest.raises(HTTPException) as exc:
        validate_claims({"sub": "s1", "email": "victime@example.test", "email_verified": False})
    assert exc.value.status_code == 403
    assert validate_claims({"sub": "s1", "email": "a@example.test", "email_verified": True})[1] == "a@example.test"


async def test_a_memory_entry_has_a_size_limit(make_group: Callable[..., Awaitable[uuid.UUID]], owner_session: Any) -> None:
    from harness.db.models.agents import Agent
    from harness.db.session import group_context

    group = await make_group()
    async with owner_session() as s, s.begin():
        agent = Agent(group_id=group, name="a", instructions="x", model_id="fake", max_iterations=5, max_duration_seconds=30, max_tokens=1000)
        s.add(agent)
        await s.flush()
        agent_id = agent.id
    huge = "x" * (memory.MAX_CONTENT_BYTES + 1)
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, group)
        with pytest.raises(MemoryTooLarge):
            await memory.write_entry(
                s, group_id=group, scope="agent", agent_id=agent_id, path="a.md", content=huge,
                expected_version=None, actor=Actor("agent", agent_id), source_run_id=None,
            )
    backend = build_run_backend(
        get_sessionmaker(), group_id=group, agent_id=agent_id, run_id=None, sink=ListSink(),
        agent_memory=True, group_memory=False, skill_tool_ids=[],
    )
    assert backend is not None
    result = await backend.awrite("/memories/a.md", huge)
    assert result.error is not None and "volumineux" in result.error
    assert (await backend.awrite("/memories/a.md", "petit")).error is None
