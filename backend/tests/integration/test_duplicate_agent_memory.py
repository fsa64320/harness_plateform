"""Un agent dupliqué démarre sans mémoire d'agent mais garde l'accès à la mémoire de groupe (cas limite)."""

from __future__ import annotations

import uuid

from harness.runtime.models import say, tool_call
from tests.memory_helpers import entries_of, person, seed_entry
from tests.world_fixtures import World


async def test_a_duplicated_agent_starts_with_empty_agent_memory_and_keeps_group_memory_access(world: World) -> None:
    original = await world.create_agent(group_memory_enabled=True)
    await seed_entry(
        world.group_id, scope="agent", agent_id=uuid.UUID(original["id"]), path="prefs.md", content="secret de l'original",
        actor=person(world.alice.person_id),
    )
    await seed_entry(world.group_id, scope="group", path="faits/a.md", content="commun", actor=person(world.alice.person_id))

    r = await world.client.post(world.url(f"/agents/{original['id']}/duplicate"), headers=world.bob.headers)
    assert r.status_code == 201
    copy = r.json()
    assert copy["id"] != original["id"] and copy["group_memory_enabled"] is True

    assert await entries_of(world.group_id, scope="agent", agent_id=uuid.UUID(copy["id"])) == []
    listing = await world.client.get(world.url(f"/agents/{copy['id']}/memory"), headers=world.bob.headers)
    assert listing.json() == []

    world.models.set(
        tool_call("read_file", {"file_path": "/memories/prefs.md"}),  # la mémoire de l'original n'est pas partagée
        tool_call("read_file", {"file_path": "/group-memory/faits/a.md"}),  # la mémoire de groupe, si
        say("ok"),
    )
    run = await world.launch(agent_id=copy["id"])
    await world.wait(run["id"])
    results = [e["payload"]["result"] for e in await world.events(run["id"]) if e["type"] == "tool_call_finished"]
    assert "secret de l'original" not in results[0]
    assert "commun" in results[1]
    scopes = {e["payload"]["scope"] for e in await world.events(run["id"]) if e["type"] == "memory_read"}
    assert scopes == {"group"}  # jamais « agent » : la mémoire de l'original n'est pas partagée

    # l'original n'a rien perdu
    assert len(await entries_of(world.group_id, scope="agent", agent_id=uuid.UUID(original["id"]))) == 1
