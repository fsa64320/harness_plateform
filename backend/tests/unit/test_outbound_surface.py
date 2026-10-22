"""Garde-fous de la surface d'attaque (revue de sécurité, T128) : aucun processus lancé, aucun code
exécuté, aucun transport MCP stdio, et des appels sortants limités aux modules prévus."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "harness"
FILES = {p: p.read_text() for p in SRC.rglob("*.py")}

# Seuls ces modules ouvrent des connexions sortantes : webhooks de hooks, serveurs MCP enregistrés,
# fournisseur de modèle, SSO, base de données.
NETWORK_MODULES = {
    "catalog/hook_webhook.py",  # webhooks enregistrés
    "catalog/hooks.py",  # client HTTP des webhooks
    "catalog/mcp.py",  # serveurs MCP enregistrés (HTTPS, distants)
    "net_policy.py",  # résolution DNS de contrôle
    "auth/oidc.py",  # fournisseur d'identité de l'organisation
    "runtime/models.py",  # Ollama
}


def test_no_process_is_spawned_and_no_code_is_executed() -> None:
    banned = re.compile(r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|pty\.spawn)\b|(?<![\w.])(eval|exec|compile)\s*\(")
    offenders = [str(p.relative_to(SRC)) for p, text in FILES.items() if banned.search(text)]
    assert offenders == []


def test_no_stdio_mcp_transport() -> None:
    offenders = [str(p.relative_to(SRC)) for p, text in FILES.items() if re.search(r"[\"']stdio[\"']|Stdio\w*|stdio_client", text)]
    assert offenders == [], "le transport stdio lancerait un processus local sur la plateforme"
    mcp = FILES[SRC / "catalog" / "mcp.py"]
    assert '"transport": "streamable_http"' in mcp


def test_outbound_network_code_is_confined_to_the_expected_modules() -> None:
    pattern = re.compile(r"^\s*(import|from)\s+(httpx|requests|urllib\.request|aiohttp|http\.client|socket|websockets)\b", re.M)
    users = {str(p.relative_to(SRC)) for p, text in FILES.items() if pattern.search(text)}
    assert users <= NETWORK_MODULES, f"modules réseau inattendus : {sorted(users - NETWORK_MODULES)}"


def test_agent_and_member_code_never_reach_the_filesystem_directly() -> None:
    # les agents lisent/écrivent des fichiers virtuels (état de l'exécution et tables SQL), jamais le disque
    pattern = re.compile(r"(?<![\w.])open\s*\(|\b(FilesystemBackend|LocalShellBackend)\b|Path\([^)]*\)\.(read|write)_")
    offenders = [str(p.relative_to(SRC)) for p, text in FILES.items() if pattern.search(text)]
    assert offenders == []
