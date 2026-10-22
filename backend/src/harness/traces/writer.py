"""Écriture de la trace : `seq` sans trou par exécution, masquage puis troncature avant écriture,
puis diffusion sur le bus (FR-007, FR-009, constitution IV)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harness.db.models.runs import TraceEvent
from harness.db.session import group_context
from harness.traces.bus import TraceBus
from harness.traces.redaction import redact

MAX_TEXT_BYTES = 64 * 1024


@dataclass(frozen=True)
class TraceRecord:
    run_id: uuid.UUID
    group_id: uuid.UUID
    seq: int
    type: str
    occurred_at: dt.datetime
    payload: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "seq": self.seq,
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
            "payload": self.payload,
        }


class TraceStore(Protocol):
    async def last_seq(self, group_id: uuid.UUID, run_id: uuid.UUID) -> int: ...

    async def append(self, record: TraceRecord) -> None: ...


class SqlTraceStore:
    """Chaque événement est validé aussitôt dans sa propre transaction, sous le contexte du groupe."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def last_seq(self, group_id: uuid.UUID, run_id: uuid.UUID) -> int:
        async with self._sessionmaker() as session, session.begin():
            await group_context(session, group_id)
            result = await session.execute(
                select(func.coalesce(func.max(TraceEvent.seq), 0)).where(TraceEvent.run_id == run_id)
            )
            return int(result.scalar_one())

    async def append(self, record: TraceRecord) -> None:
        async with self._sessionmaker() as session, session.begin():
            await group_context(session, record.group_id)
            session.add(
                TraceEvent(
                    group_id=record.group_id,
                    run_id=record.run_id,
                    seq=record.seq,
                    occurred_at=record.occurred_at,
                    type=record.type,
                    payload=record.payload,
                )
            )


def truncate_texts(value: Any, limit: int = MAX_TEXT_BYTES) -> Any:
    """Tronque chaque champ texte à `limit` octets. L'objet qui le porte reçoit `truncated: true` et
    le `sha256` du contenu complet (`<champ>_sha256` à partir du deuxième champ tronqué)."""
    if isinstance(value, list):
        return [truncate_texts(v, limit) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    digests: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(item, str) and len(item.encode()) > limit:
            digests[key] = hashlib.sha256(item.encode()).hexdigest()
            out[key] = item.encode()[:limit].decode(errors="ignore")
        else:
            out[key] = truncate_texts(item, limit)
    if digests:
        out["truncated"] = True
        for i, (key, digest) in enumerate(digests.items()):
            out["sha256" if i == 0 else f"{key}_sha256"] = digest
    return out


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        parts.append(block if isinstance(block, str) else str(block.get("text", block)))
    return "".join(parts)


def serialize_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """Messages tels que le modèle les reçoit, sous forme JSON stable (rôle, contenu, appels d'outils)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m.type, "content": message_text(m)}
        if isinstance(m, AIMessage) and m.tool_calls:
            item["tool_calls"] = [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in m.tool_calls]
        if isinstance(m, ToolMessage):
            item["tool_call_id"] = m.tool_call_id
            if m.name:
                item["name"] = m.name
        out.append(item)
    return out


class TraceWriter:
    def __init__(self, store: TraceStore, bus: TraceBus) -> None:
        self._store = store
        self.bus = bus
        self._locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next: dict[uuid.UUID, int] = {}

    async def emit(
        self,
        *,
        group_id: uuid.UUID,
        run_id: uuid.UUID,
        type: str,
        payload: dict[str, Any],
        known_secrets: Iterable[str] = (),
    ) -> TraceRecord:
        clean = truncate_texts(redact(payload, known_secrets))
        async with self._locks[run_id]:
            if run_id not in self._next:
                self._next[run_id] = await self._store.last_seq(group_id, run_id) + 1
            seq = self._next[run_id]
            record = TraceRecord(run_id, group_id, seq, type, dt.datetime.now(dt.UTC), clean)
            await self._store.append(record)
            self._next[run_id] = seq + 1
        self.bus.publish(record)
        return record

    def forget(self, run_id: uuid.UUID) -> None:
        """Libère le compteur d'une exécution terminée (il sera relu en base si on y réécrit)."""
        self._next.pop(run_id, None)
        self._locks.pop(run_id, None)


class RunEventSink:
    """Émetteur d'événements d'une exécution : fixe son identité et ses secrets connus."""

    def __init__(
        self,
        writer: TraceWriter,
        *,
        group_id: uuid.UUID,
        run_id: uuid.UUID,
        known_secrets: Iterable[str] = (),
    ) -> None:
        self._writer = writer
        self.group_id = group_id
        self.run_id = run_id
        self.known_secrets = list(known_secrets)

    async def emit(self, type: str, payload: dict[str, Any]) -> TraceRecord:
        return await self._writer.emit(
            group_id=self.group_id,
            run_id=self.run_id,
            type=type,
            payload=payload,
            known_secrets=self.known_secrets,
        )
