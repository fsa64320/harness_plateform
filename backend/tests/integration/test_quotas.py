"""Quotas par membre et par compte de service : exécutions simultanées et tokens par jour (FR-023)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import text

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def _set_member_quota(world: World, actor: Any, **quota: int) -> None:
    members = (await world.client.get(world.url("/members"), headers=world.alice.headers)).json()
    mid = next(m["id"] for m in members if m["person_email"] == actor.email)
    r = await world.client.patch(world.url(f"/members/{mid}"), json=quota, headers=world.alice.headers)
    assert r.status_code == 200, r.text


async def _launch_raw(world: World, actor: Any, agent_id: str | None = None) -> httpx.Response:
    return await world.client.post(
        world.url("/runs"), json={"agent_id": agent_id or world.agent_id, "task": "x"}, headers=actor.headers
    )


async def test_exceeding_max_concurrent_runs_is_refused_with_429_and_recorded(world: World) -> None:
    await _set_member_quota(world, world.bob, max_concurrent_runs=2)
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    first = await world.launch(world.bob)
    await world.wait_for_event(first["id"], "tool_call_started")
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    second = await world.launch(world.bob)
    await world.wait_for_event(second["id"], "tool_call_started")

    r = await _launch_raw(world, world.bob)
    assert r.status_code == 429
    assert r.json()["code"] == "quota_exceeded" and "exécutions simultanées" in r.json()["message"]

    runs = (await world.client.get(world.url("/runs?status=rejected_quota"), headers=world.bob.headers)).json()["items"]
    assert len(runs) == 1 and runs[0]["stop_reason"] == "max_concurrent_runs" and runs[0]["ended_at"] is not None
    assert [e["type"] for e in await world.events(runs[0]["id"])] == ["run_ended"]
    assert world.runner.is_active(uuid.UUID(runs[0]["id"])) is False


async def test_a_finished_run_frees_a_slot(world: World) -> None:
    await _set_member_quota(world, world.bob, max_concurrent_runs=1)
    world.models.set(say("fini"))
    first = await world.launch(world.bob)
    await world.wait(first["id"])
    world.models.set(say("fini encore"))
    second = await _launch_raw(world, world.bob)
    assert second.status_code == 202


async def test_quotas_are_per_member(world: World) -> None:
    await _set_member_quota(world, world.bob, max_concurrent_runs=1)
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    first = await world.launch(world.bob)
    await world.wait_for_event(first["id"], "tool_call_started")
    assert (await _launch_raw(world, world.bob)).status_code == 429
    world.models.set(say("ok"))
    assert (await _launch_raw(world, world.carol)).status_code == 202  # carol a son propre quota


async def test_exceeding_max_tokens_per_day_is_refused_with_429(world: World) -> None:
    await _set_member_quota(world, world.bob, max_tokens_per_day=100)
    world.models.set(say("gros", input_tokens=90, output_tokens=40))
    run = await world.launch(world.bob)
    await world.wait(run["id"])

    r = await _launch_raw(world, world.bob)
    assert r.status_code == 429 and "tokens" in r.json()["message"]
    rejected = (await world.client.get(world.url("/runs?status=rejected_quota"), headers=world.bob.headers)).json()["items"]
    assert [x["stop_reason"] for x in rejected] == ["max_tokens_per_day"]
    assert (await _launch_raw(world, world.carol)).status_code == 202


async def test_tokens_of_previous_days_do_not_count(world: World, owner_session: Any) -> None:
    await _set_member_quota(world, world.bob, max_tokens_per_day=100)
    world.models.set(say("gros", input_tokens=90, output_tokens=40))
    run = await world.launch(world.bob)
    await world.wait(run["id"])
    async with owner_session() as s, s.begin():
        await s.execute(text("UPDATE runs SET created_at = created_at - interval '2 days'"))
    world.models.set(say("ok"))
    assert (await _launch_raw(world, world.bob)).status_code == 202


async def test_service_account_has_its_own_quotas(world: World) -> None:
    r = await world.client.post(
        world.url("/service-accounts"),
        json={"name": "ci", "allowed_agent_ids": [world.agent_id], "max_concurrent_runs": 1, "max_tokens_per_day": 100000},
        headers=world.alice.headers,
    )
    token = {"Authorization": f"Bearer {r.json()['token']}"}
    anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test/api/v1")
    async with anonymous as program:
        world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
        first = (await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=token)).json()
        await world.wait_for_event(first["id"], "tool_call_started")
        second = await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=token)
    assert second.status_code == 429
    world.models.set(say("ok"))
    assert (await _launch_raw(world, world.bob)).status_code == 202  # les membres ne sont pas concernés
