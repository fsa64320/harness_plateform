"""Service HTTPS de test pour les hooks webhook : répond après un délai, ou jamais (délai dépassé)."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.fixtures.mcp_test_server import _free_port, _self_signed_cert


class WebhookTestServer:
    def __init__(
        self, *, delay: float = 0.0, decision: str = "allow", host: str = "127.0.0.1", extra_dns: tuple[str, ...] = ()
    ) -> None:
        self.host = host
        self.delay, self.decision = delay, decision
        self.requests: list[dict[str, Any]] = []
        self._dir = tempfile.TemporaryDirectory()
        self.cert_path, self._key_path = _self_signed_cert(
            Path(self._dir.name), common_name="harness-webhook-test", extra_dns=extra_dns
        )
        self.port = _free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"https://localhost:{self.port}/decide"

    def _app(self) -> Starlette:
        async def decide(request: Request) -> JSONResponse:
            self.requests.append({"headers": dict(request.headers), "body": (await request.body()).decode()})
            await asyncio.sleep(self.delay)
            return JSONResponse({"decision": self.decision, "reason": "service de test"})

        return Starlette(routes=[Route("/decide", decide, methods=["POST"])])

    def start(self) -> WebhookTestServer:
        config = uvicorn.Config(
            self._app(), host=self.host, port=self.port, ssl_certfile=str(self.cert_path),
            ssl_keyfile=str(self._key_path), log_level="critical",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Le service de webhook de test n'a pas démarré")
            time.sleep(0.02)
        return self

    def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._dir.cleanup()
