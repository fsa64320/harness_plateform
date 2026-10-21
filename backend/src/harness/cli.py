"""CLI d'administration de la plateforme : `harness-admin create-group <nom> --admin <email>`."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from harness.config import get_settings
from harness.db import session as db_session
from harness.db.models.identity import Group, Membership, Person
from harness.db.session import group_context, platform_context
from harness.groups.audit import record_admin_action


async def create_group(name: str, admin_emails: list[str]) -> uuid.UUID:
    """Crée le groupe et ses premiers administrateurs, et consigne l'action (FR-022a, FR-024)."""
    async with db_session.get_sessionmaker()() as session, session.begin():
        await platform_context(session)
        group = Group(name=name)
        session.add(group)
        await session.flush()
        await group_context(session, group.id)
        for email in admin_emails:
            person = (
                await session.execute(select(Person).where(Person.email == email))
            ).scalar_one_or_none()
            if person is None:
                person = Person(email=email, display_name=email.split("@")[0])
                session.add(person)
                await session.flush()
            session.add(
                Membership(group_id=group.id, person_id=person.id, roles=["admin", "member"])
            )
        await record_admin_action(
            session,
            group_id=group.id,
            actor_id=None,
            action="group.create",
            target_type="group",
            target_id=group.id,
            details={"name": name, "admins": admin_emails, "via": "cli"},
        )
        return group.id


async def _main(args: argparse.Namespace) -> int:
    db_session.init_engine(get_settings().database_url)
    try:
        if args.command == "create-group":
            group_id = await create_group(args.name, args.admin)
            print(f"Groupe « {args.name} » créé : {group_id}")
    finally:
        await db_session.dispose_engine()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-group", help="Créer un groupe et son premier administrateur")
    create.add_argument("name")
    create.add_argument("--admin", action="append", required=True, help="e-mail d'un administrateur")
    sys.exit(asyncio.run(_main(parser.parse_args())))
