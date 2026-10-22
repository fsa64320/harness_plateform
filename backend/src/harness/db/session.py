"""Moteur et sessions asynchrones, et contextes RLS (`app.group_id`, `app.person_id`)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None
_maker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> None:
    global _engine, _maker
    _engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=30)
    _maker = async_sessionmaker(_engine, expire_on_commit=False)


async def dispose_engine() -> None:
    global _engine, _maker
    if _engine is not None:
        await _engine.dispose()
    _engine = _maker = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _maker is None:
        raise RuntimeError("Moteur de base de données non initialisé (init_engine)")
    return _maker


async def get_session() -> AsyncIterator[AsyncSession]:
    """Une transaction par requête : validée si la route réussit, annulée sinon."""
    async with get_sessionmaker()() as session, session.begin():
        yield session


# `scope="function"` : la transaction est validée avant l'envoi de la réponse, pour qu'un client
# qui enchaîne deux appels voie toujours le résultat du premier.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


async def _set_local(session: AsyncSession, name: str, value: str) -> None:
    # `set_config(..., true)` équivaut à SET LOCAL, mais accepte un paramètre lié.
    await session.execute(text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value})


async def group_context(session: AsyncSession, group_id: uuid.UUID) -> None:
    """Exécute `SET LOCAL app.group_id` : toute la suite de la transaction voit ce seul groupe."""
    await _set_local(session, "app.group_id", str(group_id))


async def person_context(session: AsyncSession, person_id: uuid.UUID) -> None:
    """Permet de lister ses propres appartenances avant de connaître le groupe."""
    await _set_local(session, "app.person_id", str(person_id))


async def platform_context(session: AsyncSession) -> None:
    """Opérations de plateforme (créer, lister, désactiver des groupes). N'ouvre aucun contenu."""
    await _set_local(session, "app.platform_admin", "on")
