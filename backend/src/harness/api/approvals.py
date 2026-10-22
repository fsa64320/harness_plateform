"""Demandes d'approbation : liste et décision (FR-005, FR-005b)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from harness.auth.permissions import GroupMemberDep, GroupSessionDep
from harness.db.models.runs import ApprovalRequest
from harness.runtime import approvals

router = APIRouter(prefix="/groups/{group_id}/approvals", tags=["approvals"])


class DecisionInput(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


def approval_out(a: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "run_id": str(a.run_id),
        "tool_operation": a.tool_operation,
        "arguments": a.arguments,
        "status": a.status,
        "requested_at": a.requested_at.isoformat().replace("+00:00", "Z"),
        "expires_at": a.expires_at.isoformat().replace("+00:00", "Z"),
        "no_eligible_approver": a.no_eligible_approver,
        "decided_by": str(a.decided_by) if a.decided_by else None,
    }


@router.get("")
async def list_approvals(
    access: GroupMemberDep,
    session: GroupSessionDep,
    status: Annotated[Literal["pending", "approved", "rejected", "expired"] | None, Query()] = None,
) -> list[dict[str, Any]]:
    return [approval_out(a) for a in await approvals.list_visible(session, access, status)]


@router.post("/{approval_id}/decision")
async def decide(approval_id: uuid.UUID, body: DecisionInput, request: Request, access: GroupMemberDep) -> dict[str, Any]:
    try:
        request_row = await request.app.state.runner.decide(access, approval_id, body.decision, body.comment)
    except approvals.ApprovalNotFound as exc:
        raise HTTPException(404, "Demande introuvable") from exc
    except approvals.ApprovalForbidden as exc:
        raise HTTPException(
            403, {"code": "forbidden", "message": "Seuls le déclencheur ou un approbateur peuvent décider"}
        ) from exc
    except approvals.ApprovalConflict as exc:
        raise HTTPException(409, {"code": "conflict", "message": "Demande déjà décidée ou expirée"}) from exc
    return approval_out(request_row)
