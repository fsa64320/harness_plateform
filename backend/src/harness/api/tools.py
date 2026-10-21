"""Catalogue d'outils du groupe : serveurs MCP, skills et hooks (FR-005a, FR-010, FR-011, FR-014).

Les identifiants (identifiant MCP, secret de signature d'un webhook) sont en écriture seule : ils
n'apparaissent dans aucune réponse, seul `has_credential` indique qu'un identifiant est enregistré."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from harness.auth.permissions import GroupAccess, HumanMemberDep, require_roles
from harness.catalog import mcp, skills
from harness.catalog import service as catalog
from harness.catalog.credentials import store_credential
from harness.db.models.catalog import HookDefinition, Tool, ToolKind, ToolOperation
from harness.db.session import SessionDep
from harness.groups.audit import record_admin_action

router = APIRouter(prefix="/groups/{group_id}/tools", tags=["tools"])

ToolManagerDep = Annotated[GroupAccess, Depends(require_roles("tool_manager"))]
HookEvent = Literal["run_start", "run_end", "before_tool_call", "after_tool_call"]


class McpCreate(BaseModel):
    url: str
    credential: str | None = Field(default=None, repr=False)


class SkillCreate(BaseModel):
    files: dict[str, str]


class HookInput(BaseModel):
    events: list[HookEvent] = Field(min_length=1)
    hook_type: Literal["rule", "webhook"]
    priority: int = 100
    condition: dict[str, Any] | None = None
    decision: Literal["allow", "block", "log"] | None = None
    url: str | None = None
    signing_secret: str | None = Field(default=None, repr=False)
    timeout_seconds: int = Field(default=10, ge=1, le=10)


class ToolCreate(BaseModel):
    kind: ToolKind
    name: str = Field(min_length=1)
    description: str = ""
    mcp: McpCreate | None = None
    skill: SkillCreate | None = None
    hook: HookInput | None = None


class ClassificationInput(BaseModel):
    classification: str


def _invalid(message: str) -> HTTPException:
    return HTTPException(422, {"code": "invalid_tool", "message": message})


def operation_out(op: ToolOperation) -> dict[str, Any]:
    return {
        "id": str(op.id),
        "name": op.operation_name,
        "input_schema": op.input_schema,
        "classification": op.classification,
    }


def hook_out(hook: HookDefinition) -> dict[str, Any]:
    out = {
        "events": hook.events,
        "hook_type": hook.hook_type,
        "priority": hook.priority,
        "condition": hook.condition,
        "decision": hook.decision,
        "url": hook.url,
        "timeout_seconds": hook.timeout_seconds,
    }
    return {k: v for k, v in out.items() if v is not None}  # le contrat ne prévoit pas de null


async def tool_out(session: SessionDep, tool: Tool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(tool.id),
        "kind": tool.kind,
        "name": tool.name,
        "description": tool.description,
        "status": tool.status,
        "has_credential": tool.credential_id is not None,
        "operations": [],
    }
    if tool.kind == ToolKind.MCP_SERVER:
        operations = (await session.execute(select(ToolOperation).where(ToolOperation.tool_id == tool.id))).scalars()
        out["operations"] = [operation_out(o) for o in sorted(operations, key=lambda o: o.operation_name)]
    elif tool.kind == ToolKind.HOOK:
        hook = (await session.execute(select(HookDefinition).where(HookDefinition.tool_id == tool.id))).scalar_one()
        out["hook"] = hook_out(hook)
        out["has_credential"] = hook.signing_secret_id is not None
    return out


@router.get("")
async def list_tools(
    access: HumanMemberDep, session: SessionDep, kind: Annotated[ToolKind | None, Query()] = None
) -> list[dict[str, Any]]:
    query = select(Tool).where(Tool.status == "active").order_by(Tool.name)
    if kind:
        query = query.where(Tool.kind == kind)
    return [await tool_out(session, t) for t in (await session.execute(query)).scalars()]


async def _create_hook(session: SessionDep, access: GroupAccess, body: ToolCreate) -> Tool:
    hook = body.hook
    if hook is None:
        raise _invalid("Le champ hook est requis pour un hook")
    if hook.hook_type == "rule":
        if hook.decision is None:
            raise _invalid("Une règle exige une décision (allow, block ou log)")
    else:
        parts = urlsplit(hook.url or "")
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise _invalid("L'URL d'un hook webhook doit être en HTTPS, sans identifiants")
        if not hook.signing_secret:
            raise _invalid("Un hook webhook exige un secret de signature")
    existing = await session.execute(select(Tool.id).where(Tool.kind == ToolKind.HOOK, Tool.name == body.name))
    if existing.first() is not None:
        raise HTTPException(409, {"code": "conflict", "message": f"Un hook « {body.name} » existe déjà"})
    secret = await store_credential(session, access.group_id, hook.signing_secret) if hook.signing_secret else None
    tool = Tool(
        group_id=access.group_id,
        kind=ToolKind.HOOK,
        name=body.name,
        description=body.description,
        created_by=access.principal.id,
    )
    session.add(tool)
    await session.flush()
    session.add(
        HookDefinition(
            group_id=access.group_id,
            tool_id=tool.id,
            events=list(hook.events),
            hook_type=hook.hook_type,
            condition=hook.condition if hook.hook_type == "rule" else None,
            decision=hook.decision if hook.hook_type == "rule" else None,
            url=hook.url if hook.hook_type == "webhook" else None,
            signing_secret_id=secret.id if secret else None,
            timeout_seconds=hook.timeout_seconds,
            priority=hook.priority,
        )
    )
    await session.flush()
    await record_admin_action(
        session,
        group_id=access.group_id,
        actor_id=access.principal.id,
        action="tool.register",
        target_type="tool",
        target_id=tool.id,
        details={
            "kind": "hook",
            "name": body.name,
            "hook_type": hook.hook_type,
            "events": list(hook.events),
            "decision": hook.decision,
            "url": hook.url,
        },
    )
    return tool


@router.post("", status_code=201)
async def create_tool(body: ToolCreate, access: ToolManagerDep, session: SessionDep) -> dict[str, Any]:
    try:
        if body.kind == ToolKind.MCP_SERVER:
            if body.mcp is None:
                raise _invalid("Le champ mcp est requis pour un serveur MCP")
            tool = await mcp.register_mcp_server(
                session,
                group_id=access.group_id,
                actor_id=access.principal.id,
                name=body.name,
                description=body.description,
                url=body.mcp.url,
                credential=body.mcp.credential,
            )
        elif body.kind == ToolKind.SKILL:
            if body.skill is None:
                raise _invalid("Le champ skill est requis pour une skill")
            tool = await skills.register_skill(
                session,
                group_id=access.group_id,
                actor_id=access.principal.id,
                name=body.name,
                description=body.description,
                files=body.skill.files,
            )
        else:
            tool = await _create_hook(session, access, body)
    except (mcp.McpError, skills.SkillInvalid) as exc:
        raise _invalid(str(exc)) from exc
    except (mcp.ToolConflict, skills.SkillConflict) as exc:
        raise HTTPException(409, {"code": "conflict", "message": str(exc)}) from exc
    return await tool_out(session, tool)


@router.get("/{tool_id}")
async def get_tool(tool_id: uuid.UUID, access: HumanMemberDep, session: SessionDep) -> dict[str, Any]:
    try:
        return await tool_out(session, await catalog.get_tool(session, tool_id))
    except catalog.ToolNotFound as exc:
        raise HTTPException(404, "Outil introuvable") from exc


@router.delete("/{tool_id}", status_code=204)
async def retire_tool(tool_id: uuid.UUID, access: ToolManagerDep, session: SessionDep) -> Response:
    try:
        await catalog.retire_tool(session, group_id=access.group_id, actor_id=access.principal.id, tool_id=tool_id)
    except catalog.ToolNotFound as exc:
        raise HTTPException(404, "Outil introuvable") from exc
    return Response(status_code=204)


@router.put("/{tool_id}/operations/{operation_name}/classification")
async def classify(
    tool_id: uuid.UUID, operation_name: str, body: ClassificationInput, access: ToolManagerDep, session: SessionDep
) -> dict[str, Any]:
    try:
        operation = await mcp.classify_operation(
            session,
            group_id=access.group_id,
            actor_id=access.principal.id,
            tool_id=tool_id,
            operation_name=operation_name,
            classification=body.classification,
        )
    except mcp.ToolNotFound as exc:
        raise HTTPException(404, "Opération introuvable") from exc
    except mcp.McpError as exc:
        raise _invalid(str(exc)) from exc
    return operation_out(operation)
