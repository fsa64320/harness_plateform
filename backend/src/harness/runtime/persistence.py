"""Persistance des exécutions : `AsyncPostgresSaver` seul (pas de store LangGraph), dans le schéma
`langgraph` (research R-07). La mémoire et les skills vivent dans des tables SQL à part, sous RLS."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit, urlunsplit

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SCHEMA = "langgraph"


def _libpq_url(url: str, *, with_schema: bool = True) -> str:
    """URL SQLAlchemy (`postgresql+psycopg://`) -> URL libpq, avec `search_path` sur le schéma."""
    parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://", 1))
    query = parts.query
    if with_schema:
        option = f"options={quote(f'-c search_path={SCHEMA}')}"
        query = f"{query}&{option}" if query else option
    return urlunsplit(parts._replace(query=query))


async def prepare_schema(owner_url: str, app_role: str) -> None:
    """Crée le schéma `langgraph` et ses tables avec le rôle propriétaire, puis ouvre les droits DML
    au rôle applicatif (qui n'a ni SUPERUSER ni BYPASSRLS)."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(_libpq_url(owner_url, with_schema=False), autocommit=True) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
    async with AsyncPostgresSaver.from_conn_string(_libpq_url(owner_url)) as saver:
        await saver.setup()
    role, schema = sql.Identifier(app_role), sql.Identifier(SCHEMA)
    async with await psycopg.AsyncConnection.connect(_libpq_url(owner_url, with_schema=False), autocommit=True) as conn:
        await conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
        await conn.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(schema, role)
        )


@asynccontextmanager
async def open_checkpointer(app_url: str, *, owner_url: str | None = None) -> AsyncIterator[AsyncPostgresSaver]:
    """Checkpointer connecté avec le rôle applicatif. Avec `owner_url`, le schéma est d'abord préparé."""
    if owner_url:
        app_role = urlsplit(app_url).username or ""
        await prepare_schema(owner_url, app_role)
    pool = AsyncConnectionPool(
        _libpq_url(app_url),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        min_size=1,
        max_size=10,
        open=False,
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        if not owner_url:
            await saver.setup()
        yield saver
    finally:
        await pool.close()
