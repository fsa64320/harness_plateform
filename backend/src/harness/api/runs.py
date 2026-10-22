"""Exécutions : lancement, suivi, arrêt, trace en JSON ou en flux SSE (FR-002, FR-002b, FR-004, FR-008)."""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from harness.auth.permissions import GroupAccess, GroupMemberDep, GroupSessionDep
from harness.db.models.runs import TERMINAL_STATUSES, Run, TraceEvent
from harness.db.session import get_sessionmaker, group_context
from harness.runtime import runner as runner_module
from harness.traces.writer import TraceRecord

router = APIRouter(prefix="/groups/{group_id}/runs", tags=["runs"])

DEFAULT_PAGE, MAX_PAGE = 50, 200
EVENTS_PAGE = 1000


class RunCreate(BaseModel):
    agent_id: uuid.UUID
    task: str = Field(min_length=1)
    relaunch_of: uuid.UUID | None = None


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def run_out(run: Run) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "task": run.task,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "triggered_by_type": run.triggered_by_type,
        "triggered_by_id": str(run.triggered_by_id),
        "launch_mode": run.launch_mode,
        "relaunched_from": str(run.relaunched_from) if run.relaunched_from else None,
        "iterations": run.iterations,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost": float(run.cost),
        "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at),
        "result": run.result,
    }


def _encode_cursor(run: Run) -> str:
    return base64.urlsafe_b64encode(f"{run.created_at.isoformat()}|{run.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        stamp, run_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return dt.datetime.fromisoformat(stamp), uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid", "message": "Curseur invalide"}) from exc


async def _get_run(session: AsyncSession, run_id: uuid.UUID, access: GroupAccess) -> Run:
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None or (access.is_service_account and run.triggered_by_id != access.principal.id):
        raise HTTPException(404, "Exécution introuvable")
    return run


@router.get("")
async def list_runs(
    access: GroupMemberDep,
    session: GroupSessionDep,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    triggered_by: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: Annotated[dt.datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> dict[str, Any]:
    query = select(Run).order_by(Run.created_at.desc(), Run.id.desc())
    if access.is_service_account:
        query = query.where(Run.triggered_by_id == access.principal.id)
    if agent_id:
        query = query.where(Run.agent_id == agent_id)
    if triggered_by:
        query = query.where(Run.triggered_by_id == triggered_by)
    if status:
        query = query.where(Run.status == status)
    if from_:
        query = query.where(Run.created_at >= from_)
    if to:
        query = query.where(Run.created_at <= to)
    if cursor:
        stamp, last_id = _decode_cursor(cursor)
        query = query.where((Run.created_at < stamp) | ((Run.created_at == stamp) & (Run.id < last_id)))
    rows = list((await session.execute(query.limit(limit + 1))).scalars())
    page = rows[:limit]
    return {
        "items": [run_out(r) for r in page],
        "next_cursor": _encode_cursor(page[-1]) if len(rows) > limit else None,
    }


@router.post("", status_code=202)
async def launch(body: RunCreate, request: Request, access: GroupMemberDep) -> dict[str, Any]:
    try:
        run = await request.app.state.runner.launch(
            group_id=access.group_id,
            agent_id=body.agent_id,
            task=body.task,
            principal=access.principal,
            relaunch_of=body.relaunch_of,
        )
    except runner_module.RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except runner_module.LaunchForbidden as exc:
        raise HTTPException(403, {"code": "forbidden", "message": str(exc)}) from exc
    except runner_module.RelaunchInvalid as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    except runner_module.QuotaExceeded as exc:
        raise HTTPException(429, {"code": "quota_exceeded", "message": str(exc)}) from exc
    return run_out(run)


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, access: GroupMemberDep, session: GroupSessionDep) -> dict[str, Any]:
    return run_out(await _get_run(session, run_id, access))


@router.post("/{run_id}/stop", status_code=202)
async def stop_run(run_id: uuid.UUID, request: Request, access: GroupMemberDep) -> Response:
    # le lanceur vérifie l'existence (et, pour un compte de service, la propriété) dans sa propre transaction
    try:
        await request.app.state.runner.stop(
            access.group_id, run_id, only_triggered_by=access.principal.id if access.is_service_account else None
        )
    except runner_module.RunNotFound as exc:
        raise HTTPException(404, "Exécution introuvable") from exc
    return Response(status_code=202)


async def _events_after(group_id: uuid.UUID, run_id: uuid.UUID, after: int, limit: int) -> list[TraceRecord]:
    async with get_sessionmaker()() as session, session.begin():
        await group_context(session, group_id)
        rows = (
            await session.execute(
                select(TraceEvent)
                .where(TraceEvent.run_id == run_id, TraceEvent.seq > after)
                .order_by(TraceEvent.seq)
                .limit(limit)
            )
        ).scalars()
        return [TraceRecord(e.run_id, e.group_id, e.seq, e.type, e.occurred_at, e.payload) for e in rows]


def _sse(record: TraceRecord) -> dict[str, str]:
    return {"id": str(record.seq), "event": record.type, "data": json.dumps(record.to_api(), ensure_ascii=False)}


async def _stream(request: Request, group_id: uuid.UUID, run_id: uuid.UUID, last: int) -> AsyncIterator[dict[str, str]]:
    """Historique puis événements en direct, sans perte ni doublon ; se termine après `run_ended`."""
    bus = request.app.state.trace_bus
    with bus.subscribe(run_id) as queue:  # abonné avant de lire la base : aucun trou possible
        for record in await _events_after(group_id, run_id, last, EVENTS_PAGE * 100):
            yield _sse(record)
            last = record.seq
            if record.type == "run_ended":
                return
        async with get_sessionmaker()() as session, session.begin():
            await group_context(session, group_id)
            status = (await session.execute(select(Run.status).where(Run.id == run_id))).scalar_one()
        if status in TERMINAL_STATUSES:
            return
        while True:
            record = await queue.get()
            if record.seq <= last:
                continue
            yield _sse(record)
            last = record.seq
            if record.type == "run_ended":
                return


@router.get("/{run_id}/events", response_model=None)
async def events(
    run_id: uuid.UUID,
    request: Request,
    access: GroupMemberDep,
    session: GroupSessionDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
    accept: Annotated[str, Header()] = "application/json",
) -> Any:
    await _get_run(session, run_id, access)
    if "text/event-stream" in accept:
        try:
            last = int(last_event_id) if last_event_id else after_seq
        except ValueError:
            last = after_seq
        return EventSourceResponse(_stream(request, access.group_id, run_id, last), ping=15)
    records = await _events_after(access.group_id, run_id, after_seq, EVENTS_PAGE)
    return [r.to_api() for r in records]
