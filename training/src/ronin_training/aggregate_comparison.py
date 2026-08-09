"""Aggregate protocol-eval reports into one honest v3/v3b comparison document.

Reads the per-checkpoint reports that ``eval_runner.write_report`` produced under
``training/reports/`` (``eval91_*.md`` legacy set, ``eval91f_*.md`` frozen set),
parses their real numbers, and writes ``v3_finetune_comparison.md``.

Honesty invariants, same spirit as the rest of this package:

- Every score in the output is parsed from a report file on disk. If a report is
  absent the row says MISSING — the aggregator never estimates, extrapolates, or
  fills in a number.
- Verdicts (beats baseline? category improved? gates regressed?) are computed only
  between reports with the same case count, and only frozen-set (``eval91f_``)
  reports count as evidence against the frozen baseline.
- With zero v3b reports present, the output plainly declares the evidence phase
  incomplete and points at the runbook instead of guessing an outcome.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

# The categories the v3 effort explicitly targets, in the order they are reported.
FOCUS_CATEGORIES = (
    "valid_tool_json",
    "grounding",
    "multi_turn_stability",
    "gate_respect",
    "recovery",
    "final_answer",
    "schema_compliance",
)

_FAMILY_ORDER = {"base": 0, "v1": 1, "v2": 2, "v3": 3, "v3b": 4}
_FINAL_SENTINEL = 10**9  # sorts a family's "final" adapter after its numbered iters

_CASES_RE = re.compile(r"-\s*cases:\s*\*\*(\d+)\*\*")
_PASSED_RE = re.compile(r"-\s*passed:\s*\*\*(\d+)\*\*")
_CATEGORY_ROW_RE = re.compile(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
_FAILURE_RE = re.compile(r"^-\s*\*\*(.+?)\*\*\s*\((.+?)\):\s*(.*)$")
_STEM_RE = re.compile(r"^eval91(f?)_(.+)$")
_ITER_RE = re.compile(r"^(base|v\d+b?)(?:_iter(\d+)|_(final))?$")


@dataclass
class ReportScore:
    """One parsed eval report."""

    path: Path
    family: str  # base / v1 / v2 / v3 / v3b / other
    label: str  # e.g. "v3b iter-300"
    frozen: bool  # filename says eval91f_ (frozen snapshot run)
    iteration: int | None
    cases: int
    passed: int
    categories: dict[str, tuple[int, int]] = field(default_factory=dict)
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.cases if self.cases else 0.0

    def category(self, name: str) -> tuple[int, int] | None:
        return self.categories.get(name)

    def sort_key(self) -> tuple[int, int, str]:
        fam = _FAMILY_ORDER.get(self.family, 9)
        it = self.iteration if self.iteration is not None else _FINAL_SENTINEL
        return (fam, it, self.label)


def parse_report(path: Path) -> ReportScore | None:
    """Parse one write_report()-format markdown file; None if it isn't one."""
    text = path.read_text(encoding="utf-8")
    cases_m = _CASES_RE.search(text)
    passed_m = _PASSED_RE.search(text)
    if not cases_m or not passed_m:
        return None

    categories: dict[str, tuple[int, int]] = {}
    failures: list[tuple[str, str, str]] = []
    in_failures = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_failures = line.strip().lower() == "## failures"
            continue
        row = _CATEGORY_ROW_RE.match(line)
        if row and row.group(1) != "category":
            categories[row.group(1)] = (int(row.group(2)), int(row.group(3)))
            continue
        if in_failures:
            fail = _FAILURE_RE.match(line.strip())
            if fail:
                failures.append((fail.group(1), fail.group(2), fail.group(3)))

    family, label, frozen, iteration = _identity_from_filename(path)
    return ReportScore(
        path=path,
        family=family,
        label=label,
        frozen=frozen,
        iteration=iteration,
        cases=int(cases_m.group(1)),
        passed=int(passed_m.group(1)),
        categories=categories,
        failures=failures,
    )


def _identity_from_filename(path: Path) -> tuple[str, str, bool, int | None]:
    stem_m = _STEM_RE.match(path.stem)
    if not stem_m:
        return ("other", path.stem, False, None)
    frozen = stem_m.group(1) == "f"
    rest = stem_m.group(2)
    ident = _ITER_RE.match(rest)
    if not ident:
        return ("other", rest, frozen, None)
    family = ident.group(1)
    if ident.group(2) is not None:
        iteration: int | None = int(ident.group(2))
        label = f"{family} iter-{iteration}"
    elif ident.group(3) is not None:
        iteration = None
        label = f"{family} final"
    else:
        iteration = None
        label = family
    return (family, label, frozen, iteration)


def discover_reports(reports_dir: Path) -> list[ReportScore]:
    scores: list[ReportScore] = []
    for path in sorted(reports_dir.glob("eval91*.md")):
        parsed = parse_report(path)
        if parsed is not None:
            scores.append(parsed)
    scores.sort(key=ReportScore.sort_key)
    return scores


def pick_baseline(scores: list[ReportScore]) -> ReportScore | None:
    """v2 iter-150 is the score to beat; prefer its frozen-set rerun."""
    v2_150 = [s for s in scores if s.family == "v2" and s.iteration == 150]
    frozen = [s for s in v2_150 if s.frozen]
    if frozen:
        return frozen[0]
    return v2_150[0] if v2_150 else None


@dataclass
class Verdict:
    baseline: ReportScore
    candidates: list[ReportScore]  # frozen v3/v3b reports comparable to baseline
    excluded: list[tuple[ReportScore, str]]  # (report, why it can't be evidence)

    @property
    def best_candidate(self) -> ReportScore | None:
        return max(self.candidates, key=lambda s: s.passed) if self.candidates else None

    def beats_baseline(self) -> bool | None:
        best = self.best_candidate
        if best is None:
            return None
        return best.passed > self.baseline.passed

    def category_improved(self, name: str) -> tuple[bool | None, str]:
        base = self.baseline.category(name)
        if base is None:
            return (None, f"baseline report has no `{name}` row")
        best_seen = max(
            ((s.category(name) or (0, 0))[0], s.label) for s in self.candidates
        ) if self.candidates else None
        if best_seen is None:
            return (None, "no comparable v3/v3b reports")
        improved = best_seen[0] > base[0]
        return (
            improved,
            f"baseline {base[0]}/{base[1]} → best v3/v3b {best_seen[0]} ({best_seen[1]})",
        )

    def gates_regressed(self) -> tuple[bool | None, str]:
        base = self.baseline.category("gate_respect")
        best = self.best_candidate
        if base is None or best is None:
            return (None, "not comparable")
        got = best.category("gate_respect")
        if got is None:
            return (None, f"{best.label} report has no gate_respect row")
        return (got[0] < base[0], f"baseline {base[0]}/{base[1]} → {best.label} {got[0]}/{got[1]}")


def build_verdict(scores: list[ReportScore], baseline: ReportScore) -> Verdict:
    candidates: list[ReportScore] = []
    excluded: list[tuple[ReportScore, str]] = []
    for s in scores:
        if s.family not in ("v3", "v3b"):
            continue
        if not s.frozen:
            excluded.append((s, "not a frozen-set run (filename lacks `eval91f_`)"))
        elif s.cases != baseline.cases:
            excluded.append((s, f"case count {s.cases} != baseline {baseline.cases}"))
        else:
            candidates.append(s)
    return Verdict(baseline=baseline, candidates=candidates, excluded=excluded)


EXPECTED_V3B = (
    "eval91f_v3b_iter0300.md",
    "eval91f_v3b_iter0450.md",
    "eval91f_v3b_iter0600.md",
    "eval91f_v3b_iter0750.md",
    "eval91f_v3b_iter0900.md",
    "eval91f_v3b_iter1050.md",
    "eval91f_v3b_final.md",
)


def _yn(value: bool | None) -> str:
    if value is None:
        return "UNKNOWN (evidence missing)"
    return "YES" if value else "NO"


def render(
    scores: list[ReportScore],
    reports_dir: Path,
    *,
    model: str,
    frozen_evals: Path,
) -> str:
    lines: list[str] = []
    add = lines.append
    add("# v3 / v3b fine-tune comparison — frozen 91-case runtime-parity set")
    add("")
    add("Generated by `python -m ronin_training.aggregate_comparison`. Every number")
    add("below is parsed from a real eval report in `training/reports/`; nothing is")
    add("projected or estimated. Missing reports are named as missing, never filled in.")
    add("")
    add(f"- base model: `{model}`")
    if frozen_evals.exists():
        n = sum(1 for line in frozen_evals.read_text(encoding="utf-8").splitlines() if line.strip())
        add(f"- frozen eval set: `{frozen_evals.as_posix()}` ({n} cases)")
    else:
        add(f"- frozen eval set: `{frozen_evals.as_posix()}` — **NOT FOUND in this checkout**")

    baseline = pick_baseline(scores)
    if baseline is not None:
        note = "" if baseline.frozen else " *(non-frozen filename — rerun on the frozen set for exact parity)*"
        add(f"- baseline to beat: **v2 iter-150 — {baseline.passed}/{baseline.cases}** "
            f"(`{baseline.path.name}`){note}")
    else:
        add("- baseline to beat: **v2 iter-150 report NOT FOUND** — no verdict is possible")
    add("")

    add("## Scores")
    add("")
    header = "| checkpoint | report | total | pass % | " + " | ".join(FOCUS_CATEGORIES) + " |"
    add(header)
    add("|" + "---|" * (4 + len(FOCUS_CATEGORIES)))
    for s in scores:
        cells = [s.label + ("" if s.frozen else " *(non-frozen)*"), f"`{s.path.name}`",
                 f"{s.passed}/{s.cases}", f"{s.pass_rate * 100:.1f}%"]
        for cat in FOCUS_CATEGORIES:
            got = s.category(cat)
            cells.append(f"{got[0]}/{got[1]}" if got else "—")
        add("| " + " | ".join(cells) + " |")
    add("")

    missing = [name for name in EXPECTED_V3B if not (reports_dir / name).exists()]
    if missing:
        add("## Missing evidence")
        add("")
        add("The v3b sweep has not been fully scored. Absent reports:")
        add("")
        for name in missing:
            add(f"- `{name}` — MISSING")
        add("")
        add("Run `training/scripts/finish_v3b_evidence.sh` on the Apple Silicon machine")
        add("that holds `training/adapters/ronin_1.5b_v3b` to produce them. No verdict")
        add("below treats a missing report as a score.")
        add("")

    add("## Verdict")
    add("")
    if baseline is None:
        add("No v2 iter-150 report found — nothing to compare against. Re-run the v2")
        add("baseline on the frozen set first.")
        return "\n".join(lines) + "\n"

    verdict = build_verdict(scores, baseline)
    best = verdict.best_candidate
    overall = max(scores, key=lambda s: s.passed, default=None)
    if overall is not None:
        add(f"- best checkpoint overall (any family): **{overall.label}** "
            f"({overall.passed}/{overall.cases})")
    if best is not None:
        add(f"- best v3/v3b checkpoint (frozen set): **{best.label}** "
            f"({best.passed}/{best.cases})")
    else:
        add("- best v3/v3b checkpoint (frozen set): **none scored yet**")

    beats = verdict.beats_baseline()
    add(f"- beats v2 iter-150 ({baseline.passed}/{baseline.cases})? **{_yn(beats)}**")
    for cat in ("valid_tool_json", "grounding", "multi_turn_stability"):
        improved, detail = verdict.category_improved(cat)
        add(f"- `{cat}` improved? **{_yn(improved)}** — {detail}")
    regressed, detail = verdict.gates_regressed()
    add(f"- `gate_respect` regressed? **{_yn(regressed)}** — {detail}")
    if verdict.excluded:
        add("")
        add("Excluded from the verdict (listed, not silently dropped):")
        for s, why in verdict.excluded:
            add(f"- `{s.path.name}`: {why}")
    add("")

    add("## Recommendation")
    add("")
    if beats is None:
        add("**Evidence phase incomplete.** Score the v3b checkpoints on the frozen set")
        add("before recommending any adapter change. Until then, **v2 iter-150 remains")
        add("the shipped adapter** — its score is the only frozen-set evidence on file.")
    elif beats and best is not None:
        add(f"**Adopt {best.label}** as the new best adapter "
            f"({best.passed}/{best.cases} vs baseline {baseline.passed}/{baseline.cases}).")
        add("Point `RONIN_ADAPTER` at its staged checkpoint directory, update")
        add("`training/README.md`, and record the exact reproduction command (seed,")
        add("iters, corpus commit) in this report before opening the PR.")
    else:
        add("**Keep v2 iter-150.** No v3/v3b checkpoint scored on the frozen set beats")
        add(f"{baseline.passed}/{baseline.cases}. The v3 corpus expansion did not improve")
        add("frozen runtime-parity behavior. Likely failure modes to investigate (these")
        add("are hypotheses to test, not measured findings):")
        add("")
        add("- the model still narrates tool use instead of emitting an executable,")
        add("  wrapper-enclosed `<tool_call>` block the runtime parser accepts;")
        add("- the training rows' call format may not match the runtime parser's exact")
        add("  dialect strongly enough for the format to become the dominant behavior;")
        add("- `valid_tool_json` remains the blocking metric — until it moves off 0,")
        add("  category gains elsewhere cannot translate into usable autonomy.")
        add("")
        add("Do not buy more iterations on this corpus; change the training design")
        add("instead (see the next-design section of the evidence status doc).")
    add("")

    if best is not None and best.failures:
        add(f"## Appendix — failures of {best.label} ({len(best.failures)} cases)")
        add("")
        for name, cat, why in best.failures:
            add(f"- **{name}** ({cat}): {why}")
        add("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reports-dir", default="training/reports")
    ap.add_argument("--out", default="training/reports/v3_finetune_comparison.md")
    ap.add_argument("--model", default="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit")
    ap.add_argument("--frozen-evals", default="training/eval_snapshots/eval91_frozen.jsonl")
    a = ap.parse_args(argv)

    reports_dir = Path(a.reports_dir)
    scores = discover_reports(reports_dir)
    doc = render(scores, reports_dir, model=a.model, frozen_evals=Path(a.frozen_evals))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out} ({len(scores)} reports aggregated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
