"""Sentinel mode — ronin abstains instead of bluffing.

The failure you saw first-hand: a model asked about something it doesn't know
answers *confidently and wrongly*. Sentinel mode adds an explicit confidence
signal and a licence to say "I'm not sure." The agent ends each answer with a
``CONFIDENCE: high|medium|low`` line; on **low** it must name what it's unsure
about and what it'd verify, rather than inventing.

That signal also powers the **escalation ladder**: when routing is on and a cheap
blade returns low confidence, the session retries the turn once on the strong
blade — so uncertainty gets escalated to a better model instead of shipped.

Pure prompt + parser; fully unit-tested.
"""
from __future__ import annotations

import re

SENTINEL_SYSTEM = (
    "\n\n## Sentinel mode (abstain over bluff)\n"
    "Accuracy beats confidence. If you are NOT sure — missing context, ambiguous "
    "request, a fact you can't verify from the code or tools in front of you — say "
    "so plainly and name exactly what you'd check next. Never invent file paths, "
    "APIs, flags, or behavior to sound complete.\n"
    "End EVERY reply with one line, exactly:\n"
    "  CONFIDENCE: high    (you verified it / it's directly supported)\n"
    "  CONFIDENCE: medium  (reasonable but unverified)\n"
    "  CONFIDENCE: low     (guessing, or missing key info — and say what)\n"
)

_CONFIDENCE_RE = re.compile(r"^\s*CONFIDENCE:\s*(high|medium|low)\s*$", re.MULTILINE | re.IGNORECASE)

_BADGE = {"high": "[green]● confidence: high[/green]",
          "medium": "[yellow]● confidence: medium[/yellow]",
          "low": "[red]▲ confidence: low — verify before trusting[/red]"}


def parse_confidence(text: str) -> str | None:
    """The confidence level the agent declared, or None if it didn't."""
    matches = _CONFIDENCE_RE.findall(text or "")
    return matches[-1].lower() if matches else None


def strip_confidence(text: str) -> str:
    """Remove the CONFIDENCE marker line(s) from text shown to the user."""
    return _CONFIDENCE_RE.sub("", text or "").rstrip()


def is_low(text: str) -> bool:
    """True when the agent flagged low confidence (the escalation trigger)."""
    return parse_confidence(text) == "low"


def confidence_badge(text: str) -> str | None:
    """A rich-markup badge for the declared confidence, or None."""
    level = parse_confidence(text)
    return _BADGE.get(level) if level else None
