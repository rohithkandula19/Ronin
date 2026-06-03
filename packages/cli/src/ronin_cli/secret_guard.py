"""Secret-leak guard for the write/commit path.

Before you approve an edit, ronin scans the *resulting* file content for things
that look like live credentials (API keys, tokens, private keys) using the
hardening package's pattern set. It never blocks on its own — it surfaces a loud
warning above the approval prompt so a key never slips into a commit unnoticed.
Thin wrapper; the heavy lifting (and its tests) live in ronin_hardening.
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _scanner():
    from ronin_hardening.secret_scanner import SecretLeakScanner
    return SecretLeakScanner()


def scan_secrets(text: str) -> list[str]:
    """Return a list of secret *labels* found in ``text`` (e.g. ['aws-access-key',
    'github-token']). Empty when clean. Never raises."""
    if not text:
        return []
    try:
        res = _scanner().scan(text)
    except Exception:  # noqa: BLE001 — a guard must never break the edit path
        return []
    return [f.label for f in res.findings] if res.flagged else []


def secret_warning(text: str) -> str | None:
    """A rich-markup warning line if ``text`` looks like it contains secrets,
    else None."""
    labels = scan_secrets(text)
    if not labels:
        return None
    kinds = ", ".join(sorted(set(labels)))
    return (f"  [bold #f7768e]⚠ possible secret in this change[/bold #f7768e] "
            f"[dim]({kinds}) — don't commit a real key[/dim]")
