from harness.traces.redaction import MASK, redact

SECRET = "s3cr3t-value-of-group"  # noqa: S105


def test_known_secrets_are_masked_everywhere() -> None:
    value = {
        "a": f"token={SECRET}",
        "nested": {"list": [SECRET, {"deep": f"x{SECRET}y"}]},
        "n": 3,
    }
    out = redact(value, known_secrets=[SECRET])
    assert SECRET not in repr(out)
    assert out["n"] == 3
    assert out["nested"]["list"][0] == MASK


def test_token_and_key_patterns_are_masked() -> None:
    samples = [
        "Authorization: Bearer abcdefghijklmnop1234567890",
        "key sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghijkl",
    ]
    for s in samples:
        out = redact(s, known_secrets=[])
        assert MASK in out, s
        assert "abcdefghijkl" not in out, s


def test_emails_and_phone_numbers_are_masked() -> None:
    out = redact("Contact alice@example.com ou +33 6 12 34 56 78 ou 06 12 34 56 78", known_secrets=[])
    assert "alice@example.com" not in out
    assert "12 34 56 78" not in out
    assert out.count(MASK) == 3


def test_non_sensitive_text_is_unchanged() -> None:
    value = {"task": "Résume le document", "count": 42, "items": ["a", "b"], "ok": True, "none": None}
    assert redact(value, known_secrets=[SECRET]) == value


def test_dict_keys_with_secret_names_are_masked() -> None:
    out = redact({"password": "hunter2", "api_key": "abc", "title": "x"}, known_secrets=[])
    assert out["password"] == MASK and out["api_key"] == MASK and out["title"] == "x"


def test_empty_known_secret_does_not_mask_everything() -> None:
    assert redact("hello", known_secrets=[""]) == "hello"
