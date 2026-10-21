"""Contexte d'une exécution partagé par les middlewares : limites, compteurs, liste des outils."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


class EventSink(Protocol):
    async def emit(self, type: str, payload: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class Limits:
    max_iterations: int
    max_duration_seconds: int
    max_tokens: int


@dataclass
class RunContext:
    run_id: uuid.UUID
    group_id: uuid.UUID
    sink: EventSink
    limits: Limits
    allowed_tools: frozenset[str]  # outils liés à l'agent : catalogue autorisé + outils internes
    classifications: dict[str, str]  # nom lié -> classification
    internal_tools: tuple[str, ...]
    operations: dict[str, str] = field(default_factory=dict)  # nom lié -> nom d'opération MCP
    agent_id: str = ""
    agent_name: str = ""
    triggered_by_type: str = "person"
    triggered_by_id: str = ""
    # Consommation (reprise depuis la base quand une exécution reprend après approbation).
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    active_seconds_before: float = 0.0
    segment_started: float = field(default_factory=time.monotonic)
    limit_hit: str | None = None
    # Hooks : `run_start` bloqué (id du hook) ; `run_end` déjà évalué par le middleware.
    start_blocked_by: str | None = None
    run_end_fired: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def active_seconds(self) -> float:
        return self.active_seconds_before + (time.monotonic() - self.segment_started)

    def classification_of(self, tool_name: str) -> str:
        if tool_name in self.internal_tools:
            return "read_only"
        return self.classifications.get(tool_name, "unclassified")
