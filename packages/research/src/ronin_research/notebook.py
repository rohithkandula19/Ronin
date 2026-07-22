"""Research notebook: sources, claims, claim→source mapping, honest reports."""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SupportStrength(str, Enum):
    strong = "strong"
    moderate = "moderate"
    weak = "weak"


class ClaimStatus(str, Enum):
    supported = "supported"        # cites >=1 existing source
    inference = "inference"        # no source — model knowledge, labeled
    contradicted = "contradicted"  # cited sources disagree
    unsupported = "unsupported"    # claims support but its sources are gone


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: "src_" + uuid.uuid4().hex[:12])
    title: str = Field(min_length=1)
    url: str = ""            # may be empty (a file source); never auto-generated
    kind: str = "web"        # web | file | dataset
    published_date: str = ""  # ISO; "" = unknown, never guessed
    retrieved_date: str = ""  # ISO; separate from published_date
    quality: str = "unrated"  # unrated | low | medium | high
    excerpt: str = ""


class UnknownSourceError(ValueError):
    """A claim tried to cite a source the notebook does not hold. Fail closed."""


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: "clm_" + uuid.uuid4().hex[:12])
    text: str = Field(min_length=1)
    source_ids: tuple[str, ...] = ()
    dropped_source_ids: tuple[str, ...] = ()  # cited sources later deleted
    support: SupportStrength | None = None
    contradicted: bool = False


class Notebook:
    """A saved research project: sources collected/rejected + claims."""

    def __init__(self, *, question: str = "") -> None:
        self.question = question
        self._sources: dict[str, Source] = {}
        self._rejected: list[Source] = []
        self._claims: dict[str, Claim] = {}
        self.queries: list[str] = []

    # --------------------------------------------------------------- sources
    def add_source(self, source: Source) -> Source:
        self._sources[source.id] = source
        return source

    def reject_source(self, source: Source, reason: str = "") -> None:
        """Record a source that was looked at but not used (visible, not hidden)."""
        self._rejected.append(source)

    def delete_source(self, source_id: str) -> bool:
        """Delete a source and drop it from every claim that cited it."""
        if source_id not in self._sources:
            return False
        del self._sources[source_id]
        for cid, claim in list(self._claims.items()):
            if source_id in claim.source_ids:
                remaining = tuple(s for s in claim.source_ids if s != source_id)
                dropped = claim.dropped_source_ids + (source_id,)
                self._claims[cid] = claim.model_copy(
                    update={"source_ids": remaining, "dropped_source_ids": dropped}
                )
        return True

    def sources(self) -> list[Source]:
        return sorted(self._sources.values(), key=lambda s: s.id)

    def rejected_sources(self) -> list[Source]:
        return list(self._rejected)

    # ---------------------------------------------------------------- claims
    def add_claim(
        self,
        text: str,
        *,
        source_ids: tuple[str, ...] = (),
        support: SupportStrength | None = None,
        contradicted: bool = False,
    ) -> Claim:
        """Add a claim. Every cited source MUST already exist — no fabrication."""
        unknown = [sid for sid in source_ids if sid not in self._sources]
        if unknown:
            raise UnknownSourceError(
                f"claim cites sources not in the notebook: {unknown}"
            )
        claim = Claim(text=text, source_ids=source_ids, support=support,
                      contradicted=contradicted)
        self._claims[claim.id] = claim
        return claim

    def claims(self) -> list[Claim]:
        return sorted(self._claims.values(), key=lambda c: c.id)

    def status_of(self, claim: Claim) -> ClaimStatus:
        live = [sid for sid in claim.source_ids if sid in self._sources]
        if claim.contradicted:
            return ClaimStatus.contradicted
        if live:
            return ClaimStatus.supported
        if claim.dropped_source_ids:
            # once cited sources, but they were all deleted
            return ClaimStatus.unsupported
        return ClaimStatus.inference

    # ---------------------------------------------------------------- report
    def report(self) -> str:
        """Markdown report that is honest about sourcing."""
        lines = [f"# Research: {self.question or '(untitled)'}", ""]
        if not self._sources:
            lines += [
                "> No sources were retrieved. The findings below rest on model "
                "knowledge, not on current sources, and should be verified.",
                "",
            ]
        lines.append("## Claims")
        lines.append("")
        for claim in self.claims():
            status = self.status_of(claim)
            cites = ", ".join(claim.source_ids) if claim.source_ids else "—"
            tag = {
                ClaimStatus.supported: "SUPPORTED",
                ClaimStatus.inference: "INFERENCE (model knowledge, unsourced)",
                ClaimStatus.contradicted: "CONTRADICTED (sources disagree)",
                ClaimStatus.unsupported: "UNSUPPORTED (cited sources removed)",
            }[status]
            lines.append(f"- [{tag}] {claim.text}  · sources: {cites}")
        if self._rejected:
            lines += ["", "## Rejected sources (shown, not hidden)", ""]
            for s in self._rejected:
                lines.append(f"- {s.title}")
        lines += ["", "## Sources", ""]
        for s in self.sources():
            date = s.published_date or "date unknown"
            lines.append(f"- {s.id}: {s.title} ({date}; retrieved {s.retrieved_date or 'n/a'})")
        return "\n".join(lines) + "\n"

    def coverage(self) -> dict:
        """Citation coverage stats for observability."""
        claims = self.claims()
        supported = sum(1 for c in claims if self.status_of(c) is ClaimStatus.supported)
        return {
            "claims": len(claims),
            "supported": supported,
            "inference": sum(1 for c in claims if self.status_of(c) is ClaimStatus.inference),
            "contradicted": sum(1 for c in claims if self.status_of(c) is ClaimStatus.contradicted),
            "unsupported": sum(1 for c in claims if self.status_of(c) is ClaimStatus.unsupported),
            "sources": len(self._sources),
            "coverage": (supported / len(claims)) if claims else 0.0,
        }
