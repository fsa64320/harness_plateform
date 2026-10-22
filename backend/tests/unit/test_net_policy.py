"""Politique réseau sortant : pas de SSRF vers le réseau interne (revue de sécurité, T128).

Un gestionnaire d'outils enregistre des URL (serveurs MCP, webhooks) que la plateforme appelle depuis son
réseau : sans garde, il pourrait sonder des services internes ou les métadonnées du cloud."""

from __future__ import annotations

import pytest

from harness.net_policy import OutboundBlocked, assert_outbound_allowed, blocked_reason


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness import config

    monkeypatch.setenv("HARNESS_ALLOW_PRIVATE_NETWORKS", "false")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "https://[::1]/mcp",
        "https://169.254.169.254/latest/meta-data",  # métadonnées des clouds
        "https://10.0.0.5/x",
        "https://172.16.3.4/x",
        "https://192.168.1.10/x",
        "https://100.64.0.1/x",  # CGNAT
        "https://0.0.0.0/x",  # noqa: S104
        "https://[fd00::1]/x",
        "https://[fe80::1]/x",
    ],
)
async def test_internal_addresses_are_blocked_by_default(strict: None, url: str) -> None:
    assert await blocked_reason(url) is not None
    with pytest.raises(OutboundBlocked):
        await assert_outbound_allowed(url)


async def test_a_public_address_is_allowed(strict: None) -> None:
    assert await blocked_reason("https://93.184.216.34/mcp") is None  # adresse publique (example.com)
    await assert_outbound_allowed("https://8.8.8.8/x")


async def test_an_unresolvable_host_is_blocked(strict: None) -> None:
    assert await blocked_reason("https://ce-nom-n-existe-pas.invalid/x") is not None


async def test_private_networks_can_be_allowed_explicitly_for_development(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness import config

    monkeypatch.setenv("HARNESS_ALLOW_PRIVATE_NETWORKS", "true")
    config.get_settings.cache_clear()
    try:
        assert await blocked_reason("https://127.0.0.1/mcp") is None
    finally:
        config.get_settings.cache_clear()
