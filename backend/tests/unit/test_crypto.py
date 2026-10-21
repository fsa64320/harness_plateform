import base64

import pytest

from harness.catalog.crypto import Cipher, SecretValue, decrypt_secret, encrypt_secret

KEY = base64.b64encode(b"k" * 32).decode()


def test_round_trip_with_key_version() -> None:
    cipher = Cipher(KEY, key_version=2)
    enc = encrypt_secret("hunter2", cipher)
    assert enc.key_version == 2
    assert b"hunter2" not in enc.ciphertext
    assert decrypt_secret(enc, cipher).reveal() == "hunter2"


def test_nonce_is_unique_per_encryption() -> None:
    cipher = Cipher(KEY)
    a, b = encrypt_secret("same", cipher), encrypt_secret("same", cipher)
    assert a.nonce != b.nonce and a.ciphertext != b.ciphertext


def test_tampered_ciphertext_raises() -> None:
    cipher = Cipher(KEY)
    enc = encrypt_secret("hunter2", cipher)
    bad = type(enc)(
        ciphertext=bytes([enc.ciphertext[0] ^ 1]) + enc.ciphertext[1:],
        nonce=enc.nonce,
        key_version=enc.key_version,
    )
    with pytest.raises(ValueError):
        decrypt_secret(bad, cipher)


def test_wrong_key_raises() -> None:
    enc = encrypt_secret("hunter2", Cipher(KEY))
    other = Cipher(base64.b64encode(b"z" * 32).decode())
    with pytest.raises(ValueError):
        decrypt_secret(enc, other)


def test_plaintext_never_in_repr_or_str() -> None:
    s = SecretValue("hunter2")
    for rendered in (repr(s), str(s), f"{s}", f"{s!r}", repr({"v": s}), repr([s])):
        assert "hunter2" not in rendered
    assert s.reveal() == "hunter2"


def test_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        Cipher(base64.b64encode(b"short").decode())
