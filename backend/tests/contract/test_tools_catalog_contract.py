"""Contrat : catalogue complet (skills, hooks, retrait) et identifiants jamais renvoyés (FR-010, FR-014)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select

from harness.db.models.audit import AdminAuditEntry
from harness.db.session import get_sessionmaker, group_context
from tests.world_fixtures import World

SKILL_MD = "---\nname: redaction\ndescription: Rédiger un compte rendu\n---\nÉtapes : 1. lire 2. résumer\n"
SECRET = "s3cr3t-credential-value"  # noqa: S105


def skill_body(name: str = "redaction", **files: str) -> dict[str, Any]:
    return {
        "kind": "skill",
        "name": name,
        "description": "Procédure de rédaction",
        "skill": {"files": {"SKILL.md": SKILL_MD.replace("redaction", name), **files}},
    }


async def test_register_a_skill(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    r = await world.client.post(world.url("/tools"), json=skill_body(), headers=world.alice.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    tool = r.json()
    assert tool["kind"] == "skill" and tool["status"] == "active" and tool["has_credential"] is False
    assert tool["operations"] == []  # une skill est « lecture seule » d'office, sans opération classée

    listing = await world.client.get(world.url("/tools?kind=skill"), headers=world.bob.headers)
    assert_matches_contract(listing)
    assert [t["name"] for t in listing.json()] == ["redaction"]


async def test_register_a_rule_hook(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    body = {
        "kind": "hook",
        "name": "bloque-effets-externes",
        "hook": {
            "events": ["before_tool_call"],
            "hook_type": "rule",
            "condition": {"classification": "external_effect"},
            "decision": "block",
            "priority": 10,
        },
    }
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    assert r.json()["hook"]["decision"] == "block" and r.json()["hook"]["priority"] == 10


async def test_register_a_webhook_hook_never_returns_its_signing_secret(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    body = {
        "kind": "hook",
        "name": "validation-externe",
        "hook": {
            "events": ["before_tool_call", "run_end"],
            "hook_type": "webhook",
            "url": "https://hooks.example.test/decide",
            "signing_secret": SECRET,
        },
    }
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    assert SECRET not in r.text
    tool_id = r.json()["id"]
    for path in ("/tools", f"/tools/{tool_id}"):
        assert SECRET not in (await world.client.get(world.url(path), headers=world.alice.headers)).text


async def test_mcp_credential_is_write_only_and_has_credential_is_true(
    world: World, assert_matches_contract: Callable[[httpx.Response], None]
) -> None:
    body = {"kind": "mcp_server", "name": "vault", "mcp": {"url": world.mcp.url, "credential": SECRET}}
    r = await world.client.post(world.url("/tools"), json=body, headers=world.alice.headers)
    assert r.status_code == 201
    assert_matches_contract(r)
    assert r.json()["has_credential"] is True
    assert SECRET not in r.text
    tool_id = r.json()["id"]
    for path in ("/tools", f"/tools/{tool_id}", "/tools?kind=mcp_server"):
        assert SECRET not in (await world.client.get(world.url(path), headers=world.bob.headers)).text


async def test_remove_a_tool_from_the_catalog(world: World, assert_matches_contract: Callable[[httpx.Response], None]) -> None:
    created = (await world.client.post(world.url("/tools"), json=skill_body(), headers=world.alice.headers)).json()
    r = await world.client.delete(world.url(f"/tools/{created['id']}"), headers=world.alice.headers)
    assert r.status_code == 204
    assert_matches_contract(r)
    assert "redaction" not in [t["name"] for t in (await world.client.get(world.url("/tools"), headers=world.bob.headers)).json()]
    detail = await world.client.get(world.url(f"/tools/{created['id']}"), headers=world.bob.headers)
    assert detail.json()["status"] == "retired"


async def test_only_a_tool_manager_can_register_or_remove(world: World) -> None:
    c, bob = world.client, world.bob.headers
    assert (await c.post(world.url("/tools"), json=skill_body(), headers=bob)).status_code == 403
    assert (await c.delete(world.url(f"/tools/{world.tool_id}"), headers=bob)).status_code == 403


async def test_every_catalog_change_is_audited(world: World) -> None:
    created = (await world.client.post(world.url("/tools"), json=skill_body(), headers=world.alice.headers)).json()
    await world.client.delete(world.url(f"/tools/{created['id']}"), headers=world.alice.headers)
    async with get_sessionmaker()() as s, s.begin():
        await group_context(s, world.group_id)
        entries = (await s.execute(select(AdminAuditEntry).where(AdminAuditEntry.target_id == created["id"]))).scalars().all()
    assert [e.action for e in entries] == ["tool.register", "tool.retire"]
    assert all(e.actor_id == world.alice.person_id for e in entries)
    assert SECRET not in str([e.details for e in entries])


async def test_duplicate_name_is_a_conflict(world: World) -> None:
    await world.client.post(world.url("/tools"), json=skill_body(), headers=world.alice.headers)
    r = await world.client.post(world.url("/tools"), json=skill_body(), headers=world.alice.headers)
    assert r.status_code == 409
