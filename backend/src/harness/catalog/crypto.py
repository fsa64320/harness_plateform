"""Chiffrement AES-GCM des identifiants d'outils (FR-014). La valeur en clair ne vit que dans un
`SecretValue` dont `repr` et `str` sont masqués, et n'est révélée qu'au moment de l'injection."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class SecretValue:
    """Valeur secrète opaque : jamais affichée, jamais sérialisée."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('**********')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce__(self) -> tuple[type, tuple[str]]:
        raise TypeError("SecretValue ne se sérialise pas")


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class Cipher:
    def __init__(self, key_b64: str, key_version: int = 1) -> None:
        raw = base64.b64decode(key_b64)
        if len(raw) != 32:
            raise ValueError("La clé de chiffrement doit faire 32 octets")
        self._aes = AESGCM(raw)
        self.key_version = key_version


def encrypt_secret(plaintext: str, cipher: Cipher) -> EncryptedSecret:
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = cipher._aes.encrypt(nonce, plaintext.encode(), None)
    return EncryptedSecret(ciphertext=ciphertext, nonce=nonce, key_version=cipher.key_version)


def decrypt_secret(secret: EncryptedSecret, cipher: Cipher) -> SecretValue:
    try:
        return SecretValue(cipher._aes.decrypt(secret.nonce, secret.ciphertext, None).decode())
    except InvalidTag as exc:
        raise ValueError("Identifiant illisible : clé erronée ou données altérées") from exc
