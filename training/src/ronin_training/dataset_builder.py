"""Turn Ronin's volumes into validated MLX-format JSONL + a protocol eval set.

Deterministic (spec Page 16: "should not rely on an LLM to generate the final
JSONL"). Flow: parse volumes → validate every row against the schema + real tool
registry → drop exact duplicates → stratified split by family → write
train/valid/test.jsonl (MLX form: only `messages`/`tools`) + evals + a report.

    python -m ronin_training.dataset_builder --volumes docs/volumes --out training/data/generated
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .coverage import coverage_gaps, coverage_table
from .split_dataset import split_by_family
from .validators import validate_eval, validate_example
from .volume_parser import parse_dir

# Fields MLX-LM does not consume — stripped from the exported rows (spec Page 16).
_MLX_KEEP = ("messages", "tools")


def _mlx_row(ex: dict) -> dict:
    return {k: ex[k] for k in _MLX_KEEP if k in ex}


def _public(obj: dict) -> dict:
    """Drop internal `_`-prefixed bookkeeping keys before writing to disk."""
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def _content_hash(ex: dict) -> str:
    return hashlib.sha256(
        json.dumps(_mlx_row(ex), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build(volumes_dir: str | Path, out_dir: str | Path,
          *, strict: bool = True) -> dict:
    """Build the dataset. Returns a report dict. Raises if `strict` and any row is
    invalid — a bad training row is never silently included."""
    parsed = parse_dir(Path(volumes_dir))

    valid: list[dict] = []
    rejected: list[str] = []
    for ex in parsed.sft:
        errs = validate_example(ex)
        if errs:
            rejected.append(f"{ex.get('id', ex.get('_source_file', '?'))}: {'; '.join(errs)}")
        else:
            valid.append(ex)

    valid_evals: list[dict] = []
    for ec in parsed.evals:
        errs = validate_eval(ec)
        if errs:
            rejected.append(f"eval {ec.get('eval_id', '?')}: {'; '.join(errs)}")
        else:
            valid_evals.append(ec)

    problems = parsed.errors + rejected
    if strict and problems:
        raise ValueError(
            f"dataset build refused: {len(problems)} invalid item(s):\n  "
            + "\n  ".join(problems[:30])
        )

    # Exact-dedup by MLX content (keep first occurrence, deterministic order).
    seen: set[str] = set()
    deduped: list[dict] = []
    dupes = 0
    for ex in valid:
        h = _content_hash(ex)
        if h in seen:
            dupes += 1
            continue
        seen.add(h)
        deduped.append(ex)

    train, va, te = split_by_family(deduped)
    out = Path(out_dir)
    _write_jsonl(out / "train.jsonl", [_mlx_row(x) for x in train])
    _write_jsonl(out / "valid.jsonl", [_mlx_row(x) for x in va])
    _write_jsonl(out / "test.jsonl", [_mlx_row(x) for x in te])
    _write_jsonl(out.parent / "evals" / "ronin_protocol_eval.jsonl",
                 [_public(ec) for ec in valid_evals])

    fam_counts: dict[str, int] = {}
    for ex in deduped:
        fam_counts[ex.get("family", "_")] = fam_counts.get(ex.get("family", "_"), 0) + 1

    gaps = coverage_gaps(fam_counts)

    report = {
        "total_parsed": len(parsed.sft),
        "valid": len(valid),
        "rejected": len(rejected),
        "duplicates_removed": dupes,
        "unique": len(deduped),
        "train": len(train), "valid_split": len(va), "test": len(te),
        "evals": len(valid_evals),
        "families": dict(sorted(fam_counts.items())),
        "coverage_table": coverage_table(fam_counts),
        "coverage_gaps": gaps,
        "problems": problems,
    }
    _write_report(out.parent.parent / "reports" / "dataset_report.md", report)
    return report


def _write_report(path: Path, r: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Dataset Report", "",
        f"- parsed SFT rows: **{r['total_parsed']}**",
        f"- valid: **{r['valid']}**  · rejected: **{r['rejected']}**  · duplicates removed: {r['duplicates_removed']}",
        f"- unique rows: **{r['unique']}**  → train {r['train']} / valid {r['valid_split']} / test {r['test']}",
        f"- eval cases: **{r['evals']}**", "",
        "## Rows by family", "",
        "| family | count |", "|---|---|",
    ]
    lines += [f"| {fam} | {n} |" for fam, n in r["families"].items()]

    if r.get("coverage_table"):
        lines += ["", "## Coverage vs floor", "",
                  "| tier | family | have | floor | met |", "|---|---|---|---|---|"]
        for tier, fam, have, floor, met in r["coverage_table"]:
            lines.append(f"| {tier} | {fam} | {have} | {floor} | {'✓' if met else '✗'} |")
    gaps = r.get("coverage_gaps") or []
    lines += ["", f"**Coverage gaps: {len(gaps)}**"]
    lines += [f"- {g}" for g in gaps] if gaps else ["- none — every declared floor is met"]

    if r["problems"]:
        lines += ["", "## Rejected (not included)", ""]
        lines += [f"- {p}" for p in r["problems"][:50]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build Ronin MLX JSONL from volumes.")
    ap.add_argument("--volumes", default="docs/volumes")
    ap.add_argument("--out", default="training/data/generated")
    ap.add_argument("--allow-invalid", action="store_true",
                    help="do not raise on invalid rows (they are still excluded)")
    a = ap.parse_args(argv)
    rep = build(a.volumes, a.out, strict=not a.allow_invalid)
    _skip = {"problems", "coverage_table"}
    print(json.dumps({k: v for k, v in rep.items() if k not in _skip}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
