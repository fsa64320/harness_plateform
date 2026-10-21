"""Skills : procédures réutilisables que l'agent charge à la demande (FR-010, FR-012a).

Une skill est un ensemble de fichiers dont `SKILL.md` porte, en frontmatter, `name` et `description`.
La plateforme n'exécute jamais un script de skill : les fichiers ne sont que lus (instructions,
documents de référence)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.db.models.catalog import Tool, ToolKind
from harness.groups.audit import record_admin_action

MAX_FILES = 64
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


class SkillInvalid(Exception):
    """Skill refusée (réponse 422)."""


class SkillConflict(Exception):
    pass


def _safe_path(path: str) -> bool:
    parts = path.split("/")
    return bool(path) and not path.startswith("/") and "\\" not in path and all(p not in ("", ".", "..") for p in parts)


def validate_skill(files: dict[str, str]) -> tuple[str, str]:
    """Contrôle les fichiers et le frontmatter de `SKILL.md` ; renvoie `(name, description)`."""
    if not files:
        raise SkillInvalid("Une skill doit contenir au moins SKILL.md")
    if len(files) > MAX_FILES:
        raise SkillInvalid(f"Trop de fichiers (maximum {MAX_FILES})")
    total = 0
    for path, content in files.items():
        if not _safe_path(path):
            raise SkillInvalid(f"Chemin de fichier invalide : {path!r} (relatif, sans « .. »)")
        size = len(content.encode())
        total += size
        if size > MAX_FILE_BYTES:
            raise SkillInvalid(f"Fichier trop volumineux : {path} (maximum {MAX_FILE_BYTES} octets)")
    if total > MAX_TOTAL_BYTES:
        raise SkillInvalid("Skill trop volumineuse")
    if "SKILL.md" not in files:
        raise SkillInvalid("SKILL.md est requis")
    match = _FRONTMATTER.match(files["SKILL.md"])
    if match is None:
        raise SkillInvalid("SKILL.md doit commencer par un frontmatter YAML (name, description)")
    try:
        meta: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillInvalid(f"Frontmatter de SKILL.md illisible : {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillInvalid("Le frontmatter de SKILL.md doit être un objet YAML")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise SkillInvalid("SKILL.md : `name` est requis (lettres, chiffres, « _ » et « - »)")
    if not isinstance(description, str) or not description.strip():
        raise SkillInvalid("SKILL.md : `description` est requise")
    return name, description.strip()


async def register_skill(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    description: str,
    files: dict[str, str],
) -> Tool:
    skill_name, skill_description = validate_skill(files)
    if name != skill_name:
        raise SkillInvalid(f"SKILL.md : `name` ({skill_name}) doit égaler le nom de l'outil ({name})")
    existing = await session.execute(select(Tool.id).where(Tool.kind == ToolKind.SKILL, Tool.name == name))
    if existing.first() is not None:
        raise SkillConflict(f"Une skill « {name} » existe déjà dans ce groupe")
    tool = Tool(
        group_id=group_id,
        kind=ToolKind.SKILL,
        name=name,
        description=description or skill_description,
        created_by=actor_id,
        skill_name=skill_name,
        skill_description=skill_description,
        files=files,
    )
    session.add(tool)
    await session.flush()
    await record_admin_action(
        session,
        group_id=group_id,
        actor_id=actor_id,
        action="tool.register",
        target_type="tool",
        target_id=tool.id,
        details={"kind": "skill", "name": name, "files": sorted(files)},
    )
    return tool
