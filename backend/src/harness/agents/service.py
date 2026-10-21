"""Définitions d'agents : création, modification, duplication, suppression (FR-001, FR-001a)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.agents.schemas import AgentInput
from harness.auth.permissions import GroupAccess
from harness.db.models.agents import Agent
from harness.db.models.catalog import Tool, ToolKind, ToolOperation, ToolStatus
from harness.runtime.models import ModelProvider, ModelProviderError


class AgentInvalid(Exception):
    """Configuration d'agent refusée (réponse 422)."""


class AgentForbidden(Exception):
    """Seuls l'auteur et les administrateurs modifient ou suppriment un agent (FR-001a)."""


class AgentNotFound(Exception):
    pass


def can_modify(agent: Agent, access: GroupAccess) -> bool:
    return access.principal.id == agent.created_by or "admin" in access.roles


async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise AgentNotFound(str(agent_id))
    return agent


async def list_agents(session: AsyncSession) -> list[Agent]:
    return list((await session.execute(select(Agent).order_by(Agent.name, Agent.id))).scalars())


async def _validate(
    session: AsyncSession, provider: ModelProvider, data: AgentInput, *, check_model: bool
) -> None:
    if check_model:
        try:
            supported = await provider.check_tool_calling(data.model_id)
        except ModelProviderError as exc:
            raise AgentInvalid(f"Modèle « {data.model_id} » inutilisable : {exc}") from exc
        if not supported:
            raise AgentInvalid(f"Le modèle « {data.model_id} » ne gère pas l'appel d'outils")
    wanted = set(data.allowed_tool_ids)
    if not wanted:
        return
    # La RLS limite ces requêtes au groupe : un id d'un autre groupe est simplement introuvable.
    tool_ids = set(
        (
            await session.execute(
                select(Tool.id).where(Tool.id.in_(wanted), Tool.status == ToolStatus.ACTIVE)
            )
        ).scalars()
    )
    op_ids = set(
        (
            await session.execute(
                select(ToolOperation.id)
                .join(Tool, Tool.id == ToolOperation.tool_id)
                .where(ToolOperation.id.in_(wanted), Tool.status == ToolStatus.ACTIVE)
            )
        ).scalars()
    )
    missing = wanted - tool_ids - op_ids
    if missing:
        raise AgentInvalid("Outil hors catalogue du groupe ou retiré : " + ", ".join(sorted(map(str, missing))))


def _apply(agent: Agent, data: AgentInput) -> None:
    agent.name = data.name
    agent.instructions = data.instructions
    agent.model_id = data.model_id
    agent.allowed_tool_ids = [str(i) for i in data.allowed_tool_ids]
    agent.agent_memory_enabled = data.agent_memory_enabled
    agent.group_memory_enabled = data.group_memory_enabled
    agent.subagents_enabled = data.subagents_enabled
    agent.max_iterations = data.limits.max_iterations
    agent.max_duration_seconds = data.limits.max_duration_seconds
    agent.max_tokens = data.limits.max_tokens


async def create_agent(
    session: AsyncSession, access: GroupAccess, provider: ModelProvider, data: AgentInput
) -> Agent:
    await _validate(session, provider, data, check_model=True)
    agent = Agent(group_id=access.group_id, created_by=access.principal.id)
    _apply(agent, data)
    session.add(agent)
    await session.flush()
    return agent


async def update_agent(
    session: AsyncSession, access: GroupAccess, provider: ModelProvider, agent_id: uuid.UUID, data: AgentInput
) -> Agent:
    agent = await get_agent(session, agent_id)
    if not can_modify(agent, access):
        raise AgentForbidden()
    await _validate(session, provider, data, check_model=data.model_id != agent.model_id)
    _apply(agent, data)
    await session.flush()
    return agent


async def delete_agent(session: AsyncSession, access: GroupAccess, agent_id: uuid.UUID) -> None:
    agent = await get_agent(session, agent_id)
    if not can_modify(agent, access):
        raise AgentForbidden()
    await session.delete(agent)
    await session.flush()


async def duplicate_agent(session: AsyncSession, access: GroupAccess, agent_id: uuid.UUID) -> Agent:
    """Copie la configuration ; la copie démarre avec une mémoire d'agent vide (aucune entrée copiée)."""
    original = await get_agent(session, agent_id)
    copy = Agent(
        group_id=access.group_id,
        created_by=access.principal.id,
        name=f"{original.name} (copie)",
        instructions=original.instructions,
        model_id=original.model_id,
        allowed_tool_ids=list(original.allowed_tool_ids),
        agent_memory_enabled=original.agent_memory_enabled,
        group_memory_enabled=original.group_memory_enabled,
        subagents_enabled=original.subagents_enabled,
        max_iterations=original.max_iterations,
        max_duration_seconds=original.max_duration_seconds,
        max_tokens=original.max_tokens,
    )
    session.add(copy)
    await session.flush()
    return copy


async def build_snapshot(session: AsyncSession, agent: Agent) -> dict[str, Any]:
    """Configuration figée au lancement : l'agent et, pour chaque outil autorisé, son état actuel."""
    wanted = {uuid.UUID(i) for i in agent.allowed_tool_ids}
    ops = (
        (
            await session.execute(
                select(ToolOperation).where(ToolOperation.id.in_(wanted) | ToolOperation.tool_id.in_(wanted))
            )
        )
        .scalars()
        .all()
    )
    tool_ids = wanted | {o.tool_id for o in ops}
    tools = (
        (await session.execute(select(Tool).where(Tool.id.in_(tool_ids)).order_by(Tool.name))).scalars().all()
        if tool_ids
        else []
    )
    by_tool: dict[uuid.UUID, list[ToolOperation]] = {}
    for op in ops:
        by_tool.setdefault(op.tool_id, []).append(op)
    return {
        "agent_id": str(agent.id),
        "name": agent.name,
        "instructions": agent.instructions,
        "model_id": agent.model_id,
        "allowed_tool_ids": list(agent.allowed_tool_ids),
        "agent_memory_enabled": agent.agent_memory_enabled,
        "group_memory_enabled": agent.group_memory_enabled,
        "subagents_enabled": agent.subagents_enabled,
        "limits": {
            "max_iterations": agent.max_iterations,
            "max_duration_seconds": agent.max_duration_seconds,
            "max_tokens": agent.max_tokens,
        },
        "tools": [
            {
                "id": str(t.id),
                "kind": t.kind,
                "name": t.name,
                "status": t.status,
                "url": t.url,
                "operations": [
                    {"id": str(o.id), "name": o.operation_name, "classification": o.classification}
                    for o in sorted(by_tool.get(t.id, []), key=lambda o: o.operation_name)
                ]
                if t.kind == ToolKind.MCP_SERVER
                else [],
            }
            for t in tools
        ],
    }
