import os

import pytest
from pydantic import ValidationError


def test_settings_fail_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    from harness.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_reject_short_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_ENCRYPTION_KEY", "c2hvcnQ=")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    from harness.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_langsmith_tracing_is_forced_off() -> None:
    """Import dans un processus neuf : recharger le module dans le processus de test laisserait d'autres
    modules attachés à une ancienne fonction `get_settings`."""
    import subprocess
    import sys

    env = {
        **os.environ,
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "HARNESS_ENCRYPTION_KEY": os.environ["HARNESS_ENCRYPTION_KEY"],
    }
    code = "import os, harness.config; print(os.environ['LANGSMITH_TRACING'], os.environ['LANGCHAIN_TRACING_V2'])"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)  # noqa: S603
    assert out.stdout.split() == ["false", "false"]
