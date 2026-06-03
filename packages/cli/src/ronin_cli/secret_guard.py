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


# Substrings that mark an obvious example/placeholder, not a live credential —
# e.g. AWS's documented "AKIAIOSFODNN7EXAMPLE". Suppress these to avoid flagging
# docs, fixtures, and test data.
_PLACEHOLDER_MARKERS = ("EXAMPLE", "PLACEHOLDER", "YOUR_", "YOURKEY", "XXXXXX",
                        "REDACTED", "1234567890", "DEADBEEF", "TEST_TOKEN")
# Inline allow-pragmas: put one on the same line as a known-safe value (a fixture,
# a doc example) to suppress it — the standard detect-secrets-style escape hatch.
_ALLOW_PRAGMAS = ("ronin:allow-secret", "pragma: allowlist secret", "noqa: secret")


def _line_of(text: str, start: int, end: int) -> str:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return text[ls:le if le != -1 else len(text)]


def scan_secrets(text: str) -> list[str]:
    """Return a list of secret *labels* found in ``text`` (e.g. ['aws-access-key',
    'github-token']). Obvious example/placeholder values are suppressed. Empty
    when clean. Never raises."""
    if not text:
        return []
    try:
        res = _scanner().scan(text)
    except Exception:  # noqa: BLE001 — a guard must never break the edit path
        return []
    if not res.flagged:
        return []
    labels: list[str] = []
    for f in res.findings:
        match = text[f.span_start:f.span_end].upper()
        if any(m in match for m in _PLACEHOLDER_MARKERS):
            continue  # documented example / placeholder, not a real secret
        line = _line_of(text, f.span_start, f.span_end).lower()
        if any(p in line for p in _ALLOW_PRAGMAS):
            continue  # explicitly allow-listed on this line
        labels.append(f.label)
    return labels


def secret_warning(text: str) -> str | None:
    """A rich-markup warning line if ``text`` looks like it contains secrets,
    else None."""
    labels = scan_secrets(text)
    if not labels:
        return None
    kinds = ", ".join(sorted(set(labels)))
    return (f"  [bold #f7768e]⚠ possible secret in this change[/bold #f7768e] "
            f"[dim]({kinds}) — don't commit a real key[/dim]")
