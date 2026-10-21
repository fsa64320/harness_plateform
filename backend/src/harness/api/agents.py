"""Agents du groupe : tout membre consulte, lance et duplique ; l'auteur et les admins modifient
(FR-001, FR-001a)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from harness.agents import service
from harness.agents.schemas import AgentInput
from harness.auth.permissions import HumanMemberDep
from harness.db.models.agents import Agent, agent_limits
from harness.db.session import SessionDep

router = APIRouter(prefix="/groups/{group_id}/agents", tags=["agents"])


def agent_out(agent: Agent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "instructions": agent.instructions,
        "model_id": agent.model_id,
        "allowed_tool_ids": agent.allowed_tool_ids,
        "agent_memory_enabled": agent.agent_memory_enabled,
        "group_memory_enabled": agent.group_memory_enabled,
        "subagents_enabled": agent.subagents_enabled,
        "limits": agent_limits(agent),
        "created_by": str(agent.created_by) if agent.created_by else None,
        "config_changed_at": agent.config_changed_at.isoformat() if agent.config_changed_at else None,
    }


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, service.AgentNotFound):
        return HTTPException(404, "Agent introuvable")
    if isinstance(exc, service.AgentForbidden):
        return HTTPException(403, {"code": "forbidden", "message": "Réservé à l'auteur de l'agent ou aux administrateurs"})
    return HTTPException(422, {"code": "invalid_agent", "message": str(exc)})


@router.get("")
async def list_agents(access: HumanMemberDep, session: SessionDep) -> list[dict[str, Any]]:
    return [agent_out(a) for a in await service.list_agents(session)]


@router.post("", status_code=201)
async def create_agent(
    body: AgentInput, request: Request, access: HumanMemberDep, session: SessionDep
) -> dict[str, Any]:
    try:
        agent = await service.create_agent(session, access, request.app.state.model_provider, body)
    except service.AgentInvalid as exc:
        raise _errors(exc) from exc
    return agent_out(agent)


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, access: HumanMemberDep, session: SessionDep) -> dict[str, Any]:
    try:
        return agent_out(await service.get_agent(session, agent_id))
    except service.AgentNotFound as exc:
        raise _errors(exc) from exc


@router.put("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, body: AgentInput, request: Request, access: HumanMemberDep, session: SessionDep
) -> dict[str, Any]:
    try:
        agent = await service.update_agent(session, access, request.app.state.model_provider, agent_id, body)
    except (service.AgentNotFound, service.AgentForbidden, service.AgentInvalid) as exc:
        raise _errors(exc) from exc
    return agent_out(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, access: HumanMemberDep, session: SessionDep) -> Response:
    try:
        await service.delete_agent(session, access, agent_id)
    except (service.AgentNotFound, service.AgentForbidden) as exc:
        raise _errors(exc) from exc
    return Response(status_code=204)


@router.post("/{agent_id}/duplicate", status_code=201)
async def duplicate_agent(agent_id: uuid.UUID, access: HumanMemberDep, session: SessionDep) -> dict[str, Any]:
    try:
        return agent_out(await service.duplicate_agent(session, access, agent_id))
    except service.AgentNotFound as exc:
        raise _errors(exc) from exc
