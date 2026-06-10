"""Hashing & checksums — `ronin hash`.

`ronin hash "text"` or `ronin hash --file path` computes common digests
(md5, sha1, sha256, sha512). Files are streamed in chunks so large files don't
load into memory. Pure stdlib (``hashlib``), offline.

md5/sha1 are for checksums/legacy interop only — not cryptographically safe.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ALGORITHMS = ("md5", "sha1", "sha256", "sha512")
_CHUNK = 1 << 16  # 64 KiB streaming reads


def hash_text(text: str, algorithms: tuple[str, ...] = ALGORITHMS) -> dict:
    """Digests of a UTF-8 string. Pure. Returns ``{algo: hexdigest, ...}``."""
    data = text.encode("utf-8")
    return {algo: hashlib.new(algo, data).hexdigest() for algo in algorithms}


def hash_file(path: str | Path, algorithms: tuple[str, ...] = ALGORITHMS) -> dict:
    """Digests of a file, streamed in chunks. Returns ``{algo: hex}`` or error."""
    p = Path(path)
    if not p.is_file():
        return {"error": f"not a file: {p}"}
    workers = {algo: hashlib.new(algo) for algo in algorithms}
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            for h in workers.values():
                h.update(chunk)
    return {algo: h.hexdigest() for algo, h in workers.items()}


def verify(expected: str, actual_digests: dict) -> bool:
    """True if ``expected`` (case-insensitive) matches any computed digest. Pure."""
    target = expected.strip().lower()
    return any(
        digest == target for algo, digest in actual_digests.items() if algo != "error"
    )
