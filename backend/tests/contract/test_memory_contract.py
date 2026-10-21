"""Contrat : mémoire d'agent et de groupe, révisions, propositions (FR-015 à FR-017a)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from sqlalchemy import select

from harness.db.models.memory import GroupMemoryProposal
from harness.db.session import get_sessionmaker, group_context
from tests.memory_helpers import person, seed_entry
from tests.world_fixtures import World


async def _agent_entry(world: World, path: str = "preferences.md", content: str = "réponses courtes") -> dict:
    outcome = await seed_entry(
        world.group_id, scope="agent", agent_id=uuid.UUID(world.agent_id), path=path, content=content,
        actor=person(world.alice.person_id),
    )
    return {"id": str(outcome.entry.id), "version": outcome.entry.version}


async def _proposal(world: World, path: str = "faits/tarifs.md", content: str = "Tarif 2026 : 12 €") -> str:
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        proposal = GroupMemoryProposal(
            group_id=world.group_id, run_id=None, agent_id=uuid.UUID(world.agent_id),
            proposed_path=path, proposed_content=content, status="pending",
        )
        s.add(proposal)
        await s.flush()
        return str(proposal.id)


async def test_agent_memory_is_readable_by_any_member(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    entry = await _agent_entry(world)
    r = await world.client.get(world.url(f"/agents/{world.agent_id}/memory"), headers=world.carol.headers)
    assert r.status_code == 200
    assert_matches_contract(r)
    [item] = r.json()
    assert item["id"] == entry["id"] and item["scope"] == "agent" and item["agent_id"] == world.agent_id
    assert item["path"] == "preferences.md" and item["content"] == "réponses courtes" and item["version"] == 1
    assert item["author_type"] == "person"


async def test_group_memory_is_readable_by_any_member_but_only_managers_add(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    body = {"path": "faits/horaires.md", "content": "Ouvert de 9 h à 18 h"}
    created = await world.client.post(world.url("/group-memory"), json=body, headers=world.alice.headers)
    assert created.status_code == 201
    assert_matches_contract(created)
    assert created.json()["scope"] == "group" and created.json()["agent_id"] is None

    assert (await world.client.post(world.url("/group-memory"), json=body, headers=world.bob.headers)).status_code == 403
    listing = await world.client.get(world.url("/group-memory"), headers=world.bob.headers)
    assert_matches_contract(listing)
    assert [e["path"] for e in listing.json()] == ["faits/horaires.md"]


async def test_update_with_version_and_409_on_conflict(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    entry = await _agent_entry(world)
    url = world.url(f"/memory/{entry['id']}")
    ok = await world.client.put(url, json={"content": "réponses détaillées", "version": 1}, headers=world.alice.headers)
    assert ok.status_code == 200
    assert_matches_contract(ok)
    assert ok.json()["version"] == 2 and ok.json()["content"] == "réponses détaillées"

    stale = await world.client.put(url, json={"content": "écrasement", "version": 1}, headers=world.alice.headers)
    assert stale.status_code == 409
    assert_matches_contract(stale)
    current = (await world.client.get(world.url(f"/agents/{world.agent_id}/memory"), headers=world.alice.headers)).json()
    assert current[0]["content"] == "réponses détaillées"  # rien n'a été perdu ni écrasé


async def test_revisions_record_every_change(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    entry = await _agent_entry(world)
    url = world.url(f"/memory/{entry['id']}")
    await world.client.put(url, json={"content": "v2", "version": 1}, headers=world.alice.headers)
    await world.client.delete(url, headers=world.alice.headers)

    r = await world.client.get(url + "/revisions", headers=world.carol.headers)
    assert r.status_code == 200
    assert_matches_contract(r)
    revisions = r.json()
    assert [(v["version"], v["change_type"]) for v in revisions] == [(1, "create"), (2, "update"), (3, "delete")]
    assert revisions[1]["previous_content"] == "réponses courtes"
    assert revisions[1]["changed_by"] == f"person:{world.alice.person_id}"


async def test_fr_016_agent_memory_rights(world: World) -> None:
    entry = await _agent_entry(world)
    url = world.url(f"/memory/{entry['id']}")
    body = {"content": "modifié", "version": 1}
    # ni l'auteur de l'agent ni administrateur : refusé
    assert (await world.client.put(url, json=body, headers=world.bob.headers)).status_code == 403
    assert (await world.client.delete(url, headers=world.bob.headers)).status_code == 403
    # l'auteur de l'agent (alice, aussi administratrice) peut
    assert (await world.client.put(url, json=body, headers=world.alice.headers)).status_code == 200


async def test_fr_016_an_admin_who_is_not_the_author_can_edit_agent_memory(world: World, login_as) -> None:  # type: ignore[no-untyped-def]
    other_admin = await login_as("dave@example.test", world.group_id, ("admin",))
    entry = await _agent_entry(world)
    r = await world.client.put(
        world.url(f"/memory/{entry['id']}"), json={"content": "x", "version": 1}, headers=other_admin.headers
    )
    assert r.status_code == 200


async def test_fr_016_group_memory_is_writable_only_by_group_memory_managers(world: World, login_as) -> None:  # type: ignore[no-untyped-def]
    outcome = await seed_entry(world.group_id, scope="group", path="faits/a.md", content="a", actor=person(world.alice.person_id))
    url = world.url(f"/memory/{outcome.entry.id}")
    admin_only = await login_as("erin@example.test", world.group_id, ("admin",))
    for actor in (world.bob, admin_only):
        assert (await world.client.put(url, json={"content": "z", "version": 1}, headers=actor.headers)).status_code == 403
        assert (await world.client.delete(url, headers=actor.headers)).status_code == 403
    assert (await world.client.put(url, json={"content": "z", "version": 1}, headers=world.alice.headers)).status_code == 200
    assert (await world.client.delete(url, headers=world.alice.headers)).status_code == 204


async def test_delete_removes_the_entry_from_listings(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    entry = await _agent_entry(world)
    r = await world.client.delete(world.url(f"/memory/{entry['id']}"), headers=world.alice.headers)
    assert r.status_code == 204
    assert_matches_contract(r)
    listing = await world.client.get(world.url(f"/agents/{world.agent_id}/memory"), headers=world.alice.headers)
    assert listing.json() == []


async def test_proposals_list_and_decision(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    proposal_id = await _proposal(world)
    listing = await world.client.get(world.url("/group-memory/proposals?status=pending"), headers=world.bob.headers)
    assert listing.status_code == 200
    assert_matches_contract(listing)
    assert [p["id"] for p in listing.json()] == [proposal_id]

    url = world.url(f"/group-memory/proposals/{proposal_id}/decision")
    assert (await world.client.post(url, json={"decision": "accept"}, headers=world.bob.headers)).status_code == 403
    accepted = await world.client.post(url, json={"decision": "accept"}, headers=world.alice.headers)
    assert accepted.status_code == 200
    assert_matches_contract(accepted)
    assert accepted.json()["status"] == "accepted" and accepted.json()["decided_by"] == str(world.alice.person_id)

    group = (await world.client.get(world.url("/group-memory"), headers=world.bob.headers)).json()
    assert [(e["path"], e["content"]) for e in group] == [("faits/tarifs.md", "Tarif 2026 : 12 €")]
    again = await world.client.post(url, json={"decision": "reject"}, headers=world.alice.headers)
    assert again.status_code == 409


async def test_rejected_proposal_never_reaches_group_memory(world: World) -> None:
    proposal_id = await _proposal(world)
    r = await world.client.post(
        world.url(f"/group-memory/proposals/{proposal_id}/decision"), json={"decision": "reject"}, headers=world.alice.headers
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert (await world.client.get(world.url("/group-memory"), headers=world.bob.headers)).json() == []
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        status = (await s.execute(select(GroupMemoryProposal.status))).scalar_one()
    assert status == "rejected"
