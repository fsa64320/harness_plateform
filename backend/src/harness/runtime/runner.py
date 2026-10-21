"""Lancement, suivi, arrêt, reprise et récupération des exécutions (FR-002 à FR-005, FR-003a/b).

Chaque exécution est une tâche asyncio du processus backend (research R-10). L'état durable vit en base
(`Run`) et dans le checkpointer LangGraph : une exécution en attente d'approbation survit à un
redémarrage, une exécution en cours est marquée `interrupted` au démarrage et ne reprend jamais seule.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harness.agents import service as agents
from harness.auth.permissions import Principal
from harness.catalog import credentials
from harness.catalog.hook_rules import HookEventData
from harness.catalog.hooks import HookRunner
from harness.catalog.mcp import load_runtime_tools
from harness.db.models.identity import Group, Membership, ServiceAccount
from harness.db.models.runs import TERMINAL_STATUSES, ApprovalRequest, ApprovalStatus, Run, RunStatus
from harness.db.session import group_context, platform_context
from harness.memory import proposals
from harness.memory import service as memory_service
from harness.memory.backends import build_run_backend
from harness.runtime import approvals
from harness.runtime.builder import (
    allowed_skills,
    build_agent,
    compose_system_prompt,
    hooks_from_snapshot,
    make_run_context,
    memory_section,
)
from harness.runtime.context import RunContext
from harness.runtime.models import ModelProvider
from harness.traces.redaction import redact
from harness.traces.writer import RunEventSink, TraceWriter, message_text

log = structlog.get_logger()


class RunnerError(Exception):
    """Erreur métier du lanceur, traduite en réponse HTTP par la couche API."""


class RunNotFound(RunnerError):
    pass


class LaunchForbidden(RunnerError):
    pass


class RelaunchInvalid(RunnerError):
    pass


class QuotaExceeded(RunnerError):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Runner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
        checkpointer: Any,
        writer: TraceWriter,
        *,
        global_limit: int = 50,
        check_interval: float = 30.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.provider = provider
        self.checkpointer = checkpointer
        self.writer = writer
        self._semaphore = asyncio.Semaphore(global_limit)
        self._check_interval = check_interval
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stop_requested: set[uuid.UUID] = set()
        self._abandoning = False
        self._background: asyncio.Task[None] | None = None

    # --- accès base ------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _txn(self, group_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await group_context(session, group_id)
            yield session

    async def _group_ids(self) -> list[uuid.UUID]:
        async with self._sessionmaker() as session, session.begin():
            await platform_context(session)
            return list((await session.execute(select(Group.id))).scalars())

    async def emit(self, group_id: uuid.UUID, run_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> None:
        await self.writer.emit(group_id=group_id, run_id=run_id, type=type_, payload=payload)

    # --- suivi des tâches -----------------------------------------------------------------

    def is_active(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def wait(self, run_id: uuid.UUID) -> None:
        """Attend la fin du segment en cours (utile aux tests et à l'arrêt propre)."""
        while (task := self._tasks.get(run_id)) is not None and not task.done():
            await asyncio.wait([task])  # une reprise lancée par ce segment est suivie à son tour

    async def abandon_all(self) -> None:
        """Mort brutale simulée : annule toutes les tâches sans rien écrire (le démarrage suivant
        marque les exécutions interrompues)."""
        self._abandoning = True
        if self._background is not None:
            self._background.cancel()
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._background is not None:
            await asyncio.gather(self._background, return_exceptions=True)
            self._background = None

    # --- lancement ------------------------------------------------------------------------

    async def launch(
        self,
        *,
        group_id: uuid.UUID,
        agent_id: uuid.UUID,
        task: str,
        principal: Principal,
        relaunch_of: uuid.UUID | None = None,
    ) -> Run:
        async with self._txn(group_id) as session:
            try:
                agent = await agents.get_agent(session, agent_id)
            except agents.AgentNotFound as exc:
                raise RunNotFound("Agent introuvable") from exc
            if principal.kind == "service_account" and agent_id not in principal.allowed_agent_ids:
                raise LaunchForbidden("Ce compte de service n'a pas accès à cet agent")
            if relaunch_of is not None:
                previous = await session.get(Run, relaunch_of)
                if previous is None or previous.status != RunStatus.INTERRUPTED:
                    raise RelaunchInvalid("Seule une exécution interrompue peut être relancée")
            snapshot = await agents.build_snapshot(session, agent)
            refusal = await self._quota_refusal(session, principal, group_id)
            run = Run(
                group_id=group_id,
                agent_id=agent.id,
                agent_snapshot=snapshot,
                task=task,
                status=RunStatus.QUEUED,
                triggered_by_type=principal.kind,
                triggered_by_id=principal.id,
                launch_mode="manual" if principal.kind == "person" else "programmatic",
                thread_id=str(uuid.uuid4()),
                relaunched_from=relaunch_of,
            )
            if refusal is not None:  # refusée mais consignée : « rejected_quota », avec son motif
                run.status, run.stop_reason, run.ended_at = RunStatus.REJECTED_QUOTA, refusal[0], _utcnow()
            session.add(run)
            await session.flush()
        if refusal is not None:
            await self.emit(
                group_id, run.id, "run_ended",
                {"status": "rejected_quota", "stop_reason": refusal[0], "iterations": 0, "input_tokens": 0,
                 "output_tokens": 0, "cost": 0.0, "duration_ms": 0, "result": refusal[1]},
            )
            raise QuotaExceeded(refusal[1])
        self._spawn(run.id, group_id, resume=None)
        return run

    async def _quota_refusal(
        self, session: AsyncSession, principal: Principal, group_id: uuid.UUID
    ) -> tuple[str, str] | None:
        """Quotas par membre et par compte de service (FR-023) : `(motif, message)` si dépassé."""
        if principal.kind == "service_account":
            account = await session.get(ServiceAccount, principal.id)
            if account is None:
                return None
            max_concurrent, max_tokens = account.max_concurrent_runs, account.max_tokens_per_day
        else:
            membership = (
                await session.execute(
                    select(Membership).where(Membership.person_id == principal.id, Membership.status == "active")
                )
            ).scalar_one_or_none()
            if membership is None:
                return None
            max_concurrent, max_tokens = membership.max_concurrent_runs, membership.max_tokens_per_day
        active = (
            await session.execute(
                select(func.count()).select_from(Run).where(
                    Run.triggered_by_id == principal.id,
                    Run.status.not_in([s.value for s in TERMINAL_STATUSES]),
                )
            )
        ).scalar_one()
        if active >= max_concurrent:
            return (
                "max_concurrent_runs",
                f"Quota atteint : {active} exécutions simultanées sur {max_concurrent} autorisées.",
            )
        midnight = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        used = (
            await session.execute(
                select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0)).where(
                    Run.triggered_by_id == principal.id, Run.created_at >= midnight
                )
            )
        ).scalar_one()
        if used >= max_tokens:
            return ("max_tokens_per_day", f"Quota atteint : {used} tokens consommés aujourd'hui sur {max_tokens} autorisés.")
        return None

    def _spawn(
        self, run_id: uuid.UUID, group_id: uuid.UUID, *, resume: list[dict[str, Any]] | None
    ) -> asyncio.Task[None]:
        previous = self._tasks.get(run_id)
        task = asyncio.create_task(self._execute(run_id, group_id, resume, previous))
        self._tasks[run_id] = task
        return task

    async def _execute(
        self,
        run_id: uuid.UUID,
        group_id: uuid.UUID,
        resume: list[dict[str, Any]] | None,
        previous: asyncio.Task[None] | None,
    ) -> None:
        if previous is not None and not previous.done():
            await asyncio.wait([previous])  # le segment précédent finit d'écrire avant la reprise
        try:
            async with self._semaphore:
                await self._drive(run_id, group_id, resume)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("run_crashed", run_id=str(run_id))
            with contextlib.suppress(Exception):
                await self._end_run(group_id, run_id, RunStatus.FAILED, "error", "Erreur interne de la plateforme")

    # --- cœur ------------------------------------------------------------------------------

    async def _drive(self, run_id: uuid.UUID, group_id: uuid.UUID, resume: list[dict[str, Any]] | None) -> None:
        async with self._txn(group_id) as session:
            run = await session.get(Run, run_id)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            if resume is not None and run.status != RunStatus.AWAITING_APPROVAL:
                return
            group = await session.get(Group, group_id)
            assert group is not None
            run.status = RunStatus.RUNNING
            run.started_at = run.started_at or _utcnow()
            snapshot: dict[str, Any] = dict(run.agent_snapshot)
            thread_id, task_text = run.thread_id, run.task
            consumed = (run.iterations, run.input_tokens, run.output_tokens, run.active_seconds)
            triggered_by = (run.triggered_by_type, str(run.triggered_by_id))
            started_payload = {
                "agent_id": snapshot["agent_id"],
                "agent_snapshot_version": hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True).encode()
                ).hexdigest()[:12],
                "task": run.task,
                "limits": snapshot["limits"],
                "triggered_by_type": run.triggered_by_type,
                "launch_mode": run.launch_mode,
                "relaunched_from": str(run.relaunched_from) if run.relaunched_from else None,
            }
            cost_rate = group.cost_per_1k_tokens

        secrets = await self._known_secrets(group_id)
        sink = RunEventSink(self.writer, group_id=group_id, run_id=run_id, known_secrets=secrets)
        ctx = make_run_context(
            snapshot,
            run_id=run_id,
            group_id=group_id,
            sink=sink,
            iterations=consumed[0],
            input_tokens=consumed[1],
            output_tokens=consumed[2],
            active_seconds=consumed[3],
            triggered_by=triggered_by,
        )
        hook_runner = HookRunner(
            hooks_from_snapshot(snapshot),
            group_id,
            run_id,
            secrets=await self._hook_secrets(group_id, snapshot),
            known_secrets=secrets,
        )
        sections = await self._system_sections(group_id, snapshot)
        if resume is None:
            await sink.emit(
                "run_started",
                {
                    **started_payload,
                    "system_prompt": compose_system_prompt(snapshot, sections),
                    "internal_tools": list(ctx.internal_tools),
                },
            )

        outcome: tuple[str, str | None, str | None] | None = None
        graph = None
        config: dict[str, Any] = {}
        out: dict[str, Any] = {}
        try:
            tools, warnings = await load_runtime_tools(snapshot, await self._headers_for(group_id, snapshot))
            if resume is None:
                warnings = [*self._retired_warnings(snapshot), *warnings]
            for warning in warnings:
                await sink.emit("config_warning", {"message": warning})
            graph = build_agent(
                snapshot,
                self.provider,
                tools,
                checkpointer=self.checkpointer,
                backend_factory=lambda _ctx: self._backend_for(group_id, snapshot, run_id, sink),
                ctx=ctx,
                hook_runner=hook_runner,
                system_sections=sections,
                extra_tools=self._extra_tools(group_id, snapshot, run_id, sink),
            )
            limit_iterations = snapshot["limits"]["max_iterations"]
            config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": max(25, limit_iterations * 12 + 25),
            }
            graph_input: Any = (
                {"messages": [HumanMessage(task_text)]} if resume is None else Command(resume={"decisions": resume})
            )
            remaining = max(0.001, ctx.limits.max_duration_seconds - ctx.active_seconds)
            async with asyncio.timeout(remaining):
                out = await graph.ainvoke(graph_input, config)
        except TimeoutError:
            if ctx.limit_hit is None:
                ctx.limit_hit = "duration"
                await sink.emit(
                    "limit_reached",
                    {
                        "limit": "duration",
                        "value": int(ctx.active_seconds),
                        "max": ctx.limits.max_duration_seconds,
                    },
                )
            outcome = (RunStatus.LIMIT_REACHED, "duration", None)
        except asyncio.CancelledError:
            if self._abandoning or run_id not in self._stop_requested:
                raise
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            outcome = (RunStatus.STOPPED, "manual", None)
        except Exception as exc:  # noqa: BLE001 - toute erreur d'exécution est consignée
            log.warning("run_failed", run_id=str(run_id), error=f"{type(exc).__name__}: {exc}")
            outcome = (RunStatus.FAILED, "error", f"{type(exc).__name__}: {exc}")

        counters = self._counters(ctx, cost_rate)
        if outcome is None and not out.get("__interrupt__") and ctx.start_blocked_by:
            outcome = (RunStatus.FAILED, "blocked_by_hook", f"Bloquée par le hook « {ctx.start_blocked_by} »")
        if outcome is not None:
            await self._fire_run_end(hook_runner, ctx, outcome[0], outcome[1])
            await self._end_run(group_id, run_id, outcome[0], outcome[1], outcome[2], counters)
            return
        if out.get("__interrupt__"):
            await self._await_approval(
                group_id, run_id, snapshot, graph, config, out["__interrupt__"], counters, secrets, ctx, hook_runner
            )
            return
        if ctx.limit_hit:
            status, reason = RunStatus.LIMIT_REACHED, ctx.limit_hit
        else:
            status, reason = RunStatus.SUCCEEDED, None
        await self._fire_run_end(hook_runner, ctx, status, reason)
        await self._end_run(group_id, run_id, status, reason, self._final_text(out), counters)

    async def _fire_run_end(self, runner: HookRunner, ctx: RunContext, status: str, reason: str | None) -> None:
        """Hooks `run_end` pour les fins que le middleware ne voit pas (erreur, arrêt, limite de durée) :
        ils ne font que consigner, l'exécution étant déjà finie."""
        if ctx.run_end_fired or not runner.applies_to("run_end"):
            return
        ctx.run_end_fired = True
        data = HookEventData(
            event="run_end",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            triggered_by_type=ctx.triggered_by_type,
            triggered_by_id=ctx.triggered_by_id,
            status=status,
            stop_reason=reason,
        )
        with contextlib.suppress(Exception):
            verdict = await runner.evaluate(data)
            for e in verdict.evaluations:
                suffix = " (consigné seulement : l'action est déjà passée)" if e.decision == "block" else ""
                await ctx.sink.emit(
                    "hook_evaluated",
                    {
                        "hook_id": e.hook_id,
                        "hook_type": e.hook_type,
                        "event": e.event,
                        "decision": e.decision,
                        "reason": e.reason + suffix,
                        "duration_ms": e.duration_ms,
                    },
                )

    @staticmethod
    def _counters(ctx: RunContext, cost_rate: Decimal) -> dict[str, Any]:
        tokens = ctx.input_tokens + ctx.output_tokens
        return {
            "iterations": ctx.iterations,
            "input_tokens": ctx.input_tokens,
            "output_tokens": ctx.output_tokens,
            "active_seconds": ctx.active_seconds,
            "cost": Decimal(tokens) / Decimal(1000) * cost_rate,
        }

    @staticmethod
    def _final_text(out: dict[str, Any]) -> str | None:
        for message in reversed(out.get("messages", [])):
            if isinstance(message, AIMessage):
                return message_text(message)
        return None

    async def _known_secrets(self, group_id: uuid.UUID) -> list[str]:
        """Valeurs secrètes du groupe, masquées dans la trace et les logs (FR-009, FR-014)."""
        async with self._txn(group_id) as session:
            return await credentials.group_secrets(session, group_id)

    async def _headers_for(self, group_id: uuid.UUID, snapshot: dict[str, Any]) -> dict[str, dict[str, str]]:
        """En-têtes d'authentification des serveurs MCP, déchiffrés au moment de l'exécution seulement."""
        async with self._txn(group_id) as session:
            return await credentials.tool_headers(session, snapshot)

    async def _hook_secrets(self, group_id: uuid.UUID, snapshot: dict[str, Any]) -> dict[str, str]:
        ids = [h.id for h in hooks_from_snapshot(snapshot) if h.hook_type == "webhook"]
        async with self._txn(group_id) as session:
            return await credentials.hook_secrets(session, ids)

    def _backend_for(self, group_id: uuid.UUID, snapshot: dict[str, Any], run_id: uuid.UUID, sink: Any) -> Any:
        """Fichiers de l'agent : espace de travail éphémère, `/memories/`, `/group-memory/` (lecture seule)
        et `/skills/` (lecture seule), selon la configuration figée de l'agent."""
        return build_run_backend(
            self._sessionmaker,
            group_id=group_id,
            agent_id=uuid.UUID(snapshot["agent_id"]),
            run_id=run_id,
            sink=sink,
            agent_memory=bool(snapshot.get("agent_memory_enabled")),
            group_memory=bool(snapshot.get("group_memory_enabled")),
            skill_tool_ids=[uuid.UUID(t["id"]) for t in allowed_skills(snapshot)],
        )

    def _extra_tools(self, group_id: uuid.UUID, snapshot: dict[str, Any], run_id: uuid.UUID, sink: Any) -> list[Any]:
        if not snapshot.get("group_memory_enabled"):
            return []
        return [
            proposals.make_propose_tool(
                self._sessionmaker, group_id=group_id, agent_id=uuid.UUID(snapshot["agent_id"]), run_id=run_id, sink=sink
            )
        ]

    async def _system_sections(self, group_id: uuid.UUID, snapshot: dict[str, Any]) -> list[str]:
        """Index de la mémoire (chemins seulement) ajouté au prompt système."""
        agent_paths: list[str] = []
        group_paths: list[str] = []
        if snapshot.get("agent_memory_enabled") or snapshot.get("group_memory_enabled"):
            async with self._txn(group_id) as session:
                if snapshot.get("agent_memory_enabled"):
                    entries = await memory_service.list_entries(
                        session, scope="agent", agent_id=uuid.UUID(snapshot["agent_id"])
                    )
                    agent_paths = [e.path for e in entries]
                if snapshot.get("group_memory_enabled"):
                    group_paths = [e.path for e in await memory_service.list_entries(session, scope="group")]
        section = memory_section(snapshot, agent_paths, group_paths)
        return [section] if section else []

    @staticmethod
    def _retired_warnings(snapshot: dict[str, Any]) -> list[str]:
        return [
            f"L'outil « {t['name']} » a été retiré du catalogue : l'agent s'exécute sans lui."
            for t in snapshot.get("tools", [])
            if t.get("status") == "retired"
        ]

    # --- approbation -----------------------------------------------------------------------

    async def _await_approval(
        self,
        group_id: uuid.UUID,
        run_id: uuid.UUID,
        snapshot: dict[str, Any],
        graph: Any,
        config: dict[str, Any],
        interrupts: Sequence[Any],
        counters: dict[str, Any],
        secrets: Sequence[str],
        ctx: RunContext,
        hook_runner: HookRunner,
    ) -> None:
        """Crée les demandes d'approbation d'un arrêt du graphe. Les hooks « avant appel » sont évalués
        d'abord : un appel qu'ils bloquent est refusé sans déranger personne."""
        state = await graph.aget_state(config)
        last_ai = next((m for m in reversed(state.values.get("messages", [])) if isinstance(m, AIMessage)), None)
        pending_calls = list(last_ai.tool_calls) if last_ai else []

        # 1. appels en attente, associés à leur identifiant d'appel, et décision des hooks
        planned: list[tuple[Any, list[dict[str, Any]], list[str | None]]] = []
        verdicts: dict[tuple[int, int], tuple[str, str]] = {}
        evaluations: list[Any] = []
        for i, interrupt in enumerate(interrupts):
            actions = interrupt.value.get("action_requests", [])
            call_ids: list[str | None] = []
            for j, action in enumerate(actions):
                match = next(
                    (c for c in pending_calls if c["name"] == action["name"] and c["args"] == action["args"]), None
                )
                if match is not None:
                    pending_calls.remove(match)
                call_ids.append(match["id"] if match else None)
                if hook_runner.applies_to("before_tool_call"):
                    verdict = await hook_runner.evaluate(
                        HookEventData(
                            event="before_tool_call",
                            agent_id=ctx.agent_id,
                            agent_name=ctx.agent_name,
                            triggered_by_type=ctx.triggered_by_type,
                            triggered_by_id=ctx.triggered_by_id,
                            tool=action["name"],
                            operation=ctx.operations.get(action["name"], action["name"]),
                            classification=ctx.classification_of(action["name"]),
                            arguments=action.get("args", {}),
                        )
                    )
                    evaluations.extend(verdict.evaluations)
                    block = next((e for e in verdict.evaluations if e.decision == "block"), None)
                    if block is not None:
                        name = next((h.name for h in hook_runner.hooks if h.id == block.hook_id), block.hook_id)
                        verdicts[(i, j)] = (name, block.reason)
            planned.append((interrupt, actions, call_ids))

        # 2. demandes en base ; celles que les hooks bloquent sont refusées d'office
        created: list[tuple[int, ApprovalRequest]] = []
        ready: list[tuple[uuid.UUID, list[dict[str, Any]]]] = []
        async with self._txn(group_id) as session:
            run = await session.get(Run, run_id)
            group = await session.get(Group, group_id)
            assert run is not None and group is not None
            for i, (interrupt, actions, call_ids) in enumerate(planned):
                requests = await approvals.create_requests(
                    session, run=run, group=group, interrupt_id=str(interrupt.id), actions=actions,
                    call_ids=call_ids, secrets=secrets,
                )
                for j, request in enumerate(requests):
                    if (i, j) in verdicts:
                        name, reason = verdicts[(i, j)]
                        request.status = ApprovalStatus.REJECTED
                        request.decided_at = _utcnow()
                        request.comment = f"Bloqué par le hook « {name} » : {reason}"
                    created.append((i, request))
                if all(r.status != ApprovalStatus.PENDING for r in requests):
                    ready.append((run_id, [approvals.to_decision(r) for r in requests]))
            await self._apply_counters(session, run, counters)
            run.status = RunStatus.AWAITING_APPROVAL

        # 3. trace : évaluations de hooks, refus d'office, demandes pour les approbateurs
        for e in evaluations:
            await ctx.sink.emit(
                "hook_evaluated",
                {
                    "hook_id": e.hook_id, "hook_type": e.hook_type, "event": e.event,
                    "decision": e.decision, "reason": e.reason, "duration_ms": e.duration_ms,
                },
            )
        for _, request in created:
            if request.status == ApprovalStatus.PENDING:
                await ctx.sink.emit(
                    "approval_requested",
                    {
                        "approval_id": str(request.id),
                        "call_id": request.call_id,
                        "operation": request.tool_operation,
                        "expires_at": request.expires_at.isoformat().replace("+00:00", "Z"),
                    },
                )
            else:
                await ctx.sink.emit(
                    "approval_decided",
                    {"approval_id": str(request.id), "decision": "reject", "decided_by": None},
                )
        for resume_run_id, decisions in ready:
            self._spawn(resume_run_id, group_id, resume=decisions)

    async def decide(
        self, access: Any, approval_id: uuid.UUID, decision: str, comment: str | None
    ) -> ApprovalRequest:
        """Enregistre une décision (transaction propre, validée avant toute reprise) puis reprend
        l'exécution quand toutes les demandes de l'arrêt sont tranchées."""
        async with self._txn(access.group_id) as session:
            decided = await approvals.decide(session, access, approval_id, decision, comment)
        await self.emit(
            access.group_id,
            decided.run_id,
            "approval_decided",
            {
                "approval_id": str(approval_id),
                "decision": decision,
                "decided_by": str(access.principal.id),
            },
        )
        if decided.resume_decisions is not None:
            self._spawn(decided.run_id, access.group_id, resume=decided.resume_decisions)
        return decided.request

    async def expire_overdue_approvals(self) -> None:
        """Les demandes dont le délai est dépassé expirent ; l'exécution en attente s'arrête (`expired`)."""
        now = _utcnow()
        for group_id in await self._group_ids():
            expired: list[ApprovalRequest] = []
            async with self._txn(group_id) as session:
                rows = (
                    await session.execute(
                        select(ApprovalRequest)
                        .where(ApprovalRequest.status == ApprovalStatus.PENDING, ApprovalRequest.expires_at <= now)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
                for request in rows:
                    request.status = ApprovalStatus.EXPIRED
                    request.decided_at = now
                    expired.append(request)
            for run_id in dict.fromkeys(r.run_id for r in expired):
                for request in (r for r in expired if r.run_id == run_id):
                    await self.emit(
                        group_id,
                        run_id,
                        "approval_decided",
                        {"approval_id": str(request.id), "decision": "expired", "decided_by": None},
                    )
                await self._end_run(group_id, run_id, RunStatus.EXPIRED, "approval_timeout", None)

    # --- arrêt, fin, récupération ---------------------------------------------------------

    async def stop(self, group_id: uuid.UUID, run_id: uuid.UUID) -> None:
        """Demande l'arrêt : annule la tâche en cours (vérifié entre deux étapes au plus tard)."""
        async with self._txn(group_id) as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise RunNotFound(str(run_id))
            status = run.status
        if status in TERMINAL_STATUSES:
            return
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            self._stop_requested.add(run_id)
            task.cancel()
            return
        # Pas de tâche : exécution en attente d'approbation (ou en file) : on la clôt directement.
        async with self._txn(group_id) as session:
            await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id, ApprovalRequest.status == ApprovalStatus.PENDING)
                .values(status=ApprovalStatus.EXPIRED, decided_at=_utcnow())
            )
        await self._end_run(group_id, run_id, RunStatus.STOPPED, "manual", None)

    async def stop_runs_where(self, group_id: uuid.UUID, *, triggered_by: uuid.UUID) -> None:
        """Arrête toutes les exécutions actives d'une identité (membre retiré, compte révoqué)."""
        async with self._txn(group_id) as session:
            ids = list(
                (
                    await session.execute(
                        select(Run.id).where(
                            Run.triggered_by_id == triggered_by, Run.status.not_in([s.value for s in TERMINAL_STATUSES])
                        )
                    )
                ).scalars()
            )
        for run_id in ids:
            await self.stop(group_id, run_id)

    async def stop_group_runs(self, group_id: uuid.UUID) -> None:
        async with self._txn(group_id) as session:
            ids = list(
                (
                    await session.execute(
                        select(Run.id).where(Run.status.not_in([s.value for s in TERMINAL_STATUSES]))
                    )
                ).scalars()
            )
        for run_id in ids:
            await self.stop(group_id, run_id)

    async def _apply_counters(self, session: AsyncSession, run: Run, counters: dict[str, Any]) -> None:
        run.iterations = counters["iterations"]
        run.input_tokens = counters["input_tokens"]
        run.output_tokens = counters["output_tokens"]
        run.active_seconds = counters["active_seconds"]
        run.cost = counters["cost"]

    async def _end_run(
        self,
        group_id: uuid.UUID,
        run_id: uuid.UUID,
        status: str,
        stop_reason: str | None,
        result: str | None,
        counters: dict[str, Any] | None = None,
    ) -> None:
        """Clôt l'exécution : statut en base d'abord, puis `run_ended` (dernier événement)."""
        if result:
            # Le résultat final est une sortie de modèle : il peut recopier un secret (FR-009, SC-005).
            result = redact(result, await self._known_secrets(group_id))
        async with self._txn(group_id) as session:
            run = await session.get(Run, run_id, with_for_update=True)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            if counters is not None:
                await self._apply_counters(session, run, counters)
            run.status = status
            run.stop_reason = stop_reason
            run.result = result
            run.ended_at = _utcnow()
            payload = {
                "status": status,
                "stop_reason": stop_reason,
                "iterations": run.iterations,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cost": float(run.cost),
                "duration_ms": int(run.active_seconds * 1000),
                "result": result,
            }
        await self.emit(group_id, run_id, "run_ended", payload)
        self.writer.forget(run_id)
        self._stop_requested.discard(run_id)

    async def recover_on_startup(self) -> None:
        """Toute exécution en cours à l'arrêt de la plateforme devient `interrupted` (trace conservée,
        jamais de reprise automatique) ; celles en attente d'approbation sont conservées (FR-003a/b)."""
        for group_id in await self._group_ids():
            async with self._txn(group_id) as session:
                ids = list(
                    (
                        await session.execute(
                            select(Run.id).where(Run.status.in_([RunStatus.RUNNING, RunStatus.QUEUED]))
                        )
                    ).scalars()
                )
            for run_id in ids:
                await self._end_run(group_id, run_id, RunStatus.INTERRUPTED, "platform_restart", None)

    async def expire_overdue_proposals(self) -> None:
        """Les propositions de mémoire de groupe jamais validées expirent (30 jours par défaut)."""
        for group_id in await self._group_ids():
            async with self._txn(group_id) as session:
                group = await session.get(Group, group_id)
                if group is not None:
                    await proposals.expire_overdue(session, group)

    # --- tâche périodique -----------------------------------------------------------------

    def start_background(self) -> None:
        if self._background is None:
            self._background = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self.expire_overdue_approvals()
                await self.expire_overdue_proposals()
            except Exception:
                log.exception("expiry_failed")

    async def shutdown(self) -> None:
        """Arrêt propre du processus : les exécutions en cours sont laissées en l'état (elles seront
        marquées `interrupted` au prochain démarrage)."""
        await self.abandon_all()
