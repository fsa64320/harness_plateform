"""Serveurs MCP du catalogue : enregistrement, découverte des opérations, classification, chargement.

Seuls les transports distants en HTTPS sont acceptés : la plateforme ne lance aucun processus local
(pas de stdio) et ne parle qu'à des serveurs enregistrés (FR-011, research R-09)."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.catalog.credentials import bearer_headers, store_credential
from harness.db.models.catalog import Classification, Tool, ToolKind, ToolOperation, ToolStatus
from harness.groups.audit import record_admin_action
from harness.net_policy import OutboundBlocked, assert_outbound_allowed

DISCOVERY_TIMEOUT_SECONDS = 10.0
# Le nom du serveur préfixe le nom des outils vus par le modèle : il doit rester un identifiant sûr.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class McpError(Exception):
    """Configuration invalide ou serveur injoignable (réponse 422)."""


class ToolConflict(Exception):
    """Un outil de même nom existe déjà dans le groupe (réponse 409)."""


class ToolNotFound(Exception):
    pass


def validate_registration(name: str, url: str) -> None:
    if not NAME_PATTERN.match(name):
        raise McpError("Nom invalide : lettres, chiffres, « _ » et « - », 32 caractères au plus")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise McpError("L'URL d'un serveur MCP doit être en HTTPS (transport distant uniquement)")
    if parts.username or parts.password:
        raise McpError("L'URL ne doit pas contenir d'identifiants : utiliser le champ credential")


def connection(url: str, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
    conn: dict[str, Any] = {"url": url, "transport": "streamable_http"}
    if headers:
        conn["headers"] = dict(headers)
    return conn


def _leaf_message(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


async def discover_operations(
    name: str, url: str, headers: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Se connecte au serveur et liste ses outils ; toute défaillance devient une `McpError`."""
    client = MultiServerMCPClient({name: connection(url, headers)})  # type: ignore[dict-item]
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS), client.session(name) as session:
            listed = await session.list_tools()
    except (Exception, BaseExceptionGroup) as exc:
        raise McpError(f"Serveur MCP injoignable ({url}) : {_leaf_message(exc)}") from exc
    return [
        {"name": t.name, "description": t.description or "", "input_schema": dict(t.inputSchema or {})}
        for t in listed.tools
    ]


async def register_mcp_server(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    description: str,
    url: str,
    credential: str | None = None,
) -> Tool:
    """Vérifie la joignabilité, enregistre le serveur et ses opérations (non classées). L'identifiant
    éventuel est chiffré au repos et ne sera plus jamais renvoyé (FR-014)."""
    validate_registration(name, url)
    try:
        await assert_outbound_allowed(url)
    except OutboundBlocked as exc:
        raise McpError(str(exc)) from exc
    existing = await session.execute(
        select(Tool.id).where(Tool.kind == ToolKind.MCP_SERVER, Tool.name == name)
    )
    if existing.first() is not None:
        raise ToolConflict(f"Un outil « {name} » existe déjà dans ce groupe")
    operations = await discover_operations(name, url, bearer_headers(credential) if credential else None)
    credential_row = await store_credential(session, group_id, credential) if credential else None

    tool = Tool(
        group_id=group_id,
        kind=ToolKind.MCP_SERVER,
        name=name,
        description=description,
        url=url,
        created_by=actor_id,
        credential_id=credential_row.id if credential_row else None,
        last_check_at=dt.datetime.now(dt.UTC),
        last_check_ok=True,
    )
    session.add(tool)
    await session.flush()
    for op in operations:
        session.add(
            ToolOperation(
                group_id=group_id,
                tool_id=tool.id,
                operation_name=op["name"],
                input_schema=op["input_schema"],
                classification=Classification.UNCLASSIFIED,
            )
        )
    await session.flush()
    await record_admin_action(
        session,
        group_id=group_id,
        actor_id=actor_id,
        action="tool.register",
        target_type="tool",
        target_id=tool.id,
        details={
            "kind": "mcp_server",
            "name": name,
            "url": url,
            "has_credential": credential is not None,
            "operations": [o["name"] for o in operations],
        },
    )
    return tool


async def classify_operation(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    tool_id: uuid.UUID,
    operation_name: str,
    classification: str,
) -> ToolOperation:
    """Classe une opération (rôle `tool_manager` vérifié par la route) et journalise le changement."""
    if classification not in (Classification.READ_ONLY, Classification.EXTERNAL_EFFECT):
        raise McpError("Classification invalide : read_only ou external_effect")
    operation = (
        await session.execute(
            select(ToolOperation)
            .join(Tool, Tool.id == ToolOperation.tool_id)
            .where(
                ToolOperation.tool_id == tool_id,
                ToolOperation.operation_name == operation_name,
                Tool.status == ToolStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if operation is None:
        raise ToolNotFound(operation_name)
    previous = operation.classification
    operation.classification = classification
    await session.flush()
    await record_admin_action(
        session,
        group_id=group_id,
        actor_id=actor_id,
        action="tool.classify",
        target_type="tool_operation",
        target_id=operation.id,
        details={"tool_id": str(tool_id), "operation": operation_name, "from": previous, "to": classification},
    )
    return operation


async def load_runtime_tools(
    snapshot: Mapping[str, Any],
    headers_by_tool: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[list[BaseTool], list[str]]:
    """Charge les outils MCP des serveurs utilisés par l'agent. Un serveur injoignable au lancement
    n'empêche pas l'exécution : il est signalé et ses outils manquent."""
    from harness.runtime.builder import resolve_allowed_operations

    needed = {op.tool_name: op.tool_id for op in resolve_allowed_operations(dict(snapshot))}
    urls = {t["name"]: t["url"] for t in snapshot.get("tools", []) if t.get("url")}
    headers_by_tool = headers_by_tool or {}
    tools: list[BaseTool] = []
    warnings: list[str] = []
    for name, tool_id in needed.items():
        try:
            await assert_outbound_allowed(urls[name])
        except OutboundBlocked as exc:
            warnings.append(f"Serveur MCP « {name} » ignoré : {exc}")
            continue
        client = MultiServerMCPClient(
            {name: connection(urls[name], headers_by_tool.get(tool_id))},  # type: ignore[dict-item]
            tool_name_prefix=True,
        )
        try:
            async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS):
                tools.extend(await client.get_tools())
        except (Exception, BaseExceptionGroup) as exc:
            warnings.append(f"Serveur MCP « {name} » injoignable au lancement : {_leaf_message(exc)}")
    return tools, warnings
