"""Politique réseau des appels sortants (revue de sécurité) : pas de SSRF vers le réseau interne.

La plateforme appelle des URL que des gestionnaires d'outils enregistrent (serveurs MCP, webhooks de
hooks). Sans garde, l'une d'elles pourrait viser un service interne ou les métadonnées d'un cloud
(169.254.169.254). Par défaut, tout hôte qui ne résout pas vers une adresse publique est refusé ; le
réglage `HARNESS_ALLOW_PRIVATE_NETWORKS` l'autorise pour le développement et les réseaux privés assumés.

Limite connue : la résolution est contrôlée avant chaque usage (enregistrement, lancement d'une
exécution, appel d'un webhook) mais pas verrouillée jusqu'à la connexion (rebinding DNS) ; pour une
défense complète, appliquer aussi un filtrage de sortie au niveau du réseau (pare-feu, proxy)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from harness.config import get_settings


class OutboundBlocked(Exception):
    """Appel sortant refusé par la politique réseau."""


def _addresses_blocked(addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> str | None:
    for ip in addresses:
        candidate = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if not candidate.is_global:
            return f"l'adresse {candidate} n'est pas une adresse publique d'Internet"
    return None


async def blocked_reason(url: str) -> str | None:
    """Motif de refus, ou `None` si l'appel est permis."""
    if get_settings().harness_allow_private_networks:
        return None
    host = urlsplit(url).hostname
    if not host:
        return "URL sans nom d'hôte"
    try:
        return _addresses_blocked([ipaddress.ip_address(host)])
    except ValueError:
        pass  # un nom : on le résout
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return f"l'hôte « {host} » n'est pas résolvable"
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    return _addresses_blocked(addresses) if addresses else f"l'hôte « {host} » n'a aucune adresse"


async def assert_outbound_allowed(url: str) -> None:
    reason = await blocked_reason(url)
    if reason is not None:
        raise OutboundBlocked(f"Appel sortant refusé par la politique réseau : {reason}")
