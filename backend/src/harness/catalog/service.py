"""Cycle de vie du catalogue : retrait d'un outil et signalement des agents concernés (US2-4)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harness.db.models.agents import Agent
from harness.db.models.catalog import Tool, ToolOperation, ToolStatus
from harness.groups.audit import record_admin_action


class ToolNotFound(Exception):
    pass


async def get_tool(session: AsyncSession, tool_id: uuid.UUID) -> Tool:
    tool = (await session.execute(select(Tool).where(Tool.id == tool_id))).scalar_one_or_none()
    if tool is None:
        raise ToolNotFound(str(tool_id))
    return tool


async def retire_tool(session: AsyncSession, *, group_id: uuid.UUID, actor_id: uuid.UUID, tool_id: uuid.UUID) -> Tool:
    """Retire l'outil du catalogue (il n'est plus lié à aucune exécution) et marque « configuration
    modifiée » les agents qui l'autorisaient, lui ou l'une de ses opérations."""
    tool = await get_tool(session, tool_id)
    if tool.status == ToolStatus.RETIRED:
        return tool
    tool.status = ToolStatus.RETIRED
    operation_ids = (await session.execute(select(ToolOperation.id).where(ToolOperation.tool_id == tool_id))).scalars()
    ids = [str(tool_id), *map(str, operation_ids)]
    await session.execute(
        update(Agent)
        .where(or_(*(Agent.allowed_tool_ids.contains([i]) for i in ids)))
        .values(config_changed_at=dt.datetime.now(dt.UTC))
    )
    await session.flush()
    await record_admin_action(
        session,
        group_id=group_id,
        actor_id=actor_id,
        action="tool.retire",
        target_type="tool",
        target_id=tool.id,
        details={"kind": tool.kind, "name": tool.name},
    )
    return tool
