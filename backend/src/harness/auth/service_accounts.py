"""Comptes de service : authentification par jeton (complété par la story 4)."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from harness.auth.permissions import Principal


async def authenticate_token(session: AsyncSession, token: str) -> Principal:
    raise HTTPException(status_code=401, detail="Jeton invalide")
