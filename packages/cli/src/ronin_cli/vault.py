"""ronin vault — encryption at rest + a transparent privacy audit.

This module is ronin's *data-safety* centerpiece. It has two independent pillars,
both pure/testable and fully offline (no network, ever):

1. **Encryption at rest** (opt-in) for the sensitive local data ronin keeps —
   provider tokens, long-term memory, session transcripts. The crypto primitives
   are tiny and self-contained:

   - ``derive_key(passphrase, salt)`` — PBKDF2-HMAC-SHA256 (``hashlib``), 200k
     iterations, returns a 32-byte url-safe base64 key (Fernet-compatible).
   - ``encrypt(plaintext, key)`` / ``decrypt(token, key)`` — round-trips are
     **exact**, and a wrong key or a tampered ciphertext is **rejected** (raises
     ``InvalidToken``, never returns garbage). We prefer ``cryptography.Fernet``
     when it's importable; otherwise we fall back to a documented authenticated
     stdlib scheme (HMAC-SHA256 encrypt-then-MAC over a SHA-256 counter
     keystream — see ``_StdlibCrypto``). AES is not in the stdlib, so the
     fallback is a keystream cipher, but it is *authenticated*: tampering fails.
   - ``lock_file(path, key)`` / ``unlock_file(path, key)`` — encrypt/decrypt a
     file in place.

2. **``ronin privacy`` audit** — a read-only inventory of everything ronin stores
   on your machine and whether it's protected:

   - ``audit(home_paths)`` — pure: walk ``.ronin/`` and ``~/.ronin`` (and any
     paths you pass), classify each item (secret / memory / session / cache),
     and report whether it's encrypted and whether it holds a *plaintext* API key
     (sk- / rk_ / xoxb- … prefixes). Detecting an unencrypted key on disk is the
     whole point.
   - ``render_privacy(console, findings)`` — a clear terminal report: what ronin
     stores, where, encrypted vs plaintext, redaction status, a one-line safety
     verdict, and remediation tips.

SAFETY THEME: the audit is strictly read-only and **never prints a secret value**
— only that a secret exists and whether it's protected. There is no code path
here that emits the bytes of a key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# PBKDF2 cost. 200k iterations of SHA-256 is a sane 2020s default for a local
# passphrase-derived key (OWASP-ish); high enough to slow brute force, low enough
# to stay snappy for an interactive `vault unlock`.
_PBKDF2_ITERATIONS = 200_000
_KEY_LEN = 32  # 256-bit key (Fernet wants exactly 32 url-safe-b64 bytes)

# Magic + version prefix for our stdlib fallback container, so a Fernet token
# (which starts with the byte 0x80) is never confused with a fallback token and
# decrypt can refuse a format it didn't produce.
_FALLBACK_MAGIC = b"RVK1"  # Ronin Vault Kit, container v1

# How we recognise a file ronin itself encrypted, for the audit's `encrypted`
# flag. Fernet tokens are url-safe base64 beginning with the version byte 0x80
# (which base64-encodes to the literal "gA"); our fallback container begins with
# the ASCII magic above. We check cheaply on the first few bytes.
_FERNET_PREFIX = b"gA"  # base64 of the 0x80 Fernet version byte

# Plaintext-secret detector for the audit. These prefixes are high-confidence
# live-credential shapes; we only ever record the prefix that matched, never the
# value. Mirrors (a subset of) ``redact.py`` but tuned for "is a raw key sitting
# in this file".
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sk-",   re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),       # OpenAI / Anthropic-style
    ("rk_",   re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{16,}")),  # Stripe restricted
    ("sk_",   re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}")),  # Stripe secret
    ("xoxb-", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),       # Slack
    ("ghp_",  re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),         # GitHub
    ("AKIA",  re.compile(r"\bAKIA[0-9A-Z]{16}\b")),                 # AWS access key
)

# When a file is one of these names (or under one of these dirs) we know what
# *kind* of ronin data it is, for the audit. Anything else under a ronin home is
# reported as "cache".
_SECRET_FILES = {"config.toml"}
_MEMORY_FILES = {"memory.json"}
_SESSION_DIRS = {"sessions"}
_MEMORY_DIRS = {"runs"}  # run transcripts are memory-ish; grouped under "memory"

# Cap how much of any single file we read for the plaintext-secret scan — a
# planted key shows up in the head, and we never want to slurp a huge transcript
# into memory just to audit it.
_SCAN_BYTES = 256 * 1024


class InvalidToken(Exception):
    """Raised when decryption fails: wrong key, tampered ciphertext, or a token
    this vault did not produce. Never leaks plaintext."""


# --------------------------------------------------------------------------- #
# Pillar 1 — Encryption at rest
# --------------------------------------------------------------------------- #


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte url-safe-base64 key from ``passphrase`` + ``salt``.

    PBKDF2-HMAC-SHA256 via ``hashlib`` (stdlib only). The output is base64
    url-safe encoded so it's directly usable as a ``Fernet`` key *and* as the
    keying material for the stdlib fallback. Pure and deterministic: same
    ``(passphrase, salt)`` always yields the same key.

    ``salt`` should be at least 8 bytes and unique per vault; reusing a salt
    across passphrases weakens the derivation. Raises ``ValueError`` on empty
    inputs so a caller never accidentally derives a key from nothing.
    """
    if not passphrase:
        raise ValueError("passphrase must be non-empty")
    if not salt or len(salt) < 8:
        raise ValueError("salt must be at least 8 bytes")
    raw = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_LEN
    )
    return base64.urlsafe_b64encode(raw)


def _have_fernet() -> bool:
    """True iff ``cryptography.Fernet`` is importable (it's an optional dep)."""
    try:
        import cryptography.fernet  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import failure → use the fallback
        return False


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt ``plaintext`` with ``key`` (the output of ``derive_key``).

    Returns an opaque authenticated token. Prefers ``cryptography.Fernet``
    (AES-128-CBC + HMAC) when available; otherwise uses the stdlib fallback
    (``_StdlibCrypto``: SHA-256 counter keystream, encrypt-then-MAC with
    HMAC-SHA256). Either way the token is authenticated — see ``decrypt``.

    ``plaintext`` must be ``bytes``. Raises ``TypeError`` otherwise so a stray
    ``str`` doesn't get silently mis-encoded.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    if _have_fernet():
        from cryptography.fernet import Fernet
        return Fernet(key).encrypt(bytes(plaintext))
    return _StdlibCrypto(key).encrypt(bytes(plaintext))


def decrypt(token: bytes, key: bytes) -> bytes:
    """Decrypt a token produced by ``encrypt``; return the exact plaintext.

    A wrong key, a tampered token, or a token this vault didn't produce raises
    ``InvalidToken`` — we never return garbage or partial plaintext. We detect
    the container format from the token itself (Fernet's 0x80 version byte vs our
    ``RVK1`` magic) so a vault written with Fernet still decrypts even if the
    optional dep layout differs, as long as ``cryptography`` is importable.
    """
    if not isinstance(token, (bytes, bytearray)):
        raise TypeError("token must be bytes")
    token = bytes(token)
    # Our fallback container is self-describing — handle it regardless of whether
    # Fernet is installed, so a token never becomes unreadable after an env change.
    if token.startswith(_FALLBACK_MAGIC):
        return _StdlibCrypto(key).decrypt(token)
    if _have_fernet():
        from cryptography.fernet import Fernet, InvalidToken as _FernetInvalid
        try:
            return Fernet(key).decrypt(token)
        except _FernetInvalid as exc:  # wrong key / tamper / not a Fernet token
            raise InvalidToken("decryption failed (wrong key or tampered data)") from exc
    # No Fernet and not our magic → we can't read it.
    raise InvalidToken("unrecognized token format (cannot decrypt)")


@dataclass
class _StdlibCrypto:
    """Authenticated symmetric encryption using *only* the stdlib.

    AES isn't in the Python stdlib, so this is a documented fallback used only
    when ``cryptography`` isn't installed. The construction is encrypt-then-MAC:

      * Two sub-keys are derived from the vault key via HMAC-SHA256 ("enc"/"mac")
        so the keystream and the authentication tag never share key material.
      * A random 16-byte nonce seeds a SHA-256 *counter keystream*: block ``i`` is
        ``SHA256(enc_key || nonce || u64(i))``; the keystream is XORed with the
        plaintext (a stream cipher).
      * The tag is ``HMAC-SHA256(mac_key, magic || nonce || ciphertext)``. On
        decrypt we recompute it and compare in constant time; any mismatch (wrong
        key OR a flipped byte) raises ``InvalidToken``.

    Container layout (all concatenated, no base64 — the bytes are opaque):
        magic(4) ‖ nonce(16) ‖ tag(32) ‖ ciphertext(N)

    This is *not* a substitute for AES-GCM; it's a graceful-degradation path so
    "lock my tokens" still works on a bare Python with the security property that
    matters most here: tampering and wrong keys are detected, never silently
    accepted.
    """

    key: bytes

    def _subkeys(self) -> tuple[bytes, bytes]:
        enc = hmac.new(self.key, b"ronin-vault-enc", hashlib.sha256).digest()
        mac = hmac.new(self.key, b"ronin-vault-mac", hashlib.sha256).digest()
        return enc, mac

    @staticmethod
    def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < n:
            block = hashlib.sha256(enc_key + nonce + struct.pack(">Q", counter)).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:n])

    def encrypt(self, plaintext: bytes) -> bytes:
        enc_key, mac_key = self._subkeys()
        nonce = os.urandom(16)
        ks = self._keystream(enc_key, nonce, len(plaintext))
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, ks))
        tag = hmac.new(mac_key, _FALLBACK_MAGIC + nonce + ciphertext, hashlib.sha256).digest()
        return _FALLBACK_MAGIC + nonce + tag + ciphertext

    def decrypt(self, token: bytes) -> bytes:
        enc_key, mac_key = self._subkeys()
        header = len(_FALLBACK_MAGIC) + 16 + 32
        if len(token) < header or not token.startswith(_FALLBACK_MAGIC):
            raise InvalidToken("malformed vault token")
        nonce = token[4:20]
        tag = token[20:52]
        ciphertext = token[52:]
        expected = hmac.new(mac_key, _FALLBACK_MAGIC + nonce + ciphertext, hashlib.sha256).digest()
        # Constant-time compare: wrong key OR tamper both fail here, before we
        # ever produce plaintext.
        if not hmac.compare_digest(tag, expected):
            raise InvalidToken("authentication failed (wrong key or tampered data)")
        ks = self._keystream(enc_key, nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, ks))


def lock_file(path: Path | str, key: bytes) -> Path:
    """Encrypt the file at ``path`` in place with ``key``. Returns the path.

    Reads the bytes, encrypts, and overwrites. Idempotency guard: if the file
    already looks vault-encrypted, it's left untouched (so locking twice doesn't
    double-encrypt and strand the data). Raises ``FileNotFoundError`` if missing.
    """
    p = Path(path)
    data = p.read_bytes()
    if is_encrypted_blob(data):
        return p  # already locked — don't double-wrap
    p.write_bytes(encrypt(data, key))
    return p


def unlock_file(path: Path | str, key: bytes) -> Path:
    """Decrypt the file at ``path`` in place with ``key``. Returns the path.

    Raises ``InvalidToken`` on a wrong key / tampered file (the file is left as
    it was — we only overwrite *after* a successful decrypt). If the file isn't
    encrypted, it's left untouched.
    """
    p = Path(path)
    data = p.read_bytes()
    if not is_encrypted_blob(data):
        return p  # already plaintext — nothing to do
    plaintext = decrypt(data, key)  # raises InvalidToken before we touch the file
    p.write_bytes(plaintext)
    return p


def is_encrypted_blob(data: bytes) -> bool:
    """Cheap heuristic: does ``data`` look like a ronin-vault ciphertext?

    True for our stdlib container (``RVK1`` magic) and for a Fernet token (which
    is url-safe base64 starting with the 0x80 version byte → ``gA``). Used by the
    audit's ``encrypted`` flag and by the lock/unlock idempotency guards. Pure.
    """
    if not data:
        return False
    if data.startswith(_FALLBACK_MAGIC):
        return True
    # Fernet token: url-safe base64, starts with "gA" (0x80 version byte). Be
    # strict-ish so a base64-looking config doesn't get mislabeled: require the
    # whole head to be in the base64url alphabet.
    head = data[:64]
    if head.startswith(_FERNET_PREFIX) and re.fullmatch(rb"[A-Za-z0-9_=-]+", head or b""):
        return True
    return False


# --------------------------------------------------------------------------- #
# Pillar 2 — `ronin privacy` audit
# --------------------------------------------------------------------------- #


def _classify(path: Path, root: Path) -> str:
    """Which kind of ronin data is this? secret | memory | session | cache."""
    rel_parts = set(path.relative_to(root).parts) if root in path.parents or path == root else set(path.parts)
    name = path.name
    if name in _SECRET_FILES:
        return "secret"
    if name in _MEMORY_FILES or rel_parts & _MEMORY_DIRS:
        return "memory"
    if rel_parts & _SESSION_DIRS:
        return "session"
    return "cache"


def _scan_plaintext_secret(path: Path) -> tuple[bool, list[str]]:
    """Read (a bounded head of) ``path`` and report whether it contains a
    plaintext API key. Returns ``(found, [prefixes])`` — only the *prefix labels*
    that matched, never any secret value. Encrypted/binary files won't match the
    text patterns, which is exactly what we want. Never raises."""
    try:
        with path.open("rb") as fh:
            blob = fh.read(_SCAN_BYTES)
    except OSError:
        return (False, [])
    if is_encrypted_blob(blob):
        return (False, [])  # protected — by construction not plaintext
    try:
        text = blob.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return (False, [])
    hits: list[str] = []
    for label, pat in _SECRET_PATTERNS:
        if pat.search(text):
            hits.append(label)
    return (bool(hits), hits)


def _iter_files(root: Path):
    """Yield every regular file under ``root`` (recursively). Tolerates a
    missing root and unreadable subtrees."""
    if not root.exists():
        return
    if root.is_file():
        yield root
        return
    try:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                yield p
    except OSError:
        return


def audit(home_paths: list[Path]) -> list[dict]:
    """Inventory everything ronin stores under ``home_paths``.

    ``home_paths`` are the roots to scan — typically ``.ronin`` (project) and
    ``~/.ronin`` (user-global), plus ``~/.config/ronin`` if you keep config
    there. For each regular file found, return a dict::

        {
          "path": str,                       # absolute path
          "kind": "secret"|"memory"|"session"|"cache",
          "bytes": int,                      # file size
          "encrypted": bool,                 # looks vault-encrypted on disk
          "contains_plaintext_secret": bool, # an unencrypted API key sits here
          "secret_prefixes": [str],          # which key shapes matched (labels only)
        }

    Pure given the filesystem and strictly **read-only** — it opens files for
    reading only and never prints or returns a secret value. De-duplicates roots
    and skips ones that don't exist, so passing both a missing and a present home
    is safe. Results are sorted by path for stable output/tests.
    """
    seen: set[Path] = set()
    findings: list[dict] = []
    # Resolve + de-dupe roots so an overlapping (.ronin given twice) scan doesn't
    # double-count.
    roots: list[Path] = []
    for hp in home_paths:
        try:
            rp = Path(hp).expanduser().resolve()
        except OSError:
            continue
        if rp not in roots:
            roots.append(rp)

    for root in roots:
        for f in _iter_files(root):
            rf = f.resolve()
            if rf in seen:
                continue
            seen.add(rf)
            try:
                size = rf.stat().st_size
            except OSError:
                size = 0
            try:
                blob_head = rf.open("rb").read(64)
            except OSError:
                blob_head = b""
            encrypted = is_encrypted_blob(blob_head)
            has_secret, prefixes = (False, [])
            if not encrypted:
                has_secret, prefixes = _scan_plaintext_secret(rf)
            findings.append({
                "path": str(rf),
                "kind": _classify(rf, root),
                "bytes": int(size),
                "encrypted": bool(encrypted),
                "contains_plaintext_secret": bool(has_secret),
                "secret_prefixes": prefixes,
            })

    findings.sort(key=lambda d: d["path"])
    return findings


def summarize(findings: list[dict]) -> dict:
    """Roll up ``audit`` findings into headline numbers + a safety verdict.

    Returns counts (total/by-kind), how many items are encrypted, and the key
    risk signal: ``exposed_secrets`` — files holding a plaintext API key. The
    ``verdict`` is one of ``"safe"`` (no plaintext secrets exposed) or
    ``"at_risk"`` (at least one plaintext key on disk). Pure."""
    total = len(findings)
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    encrypted = sum(1 for f in findings if f["encrypted"])
    exposed = [f for f in findings if f["contains_plaintext_secret"]]
    total_bytes = sum(f["bytes"] for f in findings)
    return {
        "total": total,
        "by_kind": by_kind,
        "encrypted": encrypted,
        "plaintext": total - encrypted,
        "exposed_secrets": len(exposed),
        "exposed_paths": [f["path"] for f in exposed],
        "total_bytes": total_bytes,
        "verdict": "at_risk" if exposed else "safe",
    }


def _human_bytes(n: int) -> str:
    """Compact human-readable size. Pure."""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}GB"


def render_privacy(console, findings: list[dict]) -> None:
    """Print the privacy report: what ronin stores, where, what's protected, the
    redaction posture, a one-line verdict, and remediation tips.

    Never prints a secret value — only that a secret exists and whether it's
    protected. ``console`` is a ``rich.console.Console``.
    """
    from rich.table import Table

    from .theme import ACCENT, ERR, MUTE, OK, WARN

    s = summarize(findings)

    console.print()
    console.print(f"[bold {ACCENT}]ronin privacy[/bold {ACCENT}]  "
                  f"[dim]· what ronin stores on this machine, and what's protected[/dim]")
    console.print()

    if not findings:
        console.print(f"  [{MUTE}]No ronin data found on disk — nothing stored locally yet.[/{MUTE}]")
        console.print()
        return

    kind_glyph = {"secret": "🔑", "memory": "🧠", "session": "💬", "cache": "📦"}
    kind_label = {
        "secret": "credentials (config.toml)",
        "memory": "long-term memory / run history",
        "session": "session transcripts",
        "cache": "other local data",
    }

    table = Table(box=None, padding=(0, 2))
    table.add_column("", style=ACCENT)                 # glyph
    table.add_column("item", style=ACCENT, overflow="fold")
    table.add_column("kind")
    table.add_column("size", justify="right", style=MUTE)
    table.add_column("at rest")
    table.add_column("plaintext key?")

    home = Path.home()
    for f in findings:
        # Show a friendly, home-relative path; never anything secret.
        path = Path(f["path"])
        try:
            shown = "~/" + str(path.relative_to(home)) if home in path.parents else str(path)
        except ValueError:
            shown = str(path)
        kind = f["kind"]
        if f["encrypted"]:
            rest = f"[{OK}]encrypted[/{OK}]"
        else:
            rest = f"[{WARN}]plaintext[/{WARN}]"
        if f["contains_plaintext_secret"]:
            # Only the prefix label, never the key itself.
            kinds = ", ".join(sorted(set(f.get("secret_prefixes", [])))) or "key"
            key_cell = f"[bold {ERR}]⚠ EXPOSED[/bold {ERR}] [dim]({kinds}…)[/dim]"
        elif kind == "secret" and not f["encrypted"]:
            key_cell = f"[{WARN}]secrets present[/{WARN}]"
        else:
            key_cell = f"[{MUTE}]—[/{MUTE}]"
        table.add_row(kind_glyph.get(kind, "•"), shown,
                      kind_label.get(kind, kind), _human_bytes(f["bytes"]),
                      rest, key_cell)

    console.print(table)
    console.print()

    # Roll-up line.
    parts = [f"{n} {k}" for k, n in sorted(s["by_kind"].items())]
    console.print(f"  [bold]{s['total']}[/bold] item(s) · "
                  f"{_human_bytes(s['total_bytes'])} · "
                  + "  ".join(parts))
    console.print(f"  [{OK}]{s['encrypted']} encrypted[/{OK}]   "
                  f"[{WARN}]{s['plaintext']} plaintext[/{WARN}]")
    console.print()

    # Redaction posture — ronin scrubs secrets/PII from shared text and logs.
    console.print(f"  [{MUTE}]Redaction:[/{MUTE}] `ronin redact` strips API keys/PII from any text "
                  f"you share; the Telegram bridge scrubs tokens from logs; the edit path "
                  f"warns before a secret is committed.")
    console.print()

    # One-line verdict + remediation.
    if s["verdict"] == "safe":
        console.print(f"  [bold {OK}]✓ SAFE[/bold {OK}] — no plaintext API keys were found sitting "
                      f"unencrypted on disk.")
        if s["plaintext"]:
            console.print(f"  [dim]Tip: for defense-in-depth, encrypt sensitive files at rest with "
                          f"`ronin vault lock <path>`.[/dim]")
    else:
        console.print(f"  [bold {ERR}]⚠ AT RISK[/bold {ERR}] — {s['exposed_secrets']} file(s) hold a "
                      f"plaintext API key on disk.")
        console.print(f"  [dim]Remediation:[/dim]")
        console.print(f"    [dim]• Encrypt it:[/dim] [bold]ronin vault lock <path>[/bold] "
                      f"[dim](derives a key from a passphrase; decrypt with `ronin vault unlock`)[/dim]")
        console.print(f"    [dim]• Or prefer env vars / your OS keychain over keys in config.toml.[/dim]")
        console.print(f"    [dim]• Rotate any key that may have been exposed.[/dim]")
    console.print()


def default_home_paths() -> list[Path]:
    """The standard places ronin keeps data — handy default for the audit.

    Honors ``RONIN_HOME`` (same override memory/runs use) for the user-global
    home, and always includes the project-local ``.ronin`` plus the config dir.
    Returned paths may or may not exist; ``audit`` skips missing ones.
    """
    home_env = os.environ.get("RONIN_HOME")
    user_home = Path(home_env) if home_env else Path.home() / ".ronin"
    return [
        Path(".ronin"),                       # project-local (cwd)
        user_home,                            # user-global runs/memory
        Path.home() / ".config" / "ronin",    # user-global config
    ]
