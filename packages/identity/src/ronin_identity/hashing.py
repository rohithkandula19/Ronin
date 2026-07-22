"""Deterministic, salted hashing for stored verifiers.

No plaintext credential is ever stored. Callers supply the salt so hashing is
deterministic and testable — there is no randomness inside this module.
"""

from __future__ import annotations

import hashlib
import hmac

from pydantic import BaseModel, ConfigDict

_DEFAULT_ALGORITHM = "sha256"
_DEFAULT_ITERATIONS = 100_000


class SecretHash(BaseModel):
    """A salted PBKDF2 digest of a sensitive value (never the value itself)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: str
    iterations: int
    salt: str
    digest: str


def hash_secret(
    raw: str,
    *,
    salt: str,
    iterations: int = _DEFAULT_ITERATIONS,
    algorithm: str = _DEFAULT_ALGORITHM,
) -> SecretHash:
    """Hash ``raw`` with a caller-supplied ``salt`` using PBKDF2-HMAC."""
    if not raw:
        raise ValueError("cannot hash an empty value")
    if not salt:
        raise ValueError("a non-empty salt is required")
    derived = hashlib.pbkdf2_hmac(
        algorithm, raw.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return SecretHash(
        algorithm=algorithm,
        iterations=iterations,
        salt=salt,
        digest=derived.hex(),
    )


def verify_secret(raw: str, hashed: SecretHash) -> bool:
    """Return True iff ``raw`` reproduces ``hashed`` (constant-time compare)."""
    if not raw:
        return False
    candidate = hashlib.pbkdf2_hmac(
        hashed.algorithm,
        raw.encode("utf-8"),
        hashed.salt.encode("utf-8"),
        hashed.iterations,
    )
    return hmac.compare_digest(candidate.hex(), hashed.digest)
