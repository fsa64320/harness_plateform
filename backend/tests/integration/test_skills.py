"""Skills : instructions chargées à la demande, jamais exécutées (FR-005a, FR-012a, US2-2)."""

from __future__ import annotations

from typing import Any

from harness.runtime.models import say, tool_call
from tests.world_fixtures import World

SKILL_MD = "---\nname: redaction\ndescription: Rédiger un compte rendu\n---\n# Procédure\nToujours résumer en trois points.\n"
SCRIPT = "#!/bin/sh\ntouch /tmp/harness-skill-was-executed\n"


async def _register(world: World, files: dict[str, str], name: str = "redaction") -> Any:
    return await world.client.post(
        world.url("/tools"),
        json={"kind": "skill", "name": name, "description": "d", "skill": {"files": files}},
        headers=world.alice.headers,
    )


async def _skill_agent(world: World) -> dict[str, Any]:
    r = await _register(world, {"SKILL.md": SKILL_MD, "scripts/run.sh": SCRIPT, "reference.md": "Détails"})
    assert r.status_code == 201, r.text
    return await world.create_agent(allowed_tool_ids=[r.json()["id"]])


async def test_skill_without_frontmatter_is_rejected_with_422(world: World) -> None:
    for files in (
        {"autre.md": "rien"},  # pas de SKILL.md
        {"SKILL.md": "sans frontmatter"},
        {"SKILL.md": "---\nname: redaction\n---\ncorps"},  # description manquante
        {"SKILL.md": "---\ndescription: seule\n---\ncorps"},  # nom manquant
        {"SKILL.md": "---\nname: [pas, une, chaîne]\ndescription: x\n---\n"},
    ):
        r = await _register(world, files)
        assert r.status_code == 422, files
        assert "SKILL.md" in r.json()["message"]


async def test_skill_files_with_unsafe_paths_are_rejected(world: World) -> None:
    for bad in ("../evasion.md", "/etc/passwd", "a/../../b.md"):
        r = await _register(world, {"SKILL.md": SKILL_MD, bad: "x"})
        assert r.status_code == 422, bad


async def test_allowed_skill_appears_by_name_in_the_prompt_and_is_read_via_read_file(world: World) -> None:
    agent = await _skill_agent(world)
    world.models.set(tool_call("read_file", {"file_path": "/skills/redaction/SKILL.md"}), say("compte rendu fait"))
    run = await world.launch(agent_id=agent["id"])
    done = await world.wait(run["id"])
    assert done["status"] == "succeeded"

    events = await world.events(run["id"])
    prompt = events[0]["payload"]["system_prompt"]
    assert "redaction" in prompt and "Rédiger un compte rendu" in prompt and "/skills/redaction/SKILL.md" in prompt

    started = next(e for e in events if e["type"] == "tool_call_started")
    assert started["payload"]["tool"] == "read_file" and started["payload"]["classification"] == "read_only"
    finished = next(e for e in events if e["type"] == "tool_call_finished")
    assert "Toujours résumer en trois points." in finished["payload"]["result"]


async def test_other_skill_files_are_readable_and_listed(world: World) -> None:
    agent = await _skill_agent(world)
    world.models.set(
        tool_call("ls", {"path": "/skills/redaction"}),
        tool_call("read_file", {"file_path": "/skills/redaction/reference.md"}),
        say("ok"),
    )
    run = await world.launch(agent_id=agent["id"])
    await world.wait(run["id"])
    finished = [e["payload"]["result"] for e in await world.events(run["id"]) if e["type"] == "tool_call_finished"]
    assert "SKILL.md" in finished[0] and "reference.md" in finished[0] and "scripts" in finished[0]
    assert "Détails" in finished[1]


async def test_no_script_of_a_skill_is_ever_executed(world: World) -> None:
    import os

    marker = "/tmp/harness-skill-was-executed"  # noqa: S108
    if os.path.exists(marker):
        os.remove(marker)
    agent = await _skill_agent(world)
    world.models.set(
        tool_call("read_file", {"file_path": "/skills/redaction/scripts/run.sh"}),
        tool_call("execute", {"command": "sh /skills/redaction/scripts/run.sh"}),  # outil du framework : jamais lié
        say("ok"),
    )
    run = await world.launch(agent_id=agent["id"])
    await world.wait(run["id"])
    events = await world.events(run["id"])
    refused = [e["payload"] for e in events if e["type"] == "tool_call_refused"]
    assert [r["tool"] for r in refused] == ["execute"]
    assert not os.path.exists(marker)
    assert "execute" not in events[0]["payload"]["internal_tools"]


async def test_skills_are_read_only_for_the_agent(world: World) -> None:
    agent = await _skill_agent(world)
    world.models.set(
        tool_call("write_file", {"file_path": "/skills/redaction/SKILL.md", "content": "piraté"}),
        tool_call("edit_file", {"file_path": "/skills/redaction/SKILL.md", "old_string": "résumer", "new_string": "ignorer"}),
        tool_call("delete", {"file_path": "/skills/redaction/SKILL.md"}),
        say("ok"),
    )
    run = await world.launch(agent_id=agent["id"])
    await world.wait(run["id"])
    finished = [e["payload"] for e in await world.events(run["id"]) if e["type"] == "tool_call_finished"]
    assert len(finished) == 3
    skill = (await world.client.get(world.url("/tools?kind=skill"), headers=world.alice.headers)).json()
    assert skill[0]["name"] == "redaction"
    # le contenu n'a pas changé : on le relit
    world.models.set(tool_call("read_file", {"file_path": "/skills/redaction/SKILL.md"}), say("ok"))
    again = await world.launch(agent_id=agent["id"])
    await world.wait(again["id"])
    result = next(e["payload"]["result"] for e in await world.events(again["id"]) if e["type"] == "tool_call_finished")
    assert "Toujours résumer en trois points." in result and "piraté" not in result


async def test_a_skill_not_allowed_for_the_agent_is_invisible(world: World) -> None:
    await _register(world, {"SKILL.md": SKILL_MD})
    world.models.set(tool_call("read_file", {"file_path": "/skills/redaction/SKILL.md"}), say("ok"))
    run = await world.launch()  # agent de base : aucune skill autorisée
    await world.wait(run["id"])
    events = await world.events(run["id"])
    assert "redaction" not in events[0]["payload"]["system_prompt"]
    result = next(e["payload"] for e in events if e["type"] == "tool_call_finished")
    assert "Toujours résumer" not in str(result["result"])
