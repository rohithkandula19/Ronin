"""Tests for ronin's data-safety vault: encryption at rest + the privacy audit.

Covers the contract that makes the feature trustworthy:
  * encrypt/decrypt round-trips are EXACT,
  * a WRONG key is rejected (raises, never returns garbage),
  * a TAMPERED ciphertext is rejected,
  * the privacy `audit` flags a planted plaintext API key — and never leaks its
    value.

The stdlib fallback path (no `cryptography`) is exercised directly so the
authenticated-fallback guarantee is tested even on a machine that has Fernet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ronin_cli.vault as v


# --------------------------------------------------------------------------- #
# derive_key
# --------------------------------------------------------------------------- #


def test_derive_key_is_deterministic_and_32_byte_b64() -> None:
    k1 = v.derive_key("hunter2", b"salt-1234")
    k2 = v.derive_key("hunter2", b"salt-1234")
    assert k1 == k2                       # deterministic
    import base64
    raw = base64.urlsafe_b64decode(k1)
    assert len(raw) == 32                 # 256-bit, Fernet-shaped


def test_derive_key_varies_with_passphrase_and_salt() -> None:
    base = v.derive_key("pw", b"salt1234")
    assert v.derive_key("pw2", b"salt1234") != base   # passphrase matters
    assert v.derive_key("pw", b"salt5678") != base    # salt matters


def test_derive_key_rejects_empty_and_short_salt() -> None:
    with pytest.raises(ValueError):
        v.derive_key("", b"salt1234")
    with pytest.raises(ValueError):
        v.derive_key("pw", b"short")      # < 8 bytes


# --------------------------------------------------------------------------- #
# encrypt / decrypt — round-trip, wrong key, tamper
# --------------------------------------------------------------------------- #


def test_encrypt_decrypt_roundtrip_exact() -> None:
    key = v.derive_key("pw", b"salt1234")
    for msg in (b"", b"hi", b"a secret token sk-XXXX", b"\x00\x01\x02\xff" * 50,
                "résumé — unicode bytes".encode("utf-8")):
        token = v.encrypt(msg, key)
        assert token != msg               # actually transformed
        assert v.decrypt(token, key) == msg   # exact round-trip


def test_decrypt_with_wrong_key_raises() -> None:
    key = v.derive_key("right", b"salt1234")
    wrong = v.derive_key("wrong", b"salt1234")
    token = v.encrypt(b"top secret", key)
    with pytest.raises(v.InvalidToken):
        v.decrypt(token, wrong)


def test_decrypt_tampered_ciphertext_raises() -> None:
    key = v.derive_key("pw", b"salt1234")
    token = bytearray(v.encrypt(b"important data here", key))
    token[-1] ^= 0x01                     # flip one bit in the ciphertext tail
    with pytest.raises(v.InvalidToken):
        v.decrypt(bytes(token), key)


def test_encrypt_requires_bytes() -> None:
    key = v.derive_key("pw", b"salt1234")
    with pytest.raises(TypeError):
        v.encrypt("a str not bytes", key)   # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# stdlib fallback path — tested directly so the guarantee holds without Fernet
# --------------------------------------------------------------------------- #


def test_stdlib_fallback_roundtrip_and_tamper() -> None:
    key = v.derive_key("pw", b"salt1234")
    c = v._StdlibCrypto(key)
    token = c.encrypt(b"fallback secret payload")
    assert token.startswith(v._FALLBACK_MAGIC)
    assert c.decrypt(token) == b"fallback secret payload"

    # wrong key
    wrong = v._StdlibCrypto(v.derive_key("nope", b"salt1234"))
    with pytest.raises(v.InvalidToken):
        wrong.decrypt(token)

    # tamper
    bad = bytearray(token)
    bad[-1] ^= 0xFF
    with pytest.raises(v.InvalidToken):
        c.decrypt(bytes(bad))


def test_fallback_token_decrypts_via_public_decrypt() -> None:
    """A token in our self-describing container decrypts through the public
    ``decrypt`` regardless of whether Fernet is installed."""
    key = v.derive_key("pw", b"salt1234")
    token = v._StdlibCrypto(key).encrypt(b"hello fallback")
    assert v.decrypt(token, key) == b"hello fallback"


# --------------------------------------------------------------------------- #
# lock_file / unlock_file
# --------------------------------------------------------------------------- #


def test_lock_unlock_file_roundtrip(tmp_path: Path) -> None:
    key = v.derive_key("pw", b"salt1234")
    f = tmp_path / "memory.json"
    payload = json.dumps({"memories": [{"text": "User's name is Rohith"}]}).encode()
    f.write_bytes(payload)

    v.lock_file(f, key)
    on_disk = f.read_bytes()
    assert on_disk != payload                 # encrypted at rest
    assert v.is_encrypted_blob(on_disk)
    assert b"Rohith" not in on_disk           # plaintext is gone

    v.unlock_file(f, key)
    assert f.read_bytes() == payload          # exact restore


def test_lock_file_is_idempotent(tmp_path: Path) -> None:
    key = v.derive_key("pw", b"salt1234")
    f = tmp_path / "config.toml"
    f.write_text("anthropic_api_key = 'x'")
    v.lock_file(f, key)
    first = f.read_bytes()
    v.lock_file(f, key)                        # second lock is a no-op
    assert f.read_bytes() == first
    v.unlock_file(f, key)
    assert f.read_text() == "anthropic_api_key = 'x'"


def test_unlock_with_wrong_key_leaves_file_intact(tmp_path: Path) -> None:
    key = v.derive_key("right", b"salt1234")
    wrong = v.derive_key("wrong", b"salt1234")
    f = tmp_path / "secret.bin"
    f.write_bytes(b"sensitive")
    v.lock_file(f, key)
    locked = f.read_bytes()
    with pytest.raises(v.InvalidToken):
        v.unlock_file(f, wrong)
    assert f.read_bytes() == locked           # untouched on failure


# --------------------------------------------------------------------------- #
# is_encrypted_blob
# --------------------------------------------------------------------------- #


def test_is_encrypted_blob_distinguishes_plaintext() -> None:
    assert not v.is_encrypted_blob(b"")
    assert not v.is_encrypted_blob(b"anthropic_api_key = 'sk-abc'")
    assert not v.is_encrypted_blob(b'{"memories": []}')
    key = v.derive_key("pw", b"salt1234")
    assert v.is_encrypted_blob(v.encrypt(b"x", key))


# --------------------------------------------------------------------------- #
# privacy audit — the safety centerpiece
# --------------------------------------------------------------------------- #


def _make_fake_home(root: Path) -> Path:
    """Build a realistic ronin home with a PLANTED fake plaintext key."""
    ronin = root / ".ronin"
    (ronin / "sessions").mkdir(parents=True)
    (ronin / "runs").mkdir(parents=True)
    # A planted fake API key sitting unencrypted in config.toml.
    (ronin / "config.toml").write_text(
        "provider = 'anthropic'\n"
        "anthropic_api_key = 'sk-FAKEFAKEFAKEFAKEFAKEFAKE1234567890abcd'\n"
    )
    (ronin / "memory.json").write_text(json.dumps({"memories": [{"text": "uses Groq"}]}))
    (ronin / "sessions" / "code-2026.json").write_text(
        json.dumps({"transcript": ["USER: hi", "ASSISTANT: hello"]})
    )
    (ronin / "runs" / "run-1.json").write_text(json.dumps({"id": "run-1"}))
    return ronin


def test_audit_flags_planted_plaintext_key(tmp_path: Path) -> None:
    ronin = _make_fake_home(tmp_path)
    findings = v.audit([ronin])

    by_name = {Path(f["path"]).name: f for f in findings}
    cfg = by_name["config.toml"]
    assert cfg["kind"] == "secret"
    assert cfg["encrypted"] is False
    assert cfg["contains_plaintext_secret"] is True
    assert "sk-" in cfg["secret_prefixes"]

    # The audit must NEVER carry the secret value anywhere in its output.
    blob = json.dumps(findings)
    assert "sk-FAKEFAKEFAKEFAKEFAKEFAKE1234567890abcd" not in blob
    assert "FAKEFAKE" not in blob


def test_audit_classifies_kinds(tmp_path: Path) -> None:
    ronin = _make_fake_home(tmp_path)
    kinds = {Path(f["path"]).name: f["kind"] for f in v.audit([ronin])}
    assert kinds["config.toml"] == "secret"
    assert kinds["memory.json"] == "memory"
    assert kinds["code-2026.json"] == "session"
    assert kinds["run-1.json"] == "memory"   # runs grouped under memory


def test_audit_marks_encrypted_after_lock(tmp_path: Path) -> None:
    ronin = _make_fake_home(tmp_path)
    key = v.derive_key("pw", b"salt1234")
    v.lock_file(ronin / "config.toml", key)

    cfg = {Path(f["path"]).name: f for f in v.audit([ronin])}["config.toml"]
    assert cfg["encrypted"] is True
    # Once encrypted, the plaintext-key detector must NOT fire.
    assert cfg["contains_plaintext_secret"] is False


def test_audit_skips_missing_roots_and_dedupes(tmp_path: Path) -> None:
    ronin = _make_fake_home(tmp_path)
    missing = tmp_path / "does-not-exist"
    # Pass the same root twice + a missing one: no crash, no double-count.
    findings = v.audit([ronin, ronin, missing])
    paths = [f["path"] for f in findings]
    assert len(paths) == len(set(paths))     # de-duplicated


def test_summarize_verdict_at_risk_then_safe(tmp_path: Path) -> None:
    ronin = _make_fake_home(tmp_path)
    s1 = v.summarize(v.audit([ronin]))
    assert s1["verdict"] == "at_risk"
    assert s1["exposed_secrets"] == 1
    assert s1["total"] >= 4

    # After encrypting the offending file, the verdict flips to safe.
    v.lock_file(ronin / "config.toml", v.derive_key("pw", b"salt1234"))
    s2 = v.summarize(v.audit([ronin]))
    assert s2["verdict"] == "safe"
    assert s2["exposed_secrets"] == 0


def test_render_privacy_never_prints_secret_value(tmp_path: Path) -> None:
    from rich.console import Console

    ronin = _make_fake_home(tmp_path)
    findings = v.audit([ronin])

    buf = Console(record=True, width=120)
    v.render_privacy(buf, findings)
    out = buf.export_text()

    assert "ronin privacy" in out
    assert "AT RISK" in out                  # verdict surfaced
    assert "sk-FAKEFAKEFAKEFAKEFAKEFAKE1234567890abcd" not in out
    assert "FAKEFAKE" not in out             # value never rendered


def test_render_privacy_handles_empty(tmp_path: Path) -> None:
    from rich.console import Console

    buf = Console(record=True, width=120)
    v.render_privacy(buf, [])
    assert "No ronin data found" in buf.export_text()
