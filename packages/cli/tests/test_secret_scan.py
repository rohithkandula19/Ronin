"""Tests for ``ronin scan`` secret scanning (ronin_cli.secret_scan).

CRITICAL SAFETY PROPERTY under test: a planted credential is *detected* (right
file:line + kind) but its full value NEVER appears anywhere in the findings or
the rendered output — only a masked hint or the bare kind.

No literal credential strings live in this source: every planted key is built at
runtime from harmless pieces (e.g. ``"sk-" + "x" * 40``) so the test file itself
contains no scannable secret.
"""
from __future__ import annotations

import io

from rich.console import Console

from ronin_cli import secret_scan
from ronin_cli.secret_scan import find_secrets, render_findings, scan_repo


# --- helpers ---------------------------------------------------------------- #
def _openai_key() -> str:
    # sk- + 40 chars → matches the openai-key pattern (sk-[A-Za-z0-9_-]{32,}).
    return "sk-" + "x" * 40


def _stripe_live_key() -> str:
    return "sk_live_" + "A" * 30


def _aws_akid() -> str:
    return "AKIA" + "Q" * 16  # AKIA + 16 upper-alnum


def _ghp() -> str:
    return "ghp_" + "b" * 36


# --- detection -------------------------------------------------------------- #
def test_detects_planted_key_with_correct_line() -> None:
    key = _openai_key()
    text = "first line\napi_key = '" + key + "'\nthird line\n"
    findings = find_secrets(text, "config.py")

    assert len(findings) >= 1
    f = findings[0]
    assert f["path"] == "config.py"
    assert f["line"] == 2  # the key is on the 2nd line
    assert f["kind"] == "openai-key"


def test_full_secret_never_in_findings() -> None:
    """The whole point: the raw value must not survive into any finding field."""
    key = _openai_key()
    findings = find_secrets("API=" + key, "c.py")

    assert findings  # it was detected
    for f in findings:
        # Not in the dict as a whole, nor in any individual stringified value.
        assert key not in str(f)
        for value in f.values():
            assert key not in str(value)
        # The hint is a masked fragment (or the kind), never the value itself.
        assert f["hint"] != key
        assert key not in f["hint"]


def test_hint_is_masked_fragment() -> None:
    key = _openai_key()
    f = find_secrets("k=" + key, "c.py")[0]
    hint = f["hint"]
    # Masked form: short prefix … last4 (e.g. "sk-x…xxxx"), and crucially the
    # hint is not a substring of the secret.
    assert "…" in hint
    assert hint not in key
    assert key not in hint


def test_detects_multiple_kinds_with_lines() -> None:
    k1, k2 = _stripe_live_key(), _aws_akid()
    text = "line1\nsecret=" + k1 + "\nline3\nakid=" + k2 + "\n"
    findings = find_secrets(text, "f.env")

    kinds = {f["kind"] for f in findings}
    assert "stripe-live-sk" in kinds
    assert "aws-akid" in kinds
    # Lines are reported correctly.
    by_kind = {f["kind"]: f["line"] for f in findings}
    assert by_kind["stripe-live-sk"] == 2
    assert by_kind["aws-akid"] == 4
    # And neither raw value leaks.
    blob = str(findings)
    assert k1 not in blob and k2 not in blob


def test_detects_github_token() -> None:
    findings = find_secrets("token: " + _ghp(), "ci.yml")
    assert any(f["kind"] == "github-pat" for f in findings)
    assert _ghp() not in str(findings)


def test_detects_private_key_header() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    findings = find_secrets(text, "id_rsa")
    assert any(f["kind"] == "private-key" for f in findings)


# --- false-positive reduction ----------------------------------------------- #
def test_clean_text_yields_nothing() -> None:
    assert find_secrets("just a normal sentence, nothing secret here", "a.py") == []
    assert find_secrets("", "empty.py") == []


def test_placeholder_is_suppressed() -> None:
    # An obvious example value carries a placeholder marker and is ignored.
    text = "OPENAI_API_KEY=sk-YOUR_KEY_HERE_xxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert find_secrets(text, ".env.example") == []


def test_allow_pragma_suppresses() -> None:
    key = _openai_key()
    text = "api=" + key + "  # pragma: allowlist secret"
    assert find_secrets(text, "fixtures.py") == []


def test_no_duplicate_for_single_key() -> None:
    # One key should not be reported by multiple overlapping patterns.
    key = _openai_key()
    findings = find_secrets("k=" + key, "c.py")
    assert len(findings) == 1


# --- repo walk (impure) ----------------------------------------------------- #
def test_scan_repo_finds_planted_key(tmp_path) -> None:
    key = _openai_key()
    (tmp_path / "app.py").write_text("KEY = '" + key + "'\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")

    findings = scan_repo(tmp_path)
    assert len(findings) == 1
    assert findings[0]["path"] == "app.py"
    assert findings[0]["kind"] == "openai-key"
    assert key not in str(findings)


def test_scan_repo_skips_vendor_dirs(tmp_path) -> None:
    key = _stripe_live_key()
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("k='" + key + "'\n", encoding="utf-8")
    (tmp_path / "src.py").write_text("ok = True\n", encoding="utf-8")

    findings = scan_repo(tmp_path)
    assert findings == []  # the key lives under node_modules → skipped


def test_scan_repo_respects_gitignore(tmp_path) -> None:
    key = _ghp()
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "leak.txt").write_text("token " + key + "\n", encoding="utf-8")

    assert scan_repo(tmp_path) == []


def test_scan_repo_skips_binary_and_large(tmp_path) -> None:
    key = _openai_key()
    # A "binary" file (NUL byte) containing the key — must be skipped.
    (tmp_path / "blob.json").write_bytes(b"\x00" + ("k=" + key).encode())
    # An oversized file — must be skipped by the size cap.
    big = tmp_path / "big.txt"
    big.write_text("a" * (secret_scan._MAX_FILE_BYTES + 10) + key, encoding="utf-8")

    assert scan_repo(tmp_path) == []


# --- rendering -------------------------------------------------------------- #
def _render_to_str(findings) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    render_findings(findings, console)
    return buf.getvalue()


def test_render_no_secrets() -> None:
    out = _render_to_str([])
    assert "no secrets found" in out


def test_render_table_has_location_and_kind_not_value() -> None:
    key = _openai_key()
    findings = find_secrets("first\nKEY=" + key, "settings.py")
    out = _render_to_str(findings)

    assert "1 potential secret" in out
    assert "settings.py:2" in out
    assert "openai-key" in out
    # The whole secret never appears in rendered output.
    assert key not in out


def test_render_plural_header() -> None:
    text = "a=" + _stripe_live_key() + "\nb=" + _aws_akid()
    findings = find_secrets(text, "f.env")
    out = _render_to_str(findings)
    assert "2 potential secrets" in out
