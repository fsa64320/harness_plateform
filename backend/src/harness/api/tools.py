"""Catalogue d'outils du groupe : serveurs MCP et classification (FR-005a, FR-010, FR-011)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from harness.auth.permissions import GroupAccess, GroupMemberDep, require_roles
from harness.catalog import mcp
from harness.db.models.catalog import Tool, ToolKind, ToolOperation
from harness.db.session import SessionDep

router = APIRouter(prefix="/groups/{group_id}/tools", tags=["tools"])

ToolManagerDep = Annotated[GroupAccess, Depends(require_roles("tool_manager"))]


class McpCreate(BaseModel):
    url: str
    credential: str | None = Field(default=None, repr=False)


class ToolCreate(BaseModel):
    kind: ToolKind
    name: str = Field(min_length=1)
    description: str = ""
    mcp: McpCreate | None = None


class ClassificationInput(BaseModel):
    classification: str


def operation_out(op: ToolOperation) -> dict[str, Any]:
    return {
        "id": str(op.id),
        "name": op.operation_name,
        "input_schema": op.input_schema,
        "classification": op.classification,
    }


async def tool_out(session: SessionDep, tool: Tool) -> dict[str, Any]:
    operations = (
        (await session.execute(select(ToolOperation).where(ToolOperation.tool_id == tool.id)))
        .scalars()
        .all()
    )
    return {
        "id": str(tool.id),
        "kind": tool.kind,
        "name": tool.name,
        "description": tool.description,
        "status": tool.status,
        "has_credential": False,
        "operations": [operation_out(o) for o in sorted(operations, key=lambda o: o.operation_name)],
    }


@router.get("")
async def list_tools(
    access: GroupMemberDep, session: SessionDep, kind: Annotated[ToolKind | None, Query()] = None
) -> list[dict[str, Any]]:
    query = select(Tool).where(Tool.status == "active").order_by(Tool.name)
    if kind:
        query = query.where(Tool.kind == kind)
    return [await tool_out(session, t) for t in (await session.execute(query)).scalars()]


@router.post("", status_code=201)
async def create_tool(body: ToolCreate, access: ToolManagerDep, session: SessionDep) -> dict[str, Any]:
    if body.kind != ToolKind.MCP_SERVER:
        raise HTTPException(422, {"code": "invalid", "message": "Seuls les serveurs MCP sont gérés pour l'instant"})
    if body.mcp is None:
        raise HTTPException(422, {"code": "invalid", "message": "Le champ mcp est requis pour un serveur MCP"})
    try:
        tool = await mcp.register_mcp_server(
            session,
            group_id=access.group_id,
            actor_id=access.principal.id,
            name=body.name,
            description=body.description,
            url=body.mcp.url,
        )
    except mcp.McpError as exc:
        raise HTTPException(422, {"code": "invalid_tool", "message": str(exc)}) from exc
    except mcp.ToolConflict as exc:
        raise HTTPException(409, {"code": "conflict", "message": str(exc)}) from exc
    return await tool_out(session, tool)


@router.get("/{tool_id}")
async def get_tool(tool_id: uuid.UUID, access: GroupMemberDep, session: SessionDep) -> dict[str, Any]:
    tool = (await session.execute(select(Tool).where(Tool.id == tool_id))).scalar_one_or_none()
    if tool is None:
        raise HTTPException(404, "Outil introuvable")
    return await tool_out(session, tool)


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
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    return operation_out(operation)
