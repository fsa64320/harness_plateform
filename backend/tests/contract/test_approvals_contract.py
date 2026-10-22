"""Contrat : demandes d'approbation (FR-005, FR-005b)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def _pause(world: World) -> dict:
    world.models.set(tool_call("crm_send_message", {"text": "salut"}), say("ok"))
    run = await world.launch(world.bob)
    await world.wait_for_event(run["id"], "approval_requested")
    return run


async def test_list_pending_approvals_matches_the_contract(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    run = await _pause(world)
    r = await world.client.get(world.url("/approvals?status=pending"), headers=world.alice.headers)
    assert r.status_code == 200
    assert_matches_contract(r)
    [approval] = r.json()
    assert approval["run_id"] == run["id"] and approval["status"] == "pending"
    assert approval["decided_by"] is None and approval["expires_at"] > approval["requested_at"]


async def test_each_caller_only_sees_the_requests_they_can_decide(world: World) -> None:
    await _pause(world)
    # le déclencheur (bob) et un approbateur (alice) les voient ; un autre membre (carol) non
    assert len(await world.pending_approvals(world.bob)) == 1
    assert len(await world.pending_approvals(world.alice)) == 1
    assert await world.pending_approvals(world.carol) == []


async def test_decision_matches_the_contract(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    await _pause(world)
    [approval] = await world.pending_approvals(world.alice)
    r = await world.client.post(
        world.url(f"/approvals/{approval['id']}/decision"),
        json={"decision": "approve", "comment": "ok pour moi"},
        headers=world.alice.headers,
    )
    assert r.status_code == 200
    assert_matches_contract(r)
    assert r.json()["status"] == "approved" and r.json()["decided_by"] == str(world.alice.person_id)


async def test_forbidden_and_conflict_match_the_contract(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    await _pause(world)
    [approval] = await world.pending_approvals(world.alice)
    url = world.url(f"/approvals/{approval['id']}/decision")
    forbidden = await world.client.post(url, json={"decision": "approve"}, headers=world.carol.headers)
    assert forbidden.status_code == 403
    assert_matches_contract(forbidden)

    await world.client.post(url, json={"decision": "reject"}, headers=world.alice.headers)
    conflict = await world.client.post(url, json={"decision": "approve"}, headers=world.alice.headers)
    assert conflict.status_code == 409
    assert_matches_contract(conflict)


async def test_unknown_decision_value_is_422(world: World) -> None:
    await _pause(world)
    [approval] = await world.pending_approvals(world.alice)
    r = await world.client.post(
        world.url(f"/approvals/{approval['id']}/decision"), json={"decision": "peut-être"}, headers=world.alice.headers
    )
    assert r.status_code == 422
