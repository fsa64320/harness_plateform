"""Bus en mémoire du processus : diffuse chaque événement de trace à ses abonnés (SSE)."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.traces.writer import TraceRecord


class TraceBus:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[TraceRecord]]] = defaultdict(set)

    def publish(self, record: TraceRecord) -> None:
        for queue in list(self._subscribers.get(record.run_id, ())):
            queue.put_nowait(record)

    @contextmanager
    def subscribe(self, run_id: uuid.UUID) -> Iterator[asyncio.Queue[TraceRecord]]:
        """S'abonner avant de lire l'historique en base évite de perdre un événement entre les deux."""
        queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]
