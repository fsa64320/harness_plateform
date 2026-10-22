"""Identifiants d'outils : stockés chiffrés, jamais sérialisés, déchiffrés au moment de l'exécution
seulement (FR-014). Leurs valeurs sont aussi enregistrées comme « secrets connus » pour être masquées
dans les traces et les logs."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.catalog.crypto import Cipher, EncryptedSecret, SecretValue, decrypt_secret, encrypt_secret
from harness.config import get_settings
from harness.db.models.catalog import Credential, HookDefinition, Tool
from harness.traces.redaction import register_known_secrets


def get_cipher() -> Cipher:
    settings = get_settings()
    return Cipher(settings.harness_encryption_key, settings.harness_encryption_key_version)


async def store_credential(session: AsyncSession, group_id: uuid.UUID, plaintext: str) -> Credential:
    encrypted = encrypt_secret(plaintext, get_cipher())
    credential = Credential(
        group_id=group_id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce, key_version=encrypted.key_version
    )
    session.add(credential)
    await session.flush()
    register_known_secrets([plaintext])
    return credential


def reveal(credential: Credential) -> SecretValue:
    """Déchiffre pour injection immédiate ; ne jamais journaliser ni sérialiser le résultat."""
    return decrypt_secret(
        EncryptedSecret(bytes(credential.ciphertext), bytes(credential.nonce), credential.key_version), get_cipher()
    )


async def group_secrets(session: AsyncSession, group_id: uuid.UUID) -> list[str]:
    """Toutes les valeurs secrètes du groupe (identifiants MCP et secrets de signature)."""
    values = [reveal(c).reveal() for c in (await session.execute(select(Credential))).scalars()]
    register_known_secrets(values)
    return values


def bearer_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def tool_headers(session: AsyncSession, snapshot: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """En-têtes d'authentification des serveurs MCP de la configuration figée, par id d'outil."""
    ids = {uuid.UUID(t["credential_id"]): t["id"] for t in snapshot.get("tools", []) if t.get("credential_id")}
    if not ids:
        return {}
    headers: dict[str, dict[str, str]] = {}
    for credential in (await session.execute(select(Credential).where(Credential.id.in_(ids)))).scalars():
        headers[ids[credential.id]] = bearer_headers(reveal(credential).reveal())
    return headers


async def hook_secrets(session: AsyncSession, hook_tool_ids: Iterable[str]) -> dict[str, str]:
    """Secrets de signature des webhooks, par id de hook (outil)."""
    wanted = [uuid.UUID(i) for i in hook_tool_ids]
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Tool.id, Credential)
            .join(HookDefinition, HookDefinition.tool_id == Tool.id)
            .join(Credential, Credential.id == HookDefinition.signing_secret_id)
            .where(Tool.id.in_(wanted))
        )
    ).all()
    return {str(tool_id): reveal(credential).reveal() for tool_id, credential in rows}
