"""Base déclarative : id UUID, horodatages, et mixin de rattachement à un groupe."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class _Declarative(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(_Declarative):
    """Toutes les tables métier ont `id` (UUID), `created_at` et `updated_at`."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GroupScopedMixin:
    """Table rattachée à un groupe : protégée par Row-Level Security (voir `db/rls.py`)."""

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
