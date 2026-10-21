"""Connexion SSO (OIDC, Authlib) : login, callback, logout. La session est un cookie signé,
HttpOnly, Secure, SameSite=Lax ; la personne est créée ou retrouvée par `oidc_subject` (FR-019)."""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from harness.auth.permissions import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    make_session_cookie,
)
from harness.config import get_settings
from harness.db.models.identity import Person
from harness.db.session import SessionDep

router = APIRouter(prefix="/auth", tags=["auth"])

_oauth: OAuth | None = None


def _client() -> Any:
    global _oauth
    settings = get_settings()
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise HTTPException(status_code=503, detail="SSO non configuré")
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="sso",
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth.sso


async def upsert_person(session: SessionDep, *, subject: str, email: str, name: str) -> Person:
    """Retrouve la personne par sujet OIDC, sinon rattache le sujet à une personne invitée par
    e-mail, sinon la crée."""
    person = (await session.execute(select(Person).where(Person.oidc_subject == subject))).scalar_one_or_none()
    if person is None:
        person = (
            await session.execute(
                select(Person).where(Person.email == email, Person.oidc_subject.is_(None))
            )
        ).scalar_one_or_none()
        if person is not None:
            person.oidc_subject = subject
    if person is None:
        person = Person(oidc_subject=subject, email=email, display_name=name)
        session.add(person)
    else:
        person.email = email
        if name:
            person.display_name = name
    await session.flush()
    return person


@router.get("/login")
async def login(request: Request) -> object:
    client = _client()
    return await client.authorize_redirect(request, get_settings().oidc_redirect_uri)


@router.get("/callback")
async def callback(request: Request, session: SessionDep) -> RedirectResponse:
    client = _client()
    token = await client.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    subject, email = claims.get("sub"), claims.get("email")
    if not subject or not email:
        raise HTTPException(status_code=400, detail="Le fournisseur d'identité n'a pas renvoyé sub et email")
    person = await upsert_person(session, subject=subject, email=email, name=claims.get("name", ""))
    response = RedirectResponse(get_settings().frontend_url)
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(person.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response
