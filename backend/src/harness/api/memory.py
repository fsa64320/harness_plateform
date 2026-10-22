"""Mémoire d'agent et de groupe : consultation, correction, historique, propositions (FR-015 à FR-017a)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from harness.agents import service as agents
from harness.auth.permissions import GroupAccess, GroupSessionDep, HumanMemberDep, require_roles
from harness.db.models.agents import Agent
from harness.db.models.memory import GroupMemoryProposal, MemoryEntry, MemoryRevision, MemoryScope
from harness.memory import proposals
from harness.memory import service as memory
from harness.memory.service import Actor

router = APIRouter(prefix="/groups/{group_id}", tags=["memory"])

ManagerDep = Annotated[GroupAccess, Depends(require_roles("group_memory_manager"))]


class EntryInput(BaseModel):
    path: str = Field(min_length=1)
    content: str


class EntryUpdate(BaseModel):
    content: str
    version: int


class ProposalDecision(BaseModel):
    decision: Literal["accept", "reject"]


def _iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def entry_out(e: MemoryEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "scope": e.scope,
        "agent_id": str(e.agent_id) if e.agent_id else None,
        "path": e.path,
        "content": e.content,
        "version": e.version,
        "source_run_id": str(e.source_run_id) if e.source_run_id else None,
        "author_type": e.author_type,
        "updated_at": _iso(e.updated_at),
    }


def revision_out(r: MemoryRevision) -> dict[str, Any]:
    return {
        "version": r.version,
        "previous_content": r.previous_content,
        "new_content": r.new_content,
        "change_type": r.change_type,
        "changed_by": r.changed_by,
        "changed_at": _iso(r.changed_at),
    }


def proposal_out(p: GroupMemoryProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "run_id": str(p.run_id) if p.run_id else None,
        "agent_id": str(p.agent_id) if p.agent_id else None,
        "proposed_path": p.proposed_path,
        "proposed_content": p.proposed_content,
        "status": p.status,
        "decided_by": str(p.decided_by) if p.decided_by else None,
    }


def _invalid(exc: Exception) -> HTTPException:
    return HTTPException(422, {"code": "invalid_path", "message": str(exc)})


def _forbidden(message: str) -> HTTPException:
    return HTTPException(403, {"code": "forbidden", "message": message})


async def _authorize_edit(session: AsyncSession, entry: MemoryEntry, access: GroupAccess) -> None:
    """FR-016 : mémoire d'agent -> auteur de l'agent ou administrateur ; mémoire de groupe -> gestionnaire."""
    if entry.scope == MemoryScope.GROUP:
        if "group_memory_manager" not in access.roles:
            raise _forbidden("Réservé aux gestionnaires de la mémoire de groupe")
        return
    agent = await session.get(Agent, entry.agent_id) if entry.agent_id else None
    if agent is None or not agents.can_modify(agent, access):
        raise _forbidden("Réservé à l'auteur de l'agent ou aux administrateurs")


async def _entry(session: AsyncSession, entry_id: uuid.UUID) -> MemoryEntry:
    try:
        return await memory.get_entry(session, entry_id)
    except memory.MemoryEntryNotFound as exc:
        raise HTTPException(404, "Entrée introuvable") from exc


@router.get("/agents/{agent_id}/memory")
async def agent_memory(agent_id: uuid.UUID, access: HumanMemberDep, session: GroupSessionDep) -> list[dict[str, Any]]:
    try:
        await agents.get_agent(session, agent_id)
    except agents.AgentNotFound as exc:
        raise HTTPException(404, "Agent introuvable") from exc
    return [entry_out(e) for e in await memory.list_entries(session, scope=MemoryScope.AGENT, agent_id=agent_id)]


@router.get("/group-memory")
async def group_memory(access: HumanMemberDep, session: GroupSessionDep) -> list[dict[str, Any]]:
    return [entry_out(e) for e in await memory.list_entries(session, scope=MemoryScope.GROUP)]


@router.post("/group-memory", status_code=201)
async def add_group_entry(body: EntryInput, access: ManagerDep, session: GroupSessionDep) -> dict[str, Any]:
    try:
        path = memory.normalize_path(body.path)
    except memory.MemoryPathInvalid as exc:
        raise _invalid(exc) from exc
    existing = await memory.find_entry(session, scope=MemoryScope.GROUP, agent_id=None, path=path)
    if existing is not None and existing.deleted_at is None:
        raise HTTPException(409, {"code": "conflict", "message": "Une entrée existe déjà à ce chemin : la modifier"})
    try:
        outcome = await memory.write_entry(
            session, group_id=access.group_id, scope=MemoryScope.GROUP, agent_id=None, path=path, content=body.content,
            expected_version=None, actor=Actor("person", access.principal.id), source_run_id=None,
        )
    except memory.MemoryTooLarge as exc:
        raise _invalid(exc) from exc
    return entry_out(outcome.entry)


@router.put("/memory/{entry_id}")
async def update_entry(entry_id: uuid.UUID, body: EntryUpdate, access: HumanMemberDep, session: GroupSessionDep) -> Any:
    entry = await _entry(session, entry_id)
    await _authorize_edit(session, entry, access)
    try:
        outcome = await memory.write_entry(
            session, group_id=access.group_id, scope=entry.scope, agent_id=entry.agent_id, path=entry.path,
            content=body.content, expected_version=body.version, actor=Actor("person", access.principal.id),
            source_run_id=entry.source_run_id,
        )
    except memory.MemoryTooLarge as exc:
        raise _invalid(exc) from exc
    if outcome.conflict:
        # Réponse (et non exception) : la transaction est validée et la tentative refusée reste dans
        # l'historique de l'entrée.
        return JSONResponse(
            status_code=409,
            content={
                "code": "version_conflict",
                "message": f"L'entrée est en version {outcome.actual_version}, pas {outcome.expected_version}",
            },
        )
    return entry_out(outcome.entry)


@router.delete("/memory/{entry_id}", status_code=204)
async def delete_entry(entry_id: uuid.UUID, access: HumanMemberDep, session: GroupSessionDep) -> Response:
    entry = await _entry(session, entry_id)
    await _authorize_edit(session, entry, access)
    await memory.delete_entry(session, entry, Actor("person", access.principal.id))
    return Response(status_code=204)


@router.get("/memory/{entry_id}/revisions")
async def revisions(entry_id: uuid.UUID, access: HumanMemberDep, session: GroupSessionDep) -> list[dict[str, Any]]:
    # l'historique reste lisible après une suppression, mais une entrée d'un autre groupe est introuvable
    try:
        await memory.get_entry(session, entry_id, include_deleted=True)
    except memory.MemoryEntryNotFound as exc:
        raise HTTPException(404, "Entrée introuvable") from exc
    return [revision_out(r) for r in await memory.list_revisions(session, entry_id)]


@router.get("/group-memory/proposals")
async def list_proposals(
    access: HumanMemberDep,
    session: GroupSessionDep,
    status: Annotated[Literal["pending", "accepted", "rejected", "expired"] | None, Query()] = None,
) -> list[dict[str, Any]]:
    return [proposal_out(p) for p in await proposals.list_proposals(session, status)]


@router.post("/group-memory/proposals/{proposal_id}/decision")
async def decide_proposal(
    proposal_id: uuid.UUID, body: ProposalDecision, access: ManagerDep, session: GroupSessionDep
) -> dict[str, Any]:
    try:
        proposal = await proposals.decide(session, access, proposal_id, body.decision)
    except proposals.ProposalNotFound as exc:
        raise HTTPException(404, "Proposition introuvable") from exc
    except proposals.ProposalConflict as exc:
        raise HTTPException(409, {"code": "conflict", "message": "Proposition déjà décidée ou expirée"}) from exc
    return proposal_out(proposal)
