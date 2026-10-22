"""Magasin de trace en mémoire : tests unitaires et évaluations (même interface que le magasin SQL)."""

from __future__ import annotations

import uuid

from harness.traces.writer import TraceRecord


class MemoryTraceStore:
    def __init__(self) -> None:
        self.rows: list[TraceRecord] = []

    async def last_seq(self, group_id: uuid.UUID, run_id: uuid.UUID) -> int:
        return max((r.seq for r in self.rows if r.run_id == run_id), default=0)

    async def append(self, record: TraceRecord) -> None:
        self.rows.append(record)

    def events(self, run_id: uuid.UUID | None = None) -> list[TraceRecord]:
        return [r for r in self.rows if run_id is None or r.run_id == run_id]

    def types(self) -> list[str]:
        return [r.type for r in self.rows]

    def of(self, type_: str) -> list[TraceRecord]:
        return [r for r in self.rows if r.type == type_]
