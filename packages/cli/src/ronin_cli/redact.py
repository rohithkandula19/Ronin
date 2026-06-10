"""Redact secrets & PII from text — `ronin redact`.

Paste a log, stack trace, or config and ronin replaces emails, IPs, API keys,
tokens and JWTs with ``[REDACTED-TYPE]`` placeholders so you can share it safely.
Pattern-based, pure, and unit-tested — nothing leaves the machine.
"""
from __future__ import annotations

import re

# Order matters: match specific high-confidence secrets before generic ones.
_PATTERNS: list[tuple[str, str]] = [
    ("JWT", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("AWS_KEY", r"\bAKIA[0-9A-Z]{16}\b"),
    ("GITHUB_TOKEN", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    ("SLACK_TOKEN", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("OPENAI_KEY", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ("STRIPE_KEY", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ("PRIVATE_KEY", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("BEARER", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    ("EMAIL", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("IPV4", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
]


def redact_text(text: str) -> dict:
    """Replace secrets/PII with ``[REDACTED-TYPE]`` markers. Returns the redacted
    text and a per-type count. Pure."""
    counts: dict[str, int] = {}
    out = text
    for label, pat in _PATTERNS:
        def _repl(_m: re.Match, _label: str = label) -> str:
            counts[_label] = counts.get(_label, 0) + 1
            return f"[REDACTED-{_label}]"
        out = re.sub(pat, _repl, out)
    return {"text": out, "redactions": counts, "total": sum(counts.values())}
