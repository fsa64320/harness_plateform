"""La politique réseau s'applique à l'enregistrement et à l'appel des serveurs MCP et des webhooks."""

from __future__ import annotations

import httpx
import pytest

from harness import config
from tests.world_fixtures import World


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_ALLOW_PRIVATE_NETWORKS", "false")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


async def test_registering_an_mcp_server_on_an_internal_address_is_refused(world: World, strict: None) -> None:
    r = await world.client.post(
        world.url("/tools"),
        json={"kind": "mcp_server", "name": "interne", "mcp": {"url": "https://169.254.169.254/mcp"}},
        headers=world.alice.headers,
    )
    assert r.status_code == 422
    assert "réseau" in r.json()["message"]


async def test_registering_a_webhook_on_an_internal_address_is_refused(world: World, strict: None) -> None:
    r = await world.client.post(
        world.url("/tools"),
        json={
            "kind": "hook", "name": "interne",
            "hook": {"events": ["before_tool_call"], "hook_type": "webhook", "url": "https://10.0.0.5/hook", "signing_secret": "s"},
        },
        headers=world.alice.headers,
    )
    assert r.status_code == 422 and "réseau" in r.json()["message"]


async def test_a_webhook_resolving_to_an_internal_address_is_blocked_at_call_time(strict: None) -> None:
    import uuid

    from harness.catalog.hook_rules import HookEventData
    from harness.catalog.hook_webhook import WebhookRequestContext, call_webhook
    from harness.catalog.hooks import Hook

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"decision": "allow"})

    hook = Hook(id="h", name="h", events=["before_tool_call"], hook_type="webhook", url="https://192.168.1.10/x")
    data = HookEventData(event="before_tool_call", agent_id="a", agent_name="a", triggered_by_type="person", triggered_by_id="p")
    result = await call_webhook(
        hook, data, WebhookRequestContext(uuid.uuid4(), uuid.uuid4()), secret="s", http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert result.decision == "block" and result.cause == "blocked_network" and not called
