"""Dataset quality gates: dedup, near-dup, length, contamination, balance."""

from __future__ import annotations

from dataclasses import dataclass, field

from .provenance import DatasetItem


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _shingles(text: str, n: int = 4) -> set[str]:
    words = _norm(text).split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class QualityReport:
    total: int
    exact_duplicates: list[str] = field(default_factory=list)  # ids dropped
    near_duplicates: list[tuple[str, str, float]] = field(default_factory=list)
    empty_outputs: list[str] = field(default_factory=list)
    too_short: list[str] = field(default_factory=list)
    contamination: list[tuple[str, str]] = field(default_factory=list)  # (train_id, test_id)
    balance: dict[str, int] = field(default_factory=dict)  # industry -> count

    def markdown(self) -> str:
        lines = ["# Dataset quality report", "", f"- items scored: {self.total}"]
        lines.append(f"- exact duplicates removed: {len(self.exact_duplicates)}")
        lines.append(f"- near-duplicate pairs (>=0.9): {len(self.near_duplicates)}")
        lines.append(f"- empty outputs: {len(self.empty_outputs)}")
        lines.append(f"- too-short outputs: {len(self.too_short)}")
        lines.append(f"- train/test contamination pairs: {len(self.contamination)}")
        if self.balance:
            lines += ["", "## Balance by industry", ""]
            for industry, count in sorted(self.balance.items()):
                lines.append(f"- {industry or '(unset)'}: {count}")
        return "\n".join(lines) + "\n"


def analyze_quality(
    items: list[DatasetItem],
    *,
    min_output_chars: int = 12,
    near_dup_threshold: float = 0.9,
) -> QualityReport:
    report = QualityReport(total=len(items))
    seen_exact: dict[tuple[str, str], str] = {}
    for item in items:
        key = (_norm(item.instruction), _norm(item.output))
        if key in seen_exact:
            report.exact_duplicates.append(item.id)
            continue
        seen_exact[key] = item.id
        if not item.output.strip():
            report.empty_outputs.append(item.id)
        elif len(item.output.strip()) < min_output_chars:
            report.too_short.append(item.id)
        report.balance[item.industry] = report.balance.get(item.industry, 0) + 1

    # near-duplicates among the non-exact-dup survivors
    survivors = [i for i in items if i.id not in set(report.exact_duplicates)]
    shingle_cache = {i.id: _shingles(i.instruction + " " + i.output) for i in survivors}
    for a in range(len(survivors)):
        for b in range(a + 1, len(survivors)):
            sim = _jaccard(shingle_cache[survivors[a].id], shingle_cache[survivors[b].id])
            if sim >= near_dup_threshold:
                report.near_duplicates.append((survivors[a].id, survivors[b].id, round(sim, 3)))
    return report


def contamination_check(
    train: list[DatasetItem], test: list[DatasetItem], *, threshold: float = 0.9
) -> list[tuple[str, str]]:
    """Flag test items that overlap training items — a leakage guard."""
    train_shingles = {i.id: _shingles(i.instruction + " " + i.output) for i in train}
    hits: list[tuple[str, str]] = []
    for t in test:
        ts = _shingles(t.instruction + " " + t.output)
        for train_id, sh in train_shingles.items():
            if _jaccard(sh, ts) >= threshold:
                hits.append((train_id, t.id))
    return hits
