"""Ronin AI OS research & citations — source-first, never fabricated.

Invariants enforced here (with tests):
- a citation must reference a source that actually exists in the notebook;
  deleting a source drops its citations, it does not leave a dangling cite;
- a claim with no supporting source is INFERENCE and is labeled as such —
  never dressed up with a citation;
- there is no code path that invents a URL: sources are added explicitly and
  a claim can only cite source ids the notebook holds;
- when no source was retrieved, the report says the answer rests on model
  knowledge, not on current sources.
"""

from .notebook import (
    Claim,
    ClaimStatus,
    Notebook,
    Source,
    SupportStrength,
    UnknownSourceError,
)

__all__ = [
    "Claim",
    "ClaimStatus",
    "Notebook",
    "Source",
    "SupportStrength",
    "UnknownSourceError",
]
