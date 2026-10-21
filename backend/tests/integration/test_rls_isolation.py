"""Isolation inter-groupes par Row-Level Security (FR-022, SC-009)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Table, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harness.db.base import Base, GroupScopedMixin
from harness.db.session import get_sessionmaker, group_context


def _group_scoped_tables() -> list[Table]:
    import harness.db.models  # noqa: F401

    tables = []
    for mapper in Base.registry.mappers:
        if issubclass(mapper.class_, GroupScopedMixin):
            tables.append(mapper.local_table)
    return sorted(tables, key=lambda t: t.name)


def _dummy(column: sa.Column[Any]) -> Any:
    t = column.type
    if isinstance(t, postgresql.UUID | sa.Uuid):
        return uuid.uuid4()
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, sa.Integer | sa.Float | sa.Numeric):
        return 0
    if isinstance(t, sa.DateTime):
        return dt.datetime.now(dt.UTC)
    if isinstance(t, sa.JSON | postgresql.JSONB):
        return {}
    if isinstance(t, sa.ARRAY):
        return []
    if isinstance(t, sa.LargeBinary):
        return b"x"
    return "x"


def _needs_value(column: sa.Column[Any]) -> bool:
    return (
        not column.nullable
        and column.default is None
        and column.server_default is None
        and not column.primary_key
    )


async def _seed(owner: AsyncEngine, table: Table, group_id: uuid.UUID) -> uuid.UUID:
    values = {c.name: _dummy(c) for c in table.columns if _needs_value(c)}
    values["group_id"] = group_id
    row_id = uuid.uuid4()
    values["id"] = row_id
    async with owner.begin() as conn:
        # FK ignorées : seule la RLS est testée ici (superuser requis, ce que l'owner de test est).
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(table.insert().values(**values))
    return row_id


def test_there_are_group_scoped_tables() -> None:
    names = {t.name for t in _group_scoped_tables()}
    assert {"memberships", "admin_audit_entries"} <= names


async def test_application_role_cannot_bypass_rls(session: AsyncSession) -> None:
    row = (
        await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False


@pytest.mark.parametrize("table", _group_scoped_tables(), ids=lambda t: t.name)
async def test_rls_is_enabled_and_forced(session: AsyncSession, table: Table) -> None:
    row = (
        await session.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n"),
            {"n": table.name},
        )
    ).one()
    assert row.relrowsecurity and row.relforcerowsecurity, f"RLS manquante sur {table.name}"


@pytest.mark.parametrize("table", _group_scoped_tables(), ids=lambda t: t.name)
async def test_rows_of_group_a_are_invisible_to_group_b(
    owner_engine: AsyncEngine, clean_db: None, table: Table
) -> None:
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    row_id = await _seed(owner_engine, table, group_a)
    maker: async_sessionmaker[AsyncSession] = get_sessionmaker()

    async def visible(group: uuid.UUID | None) -> int:
        async with maker() as s, s.begin():
            if group is not None:
                await group_context(s, group)
            res = await s.execute(
                sa.select(sa.func.count()).select_from(table).where(table.c.id == row_id)
            )
            return int(res.scalar_one())

    assert await visible(group_a) == 1
    assert await visible(group_b) == 0
    assert await visible(None) == 0  # app.group_id non défini

    # Ni modifiable ni supprimable depuis le groupe B.
    async with maker() as s, s.begin():
        await group_context(s, group_b)
        upd = await s.execute(
            table.update().where(table.c.id == row_id).values(group_id=group_b)
        )
        assert upd.rowcount == 0
        dele = await s.execute(table.delete().where(table.c.id == row_id))
        assert dele.rowcount == 0

    assert await visible(group_a) == 1


@pytest.mark.parametrize("table", _group_scoped_tables(), ids=lambda t: t.name)
async def test_cannot_insert_into_another_group(clean_db: None, table: Table) -> None:
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    values = {c.name: _dummy(c) for c in table.columns if _needs_value(c)}
    values["group_id"] = group_a
    values["id"] = uuid.uuid4()
    async with get_sessionmaker()() as s:
        with pytest.raises(sa.exc.DBAPIError, match="row-level security"):
            async with s.begin():
                await group_context(s, group_b)
                await s.execute(table.insert().values(**values))
