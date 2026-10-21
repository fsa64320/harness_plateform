"""Serveur MCP de test en processus, en HTTPS (certificat auto-signé) : `lookup`, `send_message`, `wait`.

La plateforme n'accepte que des serveurs MCP distants en HTTPS (FR-011, constitution II) ; le test
fait donc confiance au certificat de test via `SSL_CERT_FILE`, sans rien changer au code de production.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


def _self_signed_cert(directory: Path, common_name: str = "localhost") -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])  # DN distincts : OpenSSL confond deux CA de même nom
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class McpTestServer:
    """Serveur MCP HTTPS démarrable et arrêtable (pour simuler une panne en cours d'exécution)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.received_headers: list[dict[str, str]] = []
        self._dir = tempfile.TemporaryDirectory()
        self.cert_path, self._key_path = _self_signed_cert(Path(self._dir.name))
        self.port = _free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"https://localhost:{self.port}/mcp"

    def _build_app(self) -> Any:
        mcp = FastMCP(
            "test-server",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        server = self

        @mcp.tool()
        def lookup(query: str) -> str:
            """Cherche une information (lecture seule)."""
            server.calls.append(("lookup", {"query": query}))
            return f"résultat pour {query}"

        @mcp.tool()
        def send_message(text: str) -> str:
            """Envoie un message (effet externe)."""
            server.calls.append(("send_message", {"text": text}))
            return "message envoyé"

        @mcp.tool()
        def whoami() -> str:
            """Renvoie l'en-tête d'authentification reçu (simule un outil qui divulgue un secret)."""
            server.calls.append(("whoami", {}))
            return server.received_headers[-1].get("authorization", "anonyme") if server.received_headers else "anonyme"

        @mcp.tool()
        async def wait(seconds: float) -> str:
            """Attend puis répond (outil lent)."""
            server.calls.append(("wait", {"seconds": seconds}))
            await asyncio.sleep(seconds)
            return "attente terminée"

        app = mcp.streamable_http_app()

        async def record_headers(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                server.received_headers.append(
                    {k.decode().lower(): v.decode() for k, v in scope["headers"]}
                )
            await app(scope, receive, send)

        return record_headers

    def start(self) -> McpTestServer:
        config = uvicorn.Config(
            self._build_app(),
            host="127.0.0.1",
            port=self.port,
            ssl_certfile=str(self.cert_path),
            ssl_keyfile=str(self._key_path),
            log_level="warning",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Le serveur MCP de test n'a pas démarré")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = self._thread = None

    def close(self) -> None:
        self.stop()
        self._dir.cleanup()
