"""Estimate a task before doing it — `ronin estimate "<task>"`.

ronin scopes a task read-only, then reports its T-shirt size, the files it'd
touch, the main risks, and a projected $ cost on your current model — so you can
decide before spending the tokens. The cost projection + the structured-estimate
parser are pure and unit-tested; the sizing itself is the agent's read-only call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Rough (input, output) token budgets per T-shirt size — for the cost projection.
_SIZE_TOKENS: dict[str, tuple[int, int]] = {
    "S": (15_000, 2_000),
    "M": (50_000, 6_000),
    "L": (150_000, 18_000),
    "XL": (400_000, 50_000),
}

ESTIMATE_PROMPT = (
    "Estimate the effort for this task WITHOUT doing it:\n\n{task}\n\n"
    "Explore the codebase read-only first. Then give a short rationale and end with "
    "EXACTLY these three lines:\n"
    "SIZE: S|M|L|XL\nFILES: <number of files likely to change>\nRISK: low|medium|high"
)

_SIZE_RE = re.compile(r"^\s*SIZE:\s*(XL|S|M|L)\b", re.MULTILINE | re.IGNORECASE)
_FILES_RE = re.compile(r"^\s*FILES:\s*(\d+)", re.MULTILINE | re.IGNORECASE)
_RISK_RE = re.compile(r"^\s*RISK:\s*(low|medium|high)\b", re.MULTILINE | re.IGNORECASE)


@dataclass
class Estimate:
    size: str = "M"
    files: int = 0
    risk: str = "medium"


def parse_estimate(text: str) -> Estimate:
    """Parse SIZE / FILES / RISK from the agent's reply. Tolerant defaults. Pure."""
    e = Estimate()
    if (m := _SIZE_RE.search(text or "")):
        e.size = m.group(1).upper()
    if (m := _FILES_RE.search(text or "")):
        e.files = int(m.group(1))
    if (m := _RISK_RE.search(text or "")):
        e.risk = m.group(1).lower()
    return e


def project_cost(size: str, provider: str, model: str | None) -> float:
    """Projected USD cost for a task of ``size`` on provider+model. Pure."""
    from .cost import estimate_cost
    in_tok, out_tok = _SIZE_TOKENS.get((size or "M").upper(), _SIZE_TOKENS["M"])
    return estimate_cost(provider, model, in_tok, out_tok)
