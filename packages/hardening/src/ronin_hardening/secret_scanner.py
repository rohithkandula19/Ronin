"""Detect leaked credentials in agent output.

Defense against: an agent reading a piece of customer data, an env-dump, or a
config file that contains a real secret, and then echoing that secret back in
its answer or its trace.

This is the *output* counterpart to ``injection.InjectionScanner``. Wire it
right before agent output leaves your process — into a log sink, a Slack
message, an email, an HTTP response body.

Pattern set covers the highest-bleed credentials. It is intentionally narrow:
false positives in a "we redacted your secret" message are more annoying than
useful, so we err toward specific high-entropy provider-prefixed keys.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# Provider-prefixed key patterns. The prefixes give us low false-positive rates.
# Anchored loosely: catch the prefix + the high-entropy tail.
SECRET_PATTERNS: list[tuple[str, str, str]] = [
    # (label, regex, placeholder)
    ("anthropic-key",   r"\bsk-ant-[A-Za-z0-9_\-]{20,}",                          "[anthropic-key]"),
    ("openai-key",      r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}",                    "[openai-key]"),
    ("stripe-live-sk",  r"\bsk_live_[A-Za-z0-9]{20,}",                            "[stripe-live-secret]"),
    ("stripe-test-sk",  r"\bsk_test_[A-Za-z0-9]{20,}",                            "[stripe-test-secret]"),
    ("stripe-rk",       r"\brk_(?:live|test)_[A-Za-z0-9]{20,}",                   "[stripe-restricted-key]"),
    ("stripe-pk",       r"\bpk_(?:live|test)_[A-Za-z0-9]{20,}",                   "[stripe-publishable-key]"),
    ("github-pat",      r"\bghp_[A-Za-z0-9]{30,}",                                "[github-pat]"),
    ("github-oauth",    r"\bgh[ousr]_[A-Za-z0-9]{20,}",                           "[github-token]"),
    ("slack-bot",       r"\bxoxb-\d+-\d+-[A-Za-z0-9]{20,}",                       "[slack-bot-token]"),
    ("slack-user",      r"\bxoxp-\d+-\d+-\d+-[A-Za-z0-9]{20,}",                   "[slack-user-token]"),
    ("slack-app",       r"\bxapp-\d+-[A-Za-z0-9]+-\d+-[A-Za-z0-9]{20,}",          "[slack-app-token]"),
    ("aws-akid",        r"\bAKIA[0-9A-Z]{16}\b",                                  "[aws-access-key-id]"),
    ("linear-key",      r"\blin_api_[A-Za-z0-9]{20,}",                            "[linear-api-key]"),
    ("jwt",             r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "[jwt]"),
    ("fernet",          r"\bgAAAAA[A-Za-z0-9_\-]{50,}={0,2}",                     "[fernet-token]"),
    ("notion-secret",   r"\bsecret_[A-Za-z0-9]{40,}",                             "[notion-secret]"),
    ("resend-key",      r"\bre_[A-Za-z0-9_]{20,}",                                "[resend-key]"),
]


class SecretFinding(BaseModel):
    label: str
    match_prefix: str  # first 8 chars of the match — never the whole secret
    span_start: int
    span_end: int


class SecretScanResult(BaseModel):
    flagged: bool
    redacted: str
    findings: list[SecretFinding] = Field(default_factory=list)


class SecretLeakScanner(BaseModel):
    """Scan a piece of agent output for leaked credentials.

    Two-mode usage:
    1. ``scanner.scan(text)`` — pure inspection; returns the redacted string +
       structured findings. Use this for tracing/logging.
    2. ``scanner.assert_clean(text)`` — raises ``SecretLeakDetected`` on any
       hit. Use this when you absolutely don't want output to leave your
       process if it contains a key (e.g. right before posting to Slack).
    """

    extra_patterns: list[tuple[str, str, str]] = Field(default_factory=list)

    def _all_patterns(self) -> list[tuple[str, str, str]]:
        return SECRET_PATTERNS + self.extra_patterns

    def scan(self, text: str) -> SecretScanResult:
        if not text:
            return SecretScanResult(flagged=False, redacted=text)

        findings: list[SecretFinding] = []
        redacted_parts: list[str] = []
        cursor = 0

        # Single pass that handles overlapping patterns by preferring the
        # earliest start.
        spans: list[tuple[int, int, str, str]] = []  # (start, end, label, placeholder)
        for label, pattern, placeholder in self._all_patterns():
            for match in re.finditer(pattern, text):
                spans.append((match.start(), match.end(), label, placeholder))
        spans.sort(key=lambda s: (s[0], -s[1]))

        # Deduplicate overlapping spans — keep the first (broadest) match.
        merged: list[tuple[int, int, str, str]] = []
        for span in spans:
            if merged and span[0] < merged[-1][1]:
                continue
            merged.append(span)

        for start, end, label, placeholder in merged:
            redacted_parts.append(text[cursor:start])
            redacted_parts.append(placeholder)
            findings.append(SecretFinding(
                label=label,
                match_prefix=text[start:start + 8],
                span_start=start,
                span_end=end,
            ))
            cursor = end

        redacted_parts.append(text[cursor:])
        return SecretScanResult(
            flagged=bool(findings),
            redacted="".join(redacted_parts),
            findings=findings,
        )

    def assert_clean(self, text: str) -> None:
        result = self.scan(text)
        if result.flagged:
            raise SecretLeakDetected(result.findings)


class SecretLeakDetected(Exception):
    """Raised by ``SecretLeakScanner.assert_clean`` when output contains keys."""

    def __init__(self, findings: list[SecretFinding]):
        labels = ", ".join(sorted({f.label for f in findings}))
        super().__init__(f"output contains leaked secrets: {labels}")
        self.findings = findings
