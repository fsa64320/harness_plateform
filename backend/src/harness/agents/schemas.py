"""Données d'entrée d'un agent (schéma `AgentInput` du contrat)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AgentLimits(BaseModel):
    max_iterations: int = Field(ge=1)
    max_duration_seconds: int = Field(ge=1)
    max_tokens: int = Field(ge=1)


class AgentInput(BaseModel):
    name: str = Field(min_length=1)
    instructions: str
    model_id: str = Field(min_length=1)
    allowed_tool_ids: list[uuid.UUID]
    agent_memory_enabled: bool = True
    group_memory_enabled: bool = False
    subagents_enabled: bool = False
    limits: AgentLimits
