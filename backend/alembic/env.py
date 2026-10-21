"""Environnement Alembic asynchrone. Les migrations s'exécutent avec le rôle propriétaire
(`MIGRATION_DATABASE_URL`), jamais avec le rôle applicatif soumis à la RLS."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import harness.db.models  # noqa: F401
from harness.config import get_settings
from harness.db.base import Base

target_metadata = Base.metadata


def _url() -> str:
    settings = get_settings()
    return settings.migration_database_url or settings.database_url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
