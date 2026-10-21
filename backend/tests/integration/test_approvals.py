"""Approbation humaine des appels à effet externe (US1-5, FR-005, FR-005b, SC-004)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import update

from harness.db.models.runs import ApprovalRequest
from harness.runtime.models import say, tool_call
from tests.world_fixtures import World

SEND = tool_call("crm_send_message", {"text": "salut"})


async def _awaiting(world: World, run_id: str) -> dict[str, Any]:
    await world.wait_for_event(run_id, "approval_requested")
    pending = [a for a in await world.pending_approvals() if a["run_id"] == run_id]
    assert len(pending) == 1
    return pending[0]


async def _decide(world: World, approval: dict[str, Any], actor: Any, decision: str) -> Any:
    return await world.client.post(
        world.url(f"/approvals/{approval['id']}/decision"), json={"decision": decision}, headers=actor.headers
    )


async def test_unclassified_tool_pauses_the_run_until_a_decision(world: World) -> None:
    world.models.set(SEND, say("envoyé"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])

    assert (await world.run(run["id"]))["status"] == "awaiting_approval"
    assert approval["status"] == "pending" and approval["tool_operation"] == "crm_send_message"
    assert approval["arguments"] == {"text": "salut"}
    assert approval["no_eligible_approver"] is False
    assert world.mcp.calls == [] or all(c[0] != "send_message" for c in world.mcp.calls)


async def test_a_member_who_is_neither_launcher_nor_approver_cannot_decide(world: World) -> None:
    world.models.set(SEND, say("envoyé"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])

    r = await _decide(world, approval, world.carol, "approve")
    assert r.status_code == 403
    assert (await world.run(run["id"]))["status"] == "awaiting_approval"


async def test_the_launcher_can_approve_and_the_call_then_runs(world: World) -> None:
    world.models.set(SEND, say("envoyé"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])

    r = await _decide(world, approval, world.bob, "approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_by"] == str(world.bob.person_id)

    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"
    assert ("send_message", {"text": "salut"}) in world.mcp.calls
    decided = [e for e in await world.events(run["id"]) if e["type"] == "approval_decided"]
    assert decided[0]["payload"]["decision"] == "approve"
    assert decided[0]["payload"]["decided_by"] == str(world.bob.person_id)


async def test_an_approver_can_decide_on_anothers_run_and_reject_skips_the_call(world: World) -> None:
    world.models.set(SEND, say("refusé, je m'arrête"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])

    r = await _decide(world, approval, world.alice, "reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"
    assert not [c for c in world.mcp.calls if c[0] == "send_message"]
    decided = [e for e in await world.events(run["id"]) if e["type"] == "approval_decided"]
    assert decided[0]["payload"]["decision"] == "reject"
    assert decided[0]["payload"]["decided_by"] == str(world.alice.person_id)


async def test_a_decided_request_cannot_be_decided_again(world: World) -> None:
    world.models.set(SEND, say("envoyé"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])
    assert (await _decide(world, approval, world.bob, "approve")).status_code == 200
    assert (await _decide(world, approval, world.alice, "reject")).status_code == 409


async def test_a_request_past_its_deadline_expires_and_the_run_ends_expired(world: World, owner_session: Any) -> None:
    world.models.set(SEND, say("envoyé"))
    run = await world.launch(world.bob)
    approval = await _awaiting(world, run["id"])

    async with owner_session() as s, s.begin():
        await s.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == uuid.UUID(approval["id"]))
            .values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
        )
    await world.runner.expire_overdue_approvals()

    done = await world.run(run["id"])
    assert done["status"] == "expired" and done["stop_reason"] == "approval_timeout"
    assert [a["status"] for a in await world.pending_approvals()] == []
    events = await world.events(run["id"])
    assert [e["payload"]["decision"] for e in events if e["type"] == "approval_decided"] == ["expired"]
    assert events[-1]["type"] == "run_ended" and events[-1]["payload"]["status"] == "expired"
    assert not [c for c in world.mcp.calls if c[0] == "send_message"]


async def test_read_only_tools_never_ask_for_approval(world: World) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("ok"))
    run = await world.launch(world.bob)
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"
    assert "approval_requested" not in await world.event_types(run["id"])


async def test_external_effect_classification_also_requires_approval(world: World) -> None:
    r = await world.client.put(
        world.url(f"/tools/{world.tool_id}/operations/send_message/classification"),
        json={"classification": "external_effect"},
        headers=world.alice.headers,
    )
    assert r.status_code == 200
    world.models.set(SEND, say("ok"))
    run = await world.launch(world.bob)
    await _awaiting(world, run["id"])
