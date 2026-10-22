"""CLI d'administration de la plateforme : `harness-admin create-group <nom> --admin <email>`."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from harness.config import get_settings
from harness.db import session as db_session
from harness.groups import service as groups


async def create_group(name: str, admin_emails: list[str]) -> uuid.UUID:
    """Crée le groupe et ses premiers administrateurs, et consigne l'action (FR-022a, FR-024)."""
    async with db_session.get_sessionmaker()() as session, session.begin():
        group = await groups.create_group(session, name=name, admin_emails=admin_emails, actor_id=None, via="cli")
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
