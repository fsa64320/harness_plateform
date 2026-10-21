"""Comptes de service : lancement programmatique, jamais d'approbation, révocation (FR-002, FR-002a, FR-002b)."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
from sqlalchemy import text

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World


async def _account(world: World, **overrides: Any) -> dict[str, Any]:
    body = {"name": "ci", "allowed_agent_ids": [world.agent_id], **overrides}
    r = await world.client.post(world.url("/service-accounts"), json=body, headers=world.alice.headers)
    assert r.status_code == 201, r.text
    return r.json()


def bearer(account: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {account['token']}"}


async def _anonymous(world: World) -> httpx.AsyncClient:
    """Client sans en-tête d'identité de test : seul le jeton compte."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test/api/v1")


async def test_token_is_stored_hashed_with_a_salt_and_shown_once(world: World, owner_session: Any) -> None:
    account = await _account(world)
    async with owner_session() as s:
        row = (await s.execute(text("SELECT token_hash, token_prefix, status FROM service_accounts"))).one()
        columns = [c for (c,) in (await s.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'service_accounts'"))).all()]
    assert account["token"] not in str(tuple(row))
    assert row.token_hash != hashlib.sha256(account["token"].encode()).hexdigest()  # salé : pas un simple SHA-256
    assert "token_salt" in columns and row.token_prefix == account["token_prefix"]
    listing = await world.client.get(world.url("/service-accounts"), headers=world.alice.headers)
    assert account["token"] not in listing.text


async def test_a_service_account_launches_an_allowed_agent_in_programmatic_mode(world: World) -> None:
    account = await _account(world)
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("résultat pour le script"))
    async with await _anonymous(world) as program:
        r = await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "depuis la CI"}, headers=bearer(account))
        assert r.status_code == 202, r.text
        run = r.json()
        assert run["launch_mode"] == "programmatic" and run["triggered_by_type"] == "service_account"
        assert run["triggered_by_id"] == account["id"]
        await world.wait(run["id"])

        # FR-002b : suivre l'état, récupérer le résultat et la trace
        got = (await program.get(world.url(f"/runs/{run['id']}"), headers=bearer(account))).json()
        assert got["status"] == "succeeded" and got["result"] == "résultat pour le script"
        events = await program.get(world.url(f"/runs/{run['id']}/events"), headers={**bearer(account), "Accept": "application/json"})
        assert events.status_code == 200 and events.json()[0]["payload"]["launch_mode"] == "programmatic"


async def test_a_service_account_cannot_launch_an_agent_it_is_not_allowed(world: World) -> None:
    other = await world.create_agent(name="autre agent")
    account = await _account(world)
    async with await _anonymous(world) as program:
        r = await program.post(world.url("/runs"), json={"agent_id": other["id"], "task": "x"}, headers=bearer(account))
    assert r.status_code == 403


async def test_a_service_account_only_sees_and_stops_its_own_runs(world: World) -> None:
    account = await _account(world)
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    mine = await world.launch(world.bob)  # lancée par une personne
    await world.wait_for_event(mine["id"], "tool_call_started")
    async with await _anonymous(world) as program:
        assert (await program.get(world.url(f"/runs/{mine['id']}"), headers=bearer(account))).status_code == 404
        assert (await program.post(world.url(f"/runs/{mine['id']}/stop"), headers=bearer(account))).status_code == 404
        listing = (await program.get(world.url("/runs"), headers=bearer(account))).json()
        assert listing["items"] == []

        world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
        own = (await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=bearer(account))).json()
        await world.wait_for_event(own["id"], "tool_call_started")
        assert (await program.post(world.url(f"/runs/{own['id']}/stop"), headers=bearer(account))).status_code == 202
        assert (await world.wait(own["id"], timeout=5))["status"] == "stopped"


async def test_a_service_account_cannot_reach_anything_but_runs(world: World) -> None:
    account = await _account(world)
    async with await _anonymous(world) as program:
        for method, path in (
            ("GET", "/agents"), ("POST", "/agents"), ("GET", "/tools"), ("GET", "/group-memory"), ("GET", "/members"),
            ("GET", "/service-accounts"), ("GET", "/audit"),
        ):
            r = await program.request(method, world.url(path), json={}, headers=bearer(account))
            assert r.status_code == 403, (method, path, r.status_code)


async def test_a_service_account_cannot_approve_and_its_run_waits_for_an_approver(world: World) -> None:
    account = await _account(world)
    world.models.set(tool_call("crm_send_message", {"text": "salut"}), say("ok"))
    async with await _anonymous(world) as program:
        run = (await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=bearer(account))).json()
        await world.wait_for_event(run["id"], "approval_requested")
        [approval] = [a for a in await world.pending_approvals(world.alice) if a["run_id"] == run["id"]]
        assert approval["no_eligible_approver"] is False  # alice est approbatrice

        r = await program.post(world.url(f"/approvals/{approval['id']}/decision"), json={"decision": "approve"}, headers=bearer(account))
        assert r.status_code == 403
        assert (await world.run(run["id"], world.alice))["status"] == "awaiting_approval"

    ok = await world.client.post(world.url(f"/approvals/{approval['id']}/decision"), json={"decision": "approve"}, headers=world.alice.headers)
    assert ok.status_code == 200
    assert (await world.wait(run["id"]))["status"] == "succeeded"


async def test_without_any_approver_the_request_is_flagged_expires_and_the_run_ends_expired(world: World, owner_session: Any) -> None:
    import datetime as dt

    from sqlalchemy import update

    from harness.db.models.identity import Membership

    account = await _account(world)
    async with owner_session() as s, s.begin():  # plus personne n'a le rôle d'approbateur dans le groupe
        await s.execute(
            update(Membership)
            .where(Membership.person_id == world.alice.person_id)
            .values(roles=["admin", "member", "tool_manager"])
        )
    world.models.set(tool_call("crm_send_message", {"text": "salut"}), say("ok"))
    async with await _anonymous(world) as program:
        run = (await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=bearer(account))).json()
    await world.wait_for_event(run["id"], "approval_requested")

    # les administrateurs la voient (lecture seule), signalée « sans approbateur éligible »
    [flagged] = await world.pending_approvals(world.alice)
    assert flagged["run_id"] == run["id"] and flagged["no_eligible_approver"] is True
    assert await world.pending_approvals(world.bob) == []  # un simple membre ne la voit pas
    # et ne peut pas décider à la place d'un approbateur absent : l'admin n'est pas approbateur
    r = await world.client.post(world.url(f"/approvals/{flagged['id']}/decision"), json={"decision": "approve"}, headers=world.alice.headers)
    assert r.status_code == 403

    async with owner_session() as s, s.begin():
        await s.execute(text("UPDATE approval_requests SET expires_at = :t"), {"t": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)})
    await world.runner.expire_overdue_approvals()
    assert (await world.run(run["id"], world.alice))["status"] == "expired"


async def test_revocation_stops_its_runs_and_later_calls_get_401(world: World) -> None:
    account = await _account(world)
    world.models.set(tool_call("crm_wait", {"seconds": 30}), say("jamais"))
    async with await _anonymous(world) as program:
        run = (await program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=bearer(account))).json()
        await world.wait_for_event(run["id"], "tool_call_started")

        r = await world.client.post(world.url(f"/service-accounts/{account['id']}/revoke"), headers=world.alice.headers)
        assert r.status_code == 204
        done = await world.wait(run["id"], timeout=5)
        assert done["status"] == "stopped" and done["stop_reason"] == "manual"

        for call in (
            program.post(world.url("/runs"), json={"agent_id": world.agent_id, "task": "x"}, headers=bearer(account)),
            program.get(world.url(f"/runs/{run['id']}"), headers=bearer(account)),
        ):
            assert (await call).status_code == 401


async def test_invalid_or_foreign_tokens_get_401(world: World, make_group: Any) -> None:
    account = await _account(world)
    async with await _anonymous(world) as program:
        for token in ("hsa_nimportequoi", "", account["token"][:-3] + "abc", "Basic"):
            r = await program.get(world.url("/runs"), headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401, token
        other = await make_group("autre")
        r = await program.get(f"/groups/{other}/runs", headers=bearer(account))  # jeton d'un autre groupe
        assert r.status_code == 404
