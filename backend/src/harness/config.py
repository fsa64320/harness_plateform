"""Configuration par variables d'environnement. Aucun secret n'est lu depuis le code ni commité."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Aucune donnée ne doit sortir vers LangSmith (constitution : aucune donnée sortante par défaut).
# `deepagents` installe `langsmith` ; on désactive son traçage dès l'import de la configuration.
for _var in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ[_var] = "false"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Rôle applicatif sans BYPASSRLS ; le propriétaire (migrations) est `migration_database_url`.
    database_url: str
    migration_database_url: str | None = None

    # AES-256-GCM : 32 octets en base64.
    harness_encryption_key: str
    harness_encryption_key_version: int = 1

    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = True

    ollama_base_url: str = "http://localhost:11434"

    global_max_concurrent_runs: int = 50
    approval_check_interval_seconds: int = 30

    @field_validator("harness_encryption_key")
    @classmethod
    def _key_is_32_bytes(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("HARNESS_ENCRYPTION_KEY doit être du base64 valide") from exc
        if len(raw) != 32:
            raise ValueError("HARNESS_ENCRYPTION_KEY doit encoder exactement 32 octets")
        return value

    @property
    def session_secret(self) -> bytes:
        """Clé de signature des cookies de session, dérivée de la clé de chiffrement."""
        return hmac.new(
            base64.b64decode(self.harness_encryption_key), b"harness-session-v1", hashlib.sha256
        ).digest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
