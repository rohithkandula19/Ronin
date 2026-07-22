"""Configurable redaction with mandatory review.

Pattern-based redaction is useful and imperfect. This module therefore never
returns a "safe" verdict: it returns findings and a redacted text, and the
provenance layer refuses training eligibility until a human review moves the
item to redacted_reviewed / clean_reviewed. That refusal is the guarantee;
the regexes are only the assistant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Each rule: (kind, compiled pattern, replacement)
_DEFAULT_RULES: list[tuple[str, re.Pattern, str]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("phone", re.compile(r"(?<![\d.])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3}[ .-]\d{3,4}[ .-]?\d{0,4}(?![\d.])"), "[PHONE]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
    ("api_key", re.compile(r"\b(?:sk|pk|rk|csk|ghp|gho|xoxb|xoxp)[-_][A-Za-z0-9_\-]{12,}\b"), "[API_KEY]"),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[AWS_KEY]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), "[JWT]"),
    ("password_assignment", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"), r"\1: [PASSWORD]"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    ("mrn", re.compile(r"(?i)\b(mrn|medical record number)\s*[:#]?\s*\d{5,}\b"), r"\1: [MRN]"),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[IBAN]"),
    ("street_address", re.compile(r"(?i)\b\d{1,5}\s+[A-Za-z][A-Za-z ]{2,30}\s(?:st|street|ave|avenue|rd|road|blvd|lane|ln|drive|dr)\b\.?"), "[ADDRESS]"),
]


@dataclass(frozen=True)
class RedactionFinding:
    kind: str
    count: int


@dataclass
class RedactionResult:
    text: str
    findings: list[RedactionFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """No patterns matched. NOT a guarantee that no PII exists."""
        return not self.findings


def redact(
    text: str,
    *,
    extra_patterns: dict[str, str] | None = None,
    disabled_kinds: set[str] | None = None,
) -> RedactionResult:
    """Apply the rule set; return redacted text + per-kind finding counts.

    `extra_patterns` maps kind -> regex (replaced with [KIND-UPPER]).
    `disabled_kinds` removes built-in rules by kind name.
    """
    rules = [r for r in _DEFAULT_RULES if not disabled_kinds or r[0] not in disabled_kinds]
    for kind, pattern in (extra_patterns or {}).items():
        rules.append((kind, re.compile(pattern), f"[{kind.upper()}]"))

    findings: list[RedactionFinding] = []
    out = text
    for kind, pattern, replacement in rules:
        out, count = pattern.subn(replacement, out)
        if count:
            findings.append(RedactionFinding(kind=kind, count=count))
    return RedactionResult(text=out, findings=findings)
