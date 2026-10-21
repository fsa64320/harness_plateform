"""Application FastAPI : routeur `/api/v1`, logs JSON masqués, erreurs `{code, message}`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from harness import config
from harness.api import me
from harness.auth import oidc
from harness.db import session as db_session
from harness.traces.redaction import structlog_redactor

log = structlog.get_logger()

_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid",
    429: "quota_exceeded",
    503: "unavailable",
}


def configure_logging() -> None:
    """Logs JSON structurés ; chaque champ passe par le masquage des secrets (constitution IV)."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog_redactor(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


async def assert_rls_enforced() -> None:
    """Refuse de démarrer si le rôle applicatif peut contourner la RLS (échec sûr)."""
    async with db_session.get_sessionmaker()() as s:
        row = (
            await s.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if row.rolsuper or row.rolbypassrls:
        raise RuntimeError(
            "Le rôle de base de données de l'application contourne la RLS : utiliser un rôle "
            "sans SUPERUSER ni BYPASSRLS (voir MIGRATION_DATABASE_URL)"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = config.get_settings()
    db_session.init_engine(settings.database_url)
    await assert_rls_enforced()
    try:
        yield
    finally:
        await db_session.dispose_engine()


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Harness Plateform API",
        version="0.1.0",
        lifespan=lifespan if with_lifespan else None,
    )
    # Ne sert qu'à porter l'état OIDC (state/nonce) pendant la redirection vers le SSO.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.get_settings().session_secret.hex(),
        session_cookie="harness_oidc_state",
        max_age=600,
        same_site="lax",
        https_only=config.get_settings().cookie_secure,
    )

    api = APIRouter(prefix="/api/v1")
    api.include_router(me.router)
    api.include_router(oidc.router)
    app.include_router(api)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and {"code", "message"} <= exc.detail.keys():
            return _error(exc.status_code, str(exc.detail["code"]), str(exc.detail["message"]))
        return _error(exc.status_code, _CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return _error(422, "invalid", f"{where}: {first.get('msg', 'requête invalide')}")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=type(exc).__name__, exc_info=exc)
        return _error(500, "internal_error", "Erreur interne")

    return app
