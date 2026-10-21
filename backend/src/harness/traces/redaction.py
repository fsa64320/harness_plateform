"""Masquage des secrets et données personnelles avant écriture dans une trace ou un log (FR-009)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

MASK = "«masqué»"

_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    # Numéros de téléphone : international (+33 6 12 34 56 78) ou national à 10 chiffres.
    re.compile(r"\+\d{1,3}(?:[ .-]?\d){8,12}"),
    re.compile(r"\b0\d(?:[ .-]?\d{2}){4}\b"),
]
_SENSITIVE_KEYS = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)"
)


def redact(value: Any, known_secrets: Iterable[str]) -> Any:
    """Retourne une copie de `value` où secrets connus, motifs de jetons et de clés, e-mails et
    téléphones sont remplacés par `MASK`. Les valeurs non textuelles sont inchangées."""
    secrets = sorted({s for s in known_secrets if s}, key=len, reverse=True)
    return _walk(value, secrets)


def _walk(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, dict):
        return {
            k: MASK
            if isinstance(k, str) and isinstance(v, str) and v and _SENSITIVE_KEYS.search(k)
            else _walk(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_walk(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, secrets) for v in value)
    return value


def _redact_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def structlog_redactor(known_secrets: Iterable[str] = ()) -> Any:
    """Processeur structlog : masque chaque champ de l'événement de log."""
    secrets = list(known_secrets)

    def processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        return {k: _walk(v, sorted(set(secrets), key=len, reverse=True)) for k, v in event_dict.items()}

    return processor
