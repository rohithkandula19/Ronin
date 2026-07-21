"""Password hashing and session tokens — stdlib only, no plaintext at rest."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000
_ALGO = "sha256"


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Return a `pbkdf2$iterations$salt$hash` string. Never stores plaintext."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(_ALGO, password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters_s, salt, digest = stored.split("$", 3)
        iters = int(iters_s)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(_ALGO, password.encode(), salt.encode(), iters).hex()
    return hmac.compare_digest(candidate, digest)


def new_session_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Only the hash is stored."""
    raw = "ros_" + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
