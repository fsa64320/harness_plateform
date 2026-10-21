"""Demandes d'approbation des appels à effet externe (FR-005, FR-005b, FR-003b).

Qui décide : le membre qui a lancé l'exécution, ou un membre ayant le rôle `approver` ; jamais un compte
de service. Quand aucune personne n'est habilitée (exécution d'un compte de service dans un groupe sans
approbateur), la demande est marquée `no_eligible_approver`, visible en lecture des administrateurs, et
expire au délai d'attente."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.auth.permissions import GroupAccess
from harness.db.models.identity import Group, Membership, MembershipStatus
from harness.db.models.runs import ApprovalRequest, ApprovalStatus, Run
from harness.traces.redaction import redact


class ApprovalNotFound(Exception):
    pass


class ApprovalForbidden(Exception):
    pass


class ApprovalConflict(Exception):
    """Déjà décidée ou expirée (réponse 409)."""


@dataclass(frozen=True)
class Decided:
    request: ApprovalRequest
    run_id: uuid.UUID
    resume_decisions: list[dict[str, Any]] | None  # non nul quand toutes les demandes de l'arrêt sont tranchées


async def has_eligible_approver(session: AsyncSession, group_id: uuid.UUID) -> bool:
    row = await session.execute(
        select(Membership.id)
        .where(
            Membership.group_id == group_id,
            Membership.status == MembershipStatus.ACTIVE,
            Membership.roles.contains(["approver"]),
        )
        .limit(1)
    )
    return row.first() is not None


async def create_requests(
    session: AsyncSession,
    *,
    run: Run,
    group: Group,
    interrupt_id: str,
    actions: Sequence[dict[str, Any]],
    call_ids: Sequence[str | None],
    secrets: Sequence[str] = (),
) -> list[ApprovalRequest]:
    """Une demande par appel en attente d'un même arrêt du graphe, dans l'ordre des décisions."""
    now = dt.datetime.now(dt.UTC)
    nobody = run.triggered_by_type == "service_account" and not await has_eligible_approver(session, group.id)
    requests: list[ApprovalRequest] = []
    for position, action in enumerate(actions):
        request = ApprovalRequest(
            group_id=group.id,
            run_id=run.id,
            call_id=call_ids[position] if position < len(call_ids) else None,
            interrupt_id=interrupt_id,
            position=position,
            tool_operation=action["name"],
            arguments=redact(action.get("args", {}), secrets),
            requested_at=now,
            expires_at=now + dt.timedelta(hours=group.approval_timeout_hours),
            status=ApprovalStatus.PENDING,
            no_eligible_approver=nobody,
        )
        session.add(request)
        requests.append(request)
    await session.flush()
    return requests


def _can_decide(run: Run, access: GroupAccess) -> bool:
    if access.is_service_account:
        return False
    launched_by_caller = run.triggered_by_type == "person" and run.triggered_by_id == access.principal.id
    return launched_by_caller or "approver" in access.roles


async def decide(
    session: AsyncSession, access: GroupAccess, approval_id: uuid.UUID, decision: str, comment: str | None
) -> Decided:
    """Enregistre la décision (verrouille la demande et l'exécution pour qu'elle ne soit prise qu'une fois)."""
    request = (
        await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update())
    ).scalar_one_or_none()
    if request is None:
        raise ApprovalNotFound(str(approval_id))
    run = (await session.execute(select(Run).where(Run.id == request.run_id).with_for_update())).scalar_one()
    if not _can_decide(run, access):
        raise ApprovalForbidden()
    if request.status != ApprovalStatus.PENDING or request.expires_at <= dt.datetime.now(dt.UTC):
        raise ApprovalConflict()

    request.status = ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
    request.decided_by = access.principal.id
    request.decided_at = dt.datetime.now(dt.UTC)
    request.comment = comment
    await session.flush()

    siblings = list(
        (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.run_id == run.id, ApprovalRequest.interrupt_id == request.interrupt_id)
                .order_by(ApprovalRequest.position)
            )
        ).scalars()
    )
    if any(s.status == ApprovalStatus.PENDING for s in siblings):
        return Decided(request, run.id, None)
    return Decided(request, run.id, [to_decision(s) for s in siblings])


def to_decision(request: ApprovalRequest) -> dict[str, Any]:
    if request.status == ApprovalStatus.APPROVED:
        return {"type": "approve"}
    return {"type": "reject", "message": request.comment or "Appel refusé par une personne habilitée."}


async def list_visible(
    session: AsyncSession, access: GroupAccess, status: str | None
) -> list[ApprovalRequest]:
    """Demandes que l'appelant peut traiter ; les administrateurs voient en plus celles sans
    approbateur éligible (lecture seule)."""
    query = select(ApprovalRequest).join(Run, Run.id == ApprovalRequest.run_id)
    if status:
        query = query.where(ApprovalRequest.status == status)
    rows = (await session.execute(query.order_by(ApprovalRequest.requested_at, ApprovalRequest.position))).scalars()
    visible: list[ApprovalRequest] = []
    runs: dict[uuid.UUID, Run] = {}
    for request in rows:
        run = runs.get(request.run_id) or await session.get(Run, request.run_id)
        assert run is not None
        runs[request.run_id] = run
        if _can_decide(run, access) or ("admin" in access.roles and request.no_eligible_approver):
            visible.append(request)
    return visible
