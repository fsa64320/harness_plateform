"""Les secrets ne se retrouvent jamais dans les logs, tracebacks compris (constitution IV, FR-014)."""

from __future__ import annotations

import json

import pytest

from harness.main import configure_logging
from harness.traces.redaction import register_known_secrets

SECRET = "tok-9f8e7d6c5b4a3210-secret"  # noqa: S105


def test_exception_tracebacks_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    import structlog

    register_known_secrets([SECRET])
    configure_logging()
    log = structlog.get_logger()
    try:
        raise RuntimeError(f"échec avec l'identifiant {SECRET}")
    except RuntimeError as exc:
        log.error("boom", exc_info=exc, detail=f"valeur {SECRET}")
    out = capsys.readouterr().out
    assert SECRET not in out
    line = json.loads(out.strip().splitlines()[-1])
    assert line["event"] == "boom" and "«masqué»" in line["exception"] and "«masqué»" in line["detail"]


def test_patterns_are_redacted_without_registration(capsys: pytest.CaptureFixture[str]) -> None:
    import structlog

    configure_logging()
    structlog.get_logger().info("req", authorization="Bearer abcdefghijklmnopqrstuvwxyz0123", contact="alice@example.com")
    out = capsys.readouterr().out
    assert "abcdefghijklmnopqrstuvwxyz0123" not in out and "alice@example.com" not in out
