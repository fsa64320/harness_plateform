"""Monde de test : un groupe, trois personnes, un serveur MCP enregistré et un agent."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio

from tests.conftest import Actor, FakeModels


@dataclass
class World:
    group_id: uuid.UUID
    alice: Actor  # admin, tool_manager, approver, group_memory_manager
    bob: Actor  # simple membre
    carol: Actor  # simple membre
    tool_id: str
    ops: dict[str, str]  # nom d'opération -> id
    agent_id: str
    client: httpx.AsyncClient
    app: Any
    mcp: Any
    models: FakeModels
    extras: dict[str, Any] = field(default_factory=dict)

    def url(self, path: str = "") -> str:
        return f"/groups/{self.group_id}{path}"

    @property
    def runner(self) -> Any:
        return self.app.state.runner

    async def create_agent(self, actor: Actor | None = None, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": f"agent-{uuid.uuid4().hex[:6]}",
            "instructions": "Tu es un agent de test.",
            "model_id": "fake",
            "allowed_tool_ids": [self.ops["lookup"], self.ops["send_message"], self.ops["wait"]],
            "limits": {"max_iterations": 10, "max_duration_seconds": 60, "max_tokens": 100000},
            **overrides,
        }
        r = await self.client.post(self.url("/agents"), json=body, headers=(actor or self.alice).headers)
        assert r.status_code == 201, r.text
        return r.json()

    async def launch(self, actor: Actor | None = None, task: str = "fais le travail", agent_id: str | None = None,
                     **extra: Any) -> dict[str, Any]:
        r = await self.client.post(
            self.url("/runs"),
            json={"agent_id": agent_id or self.agent_id, "task": task, **extra},
            headers=(actor or self.bob).headers,
        )
        assert r.status_code == 202, r.text
        return r.json()

    async def wait(self, run_id: str, timeout: float = 20.0) -> dict[str, Any]:
        await asyncio.wait_for(self.runner.wait(uuid.UUID(run_id)), timeout)
        return await self.run(run_id)

    async def run(self, run_id: str, actor: Actor | None = None) -> dict[str, Any]:
        r = await self.client.get(self.url(f"/runs/{run_id}"), headers=(actor or self.bob).headers)
        assert r.status_code == 200, r.text
        return r.json()

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        r = await self.client.get(
            self.url(f"/runs/{run_id}/events"),
            headers={**self.bob.headers, "Accept": "application/json"},
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def event_types(self, run_id: str) -> list[str]:
        return [e["type"] for e in await self.events(run_id)]

    async def wait_for_event(self, run_id: str, type_: str, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in await self.events(run_id):
                if e["type"] == type_:
                    return e
            await asyncio.sleep(0.05)
        raise AssertionError(f"Événement {type_} jamais reçu pour {run_id}")

    async def pending_approvals(self, actor: Actor | None = None) -> list[dict[str, Any]]:
        r = await self.client.get(
            self.url("/approvals?status=pending"), headers=(actor or self.alice).headers
        )
        assert r.status_code == 200, r.text
        return r.json()


WorldFactory = Callable[..., Awaitable[World]]


@pytest_asyncio.fixture
async def world(
    client: httpx.AsyncClient,
    app: Any,
    make_group: Callable[..., Awaitable[uuid.UUID]],
    login_as: Callable[..., Awaitable[Actor]],
    mcp_server: Any,
    fake_models: FakeModels,
) -> World:
    group_id = await make_group("demo")
    alice = await login_as(
        "alice@example.test", group_id, ("admin", "tool_manager", "approver", "group_memory_manager")
    )
    bob = await login_as("bob@example.test", group_id)
    carol = await login_as("carol@example.test", group_id)

    r = await client.post(
        f"/groups/{group_id}/tools",
        json={"kind": "mcp_server", "name": "crm", "description": "CRM de test", "mcp": {"url": mcp_server.url}},
        headers=alice.headers,
    )
    assert r.status_code == 201, r.text
    tool = r.json()
    ops = {o["name"]: o["id"] for o in tool["operations"]}
    for name in ("lookup", "wait"):
        rc = await client.put(
            f"/groups/{group_id}/tools/{tool['id']}/operations/{name}/classification",
            json={"classification": "read_only"},
            headers=alice.headers,
        )
        assert rc.status_code == 200, rc.text

    w = World(group_id, alice, bob, carol, tool["id"], ops, "", client, app, mcp_server, fake_models)
    w.agent_id = (await w.create_agent(alice, name="agent-demo"))["id"]
    return w


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


