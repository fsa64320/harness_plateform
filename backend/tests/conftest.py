"""Fixtures partagées : PostgreSQL 16 réel (testcontainers), migrations, client HTTP, identités.

Aucun test n'appelle Ollama (constitution, principe I) : le modèle est un `FakeProvider`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from harness.auth.permissions import Principal, current_principal
from harness.db.models.identity import Person
from harness.db.session import SessionDep

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROLE = "harness_app"
APP_PASSWORD = "test-app-role-password"  # noqa: S105 - valeur de test d'un conteneur jetable
TEST_ENCRYPTION_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

os.environ.setdefault("HARNESS_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder")
os.environ.setdefault("LANGSMITH_TRACING", "false")


@pytest.fixture(scope="session")
def postgres() -> Iterator[Any]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def db_urls(postgres: Any) -> dict[str, str]:
    host = postgres.get_container_host_ip()
    port = postgres.get_exposed_port(5432)
    base = f"{host}:{port}/{postgres.dbname}"
    return {
        "owner": f"postgresql+psycopg://{postgres.username}:{postgres.password}@{base}",
        "app": f"postgresql+psycopg://{APP_ROLE}:{APP_PASSWORD}@{base}",
    }


@pytest_asyncio.fixture(scope="session")
async def migrated_db(db_urls: dict[str, str]) -> AsyncIterator[dict[str, str]]:
    """`alembic upgrade head` sur un PostgreSQL vierge, puis moteur applicatif (rôle sans BYPASSRLS)."""
    from harness import config
    from harness.db import session as db_session

    os.environ["DATABASE_URL"] = db_urls["app"]
    os.environ["MIGRATION_DATABASE_URL"] = db_urls["owner"]
    config.get_settings.cache_clear()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    await asyncio.to_thread(command.upgrade, cfg, "head")

    db_session.init_engine(db_urls["app"])
    yield db_urls
    await db_session.dispose_engine()


@pytest_asyncio.fixture(scope="session")
async def owner_engine(migrated_db: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    """Connexion propriétaire/superuser : sert aux fixtures et au nettoyage, jamais à l'application."""
    engine = create_async_engine(migrated_db["owner"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def clean_db(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    import harness.db.models  # noqa: F401  (enregistre toutes les tables)
    from harness.db.base import Base

    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def owner_session(owner_engine: AsyncEngine, clean_db: None) -> async_sessionmaker[AsyncSession]:
    """Fabrique de sessions propriétaire (contourne la RLS) pour préparer des données de test."""
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(migrated_db: dict[str, str], clean_db: None) -> AsyncIterator[AsyncSession]:
    """Session applicative (rôle sans BYPASSRLS) ; le test choisit son contexte avec `group_context`."""
    from harness.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        yield s


# --- Application et authentification -------------------------------------------------------


def install_auth_override(app: FastAPI) -> None:
    """Remplace `current_principal` : l'identité vient de l'en-tête `X-Test-Person` (id de Person)."""
    async def fake_principal(request: Request, session: SessionDep) -> Principal:
        raw = request.headers.get("x-test-person")
        if raw is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        person = await session.get(Person, uuid.UUID(raw))
        if person is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        return Principal(
            kind="person",
            id=person.id,
            email=person.email,
            is_platform_admin=person.is_platform_admin,
        )

    app.dependency_overrides[current_principal] = fake_principal


@dataclass
class Actor:
    person_id: uuid.UUID
    email: str
    group_id: uuid.UUID | None
    headers: dict[str, str] = field(default_factory=dict)


@pytest_asyncio.fixture
async def app(migrated_db: dict[str, str], clean_db: None) -> FastAPI:
    from harness.main import create_app

    application = create_app()
    install_auth_override(application)
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


@pytest.fixture
def make_group(owner_session: async_sessionmaker[AsyncSession]) -> Callable[..., Awaitable[uuid.UUID]]:
    async def _make(name: str | None = None, **settings: Any) -> uuid.UUID:
        from harness.db.models.identity import Group

        async with owner_session() as s, s.begin():
            group = Group(name=name or f"group-{uuid.uuid4().hex[:8]}", **settings)
            s.add(group)
            await s.flush()
            return group.id

    return _make


@pytest.fixture
def login_as(
    owner_session: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
) -> Callable[..., Awaitable[Actor]]:
    """`login_as(person, group, roles)` : crée (ou retrouve) la personne et son appartenance, et
    authentifie le client comme elle. `person` est son e-mail. Les rôles s'ajoutent à `member`.
    Chaque acteur porte ses `headers` pour parler en plusieurs identités dans un même test."""

    async def _login(
        person: str | None = None,
        group: uuid.UUID | None = None,
        roles: tuple[str, ...] = (),
        *,
        platform_admin: bool = False,
        **membership: Any,
    ) -> Actor:
        from sqlalchemy import select

        from harness.db.models.identity import Membership, Person

        email = person or f"{uuid.uuid4().hex[:8]}@example.test"
        async with owner_session() as s, s.begin():
            p = (await s.execute(select(Person).where(Person.email == email))).scalar_one_or_none()
            if p is None:
                p = Person(
                    oidc_subject=f"sub-{email}",
                    email=email,
                    display_name=email.split("@")[0],
                    is_platform_admin=platform_admin,
                )
                s.add(p)
                await s.flush()
            if group is not None:
                m = (
                    await s.execute(
                        select(Membership).where(
                            Membership.group_id == group, Membership.person_id == p.id
                        )
                    )
                ).scalar_one_or_none()
                all_roles = sorted({"member", *roles})
                if m is None:
                    s.add(Membership(group_id=group, person_id=p.id, roles=all_roles, **membership))
                else:
                    m.roles = all_roles
        actor = Actor(
            person_id=p.id,
            email=email,
            group_id=group,
            headers={"X-Test-Person": str(p.id)},
        )
        client.headers.update(actor.headers)
        return actor

    return _login
