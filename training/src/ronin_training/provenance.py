"""Forge provenance: every dataset item carries its origin and its permissions.

An item with unknown origin, unlicensed content, or no consent is not a
training row — it is a liability. Training eligibility is computed from the
record, never asserted directly, and synthetic data must say it is synthetic.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    ronin_session = "ronin_session"
    tool_trace = "tool_trace"
    eval_failure = "eval_failure"
    human_reviewed = "human_reviewed"
    synthetic = "synthetic"
    imported_file = "imported_file"


class Consent(str, Enum):
    explicit = "explicit"  # a recorded consent reference exists
    owner_self = "owner_self"  # the operator's own authored content
    none = "none"


class RedactionState(str, Enum):
    unredacted = "unredacted"
    redacted_unreviewed = "redacted_unreviewed"
    redacted_reviewed = "redacted_reviewed"
    clean_reviewed = "clean_reviewed"  # scanner found nothing; a human confirmed


# Licenses acceptable as SFT sources. Deliberately mirrors the training
# package's existing stance: proprietary assistant output is banned.
_TRAINABLE_LICENSES = frozenset(
    {"cc0", "cc-by", "mit", "apache-2.0", "owner", "synthetic"}
)


class DatasetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    input: str = ""
    output: str = ""
    source: str = ""  # e.g. session id, file path, reviewer note
    source_type: SourceType
    owner: str = ""
    license: str = "owner"
    consent: Consent = Consent.none
    consent_ref: str = ""
    collection_date: str = ""  # ISO
    industry: str = ""
    country: str = ""
    language: str = "en"
    role: str = ""
    sensitivity: str = "normal"
    redaction: RedactionState = RedactionState.unredacted
    quality_score: float | None = None  # None = unscored, never assumed
    reviewer: str = ""
    synthetic: bool = False
    exclusion_reason: str = ""

    def training_eligibility(self) -> tuple[bool, str]:
        """Computed, never asserted. Returns (eligible, reason-if-not)."""
        if self.exclusion_reason:
            return (False, self.exclusion_reason)
        if self.source_type is SourceType.synthetic and not self.synthetic:
            return (False, "synthetic source must carry the synthetic label")
        if self.license.lower() not in _TRAINABLE_LICENSES:
            return (False, f"license {self.license!r} is not trainable")
        if self.consent is Consent.none:
            return (False, "no consent recorded")
        if self.consent is Consent.explicit and not self.consent_ref:
            return (False, "explicit consent claimed without a consent_ref")
        if self.redaction in (RedactionState.unredacted, RedactionState.redacted_unreviewed):
            return (False, f"redaction state {self.redaction.value!r} — review required")
        if self.sensitivity in ("health", "minor", "credential"):
            return (False, f"{self.sensitivity} data is excluded from training by policy")
        if not self.output.strip():
            return (False, "empty output")
        return (True, "")


class ProvenanceDataset:
    """A collection of provenance-tracked items with honest filtering."""

    def __init__(self, items: list[DatasetItem] | None = None) -> None:
        self.items: list[DatasetItem] = list(items or [])

    # --------------------------------------------------------------- IO
    @classmethod
    def load_jsonl(cls, path: str | Path) -> "ProvenanceDataset":
        items = []
        for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(DatasetItem.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"{path}:{n}: invalid dataset item: {exc}") from exc
        return cls(items)

    def save_jsonl(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "".join(item.model_dump_json() + "\n" for item in self.items),
            encoding="utf-8",
        )

    # ---------------------------------------------------------- filtering
    def eligible(self) -> list[DatasetItem]:
        return [i for i in self.items if i.training_eligibility()[0]]

    def excluded(self) -> list[tuple[DatasetItem, str]]:
        out = []
        for i in self.items:
            ok, reason = i.training_eligibility()
            if not ok:
                out.append((i, reason))
        return out

    def report(self) -> str:
        """Markdown provenance report: what's in, what's out, and why."""
        lines = ["# Dataset provenance report", ""]
        lines.append(f"- items: {len(self.items)}")
        lines.append(f"- training-eligible: {len(self.eligible())}")
        lines.append(f"- excluded: {len(self.excluded())}")
        by_source: dict[str, int] = {}
        for i in self.items:
            by_source[i.source_type.value] = by_source.get(i.source_type.value, 0) + 1
        lines += ["", "## By source type", ""]
        for src, count in sorted(by_source.items()):
            lines.append(f"- {src}: {count}")
        synth = sum(1 for i in self.items if i.synthetic)
        lines.append(f"- explicitly-labeled synthetic: {synth}")
        excluded = self.excluded()
        if excluded:
            lines += ["", "## Exclusions (named, never silent)", ""]
            for item, reason in excluded:
                lines.append(f"- **{item.id}**: {reason}")
        return "\n".join(lines) + "\n"
