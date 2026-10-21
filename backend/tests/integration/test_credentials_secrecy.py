"""Identifiants d'outils : jamais en clair hors du serveur MCP concerné (FR-014, SC-005)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World

CREDENTIAL = "vault-token-9f8e7d6c5b4a3210"  # noqa: S105


async def _vault_agent(world: World) -> dict:
    r = await world.client.post(
        world.url("/tools"),
        json={"kind": "mcp_server", "name": "vault", "mcp": {"url": world.mcp.url, "credential": CREDENTIAL}},
        headers=world.alice.headers,
    )
    assert r.status_code == 201, r.text
    ops = {o["name"]: o["id"] for o in r.json()["operations"]}
    for operation in ("whoami", "lookup"):
        await world.client.put(
            world.url(f"/tools/{r.json()['id']}/operations/{operation}/classification"),
            json={"classification": "read_only"},
            headers=world.alice.headers,
        )
    return await world.create_agent(name="vault-agent", allowed_tool_ids=[ops["whoami"], ops["lookup"]])


async def test_credential_never_appears_in_responses_logs_or_traces_and_reaches_only_the_mcp_server(
    world: World, capsys: pytest.CaptureFixture[str], owner_session
) -> None:
    agent = await _vault_agent(world)
    world.models.set(
        tool_call("vault_whoami", {}),  # l'outil renvoie l'en-tête d'authentification reçu
        tool_call("vault_lookup", {"query": f"le jeton est {CREDENTIAL}"}),  # le modèle le recopie dans un argument
        say(f"Voici le jeton : {CREDENTIAL}"),  # et dans sa réponse finale
    )
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"

    # 1. envoyé au serveur MCP, sous forme d'en-tête d'autorisation
    assert any(h.get("authorization") == f"Bearer {CREDENTIAL}" for h in world.mcp.received_headers)

    # 2. absent de toutes les réponses d'API
    surfaces = [
        await world.client.get(world.url("/tools"), headers=world.alice.headers),
        await world.client.get(world.url("/agents"), headers=world.alice.headers),
        await world.client.get(world.url("/runs"), headers=world.alice.headers),
        await world.client.get(world.url(f"/runs/{run['id']}"), headers=world.alice.headers),
        await world.client.get(world.url(f"/runs/{run['id']}/events"), headers={**world.alice.headers, "Accept": "application/json"}),
        await world.client.get(world.url("/approvals"), headers=world.alice.headers),
    ]
    for response in surfaces:
        assert CREDENTIAL not in response.text, response.request.url
    assert "«masqué»" in json.dumps(await world.events(run["id"]), ensure_ascii=False)

    # 3. absent des événements de trace en base, du résultat de l'exécution et du journal
    async with owner_session() as s:
        stored = (await s.execute(text("SELECT payload::text FROM trace_events"))).scalars().all()
        results = (await s.execute(text("SELECT result FROM runs"))).scalars().all()
        audit = (await s.execute(text("SELECT details::text FROM admin_audit_entries"))).scalars().all()
        credentials = (await s.execute(text("SELECT ciphertext, nonce FROM credentials"))).all()
    assert not any(CREDENTIAL in row for row in stored)
    assert not any(CREDENTIAL in (r or "") for r in results)
    assert not any(CREDENTIAL in row for row in audit)

    # 4. chiffré au repos
    assert len(credentials) == 1 and CREDENTIAL.encode() not in bytes(credentials[0].ciphertext)

    # 5. absent des logs de l'application
    captured = capsys.readouterr()
    assert CREDENTIAL not in captured.out + captured.err


async def test_tool_without_credential_sends_no_authorization_header(world: World) -> None:
    world.models.set(tool_call("crm_lookup", {"query": "x"}), say("ok"))
    run = await world.launch()
    await world.wait(run["id"])
    assert all("authorization" not in h for h in world.mcp.received_headers[-3:])
