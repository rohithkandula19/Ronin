"""Command-line entry point for the eval suite.

Wired as ``csk-eval`` via ``[project.scripts]``. Subcommands:

    csk-eval run <dataset.jsonl> --judge <model> --target <model> [--out report.html]
    csk-eval drift <baseline.json> <candidate.json> [--threshold 0.5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dataset import GoldenDataset
from .drift import detect_drift
from .report import render_html_report
from .suite import DEFAULT_JUDGE_MODEL, DEFAULT_TARGET_MODEL, EvalSuite
from .types import RunReport, Rubric


def _cmd_run(args: argparse.Namespace) -> int:
    dataset = GoldenDataset.from_jsonl(args.dataset)
    rubric = Rubric(criteria=args.criteria.split(","))
    suite = EvalSuite(
        rubric=rubric,
        target_model=args.target,
        judge_model=args.judge,
        label=args.label,
    )
    report = suite.run(dataset)
    Path(args.json_out).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if args.out:
        render_html_report(report, args.out)
    print(f"Ran {len(dataset)} cases. Summary: {report.summary}")
    print(f"JSON: {args.json_out}" + (f"  HTML: {args.out}" if args.out else ""))
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    baseline = RunReport.model_validate_json(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = RunReport.model_validate_json(Path(args.candidate).read_text(encoding="utf-8"))
    drift = detect_drift(baseline, candidate, threshold=args.threshold)
    print(json.dumps(drift.model_dump(), indent=2))
    return 1 if drift.has_regression else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="csk-eval", description="LLM-as-a-judge eval suite.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run a golden dataset against a target model.")
    p_run.add_argument("dataset", help="Path to JSONL dataset.")
    p_run.add_argument("--target", default=DEFAULT_TARGET_MODEL)
    p_run.add_argument("--judge", default=DEFAULT_JUDGE_MODEL)
    p_run.add_argument(
        "--criteria",
        default="task_success,faithfulness,helpfulness,safety",
        help="Comma-separated rubric criteria.",
    )
    p_run.add_argument("--label", default=None)
    p_run.add_argument("--json-out", dest="json_out", default="eval-report.json")
    p_run.add_argument("--out", default=None, help="Optional HTML report path.")
    p_run.set_defaults(func=_cmd_run)

    p_drift = sub.add_parser("drift", help="Compare two run reports.")
    p_drift.add_argument("baseline")
    p_drift.add_argument("candidate")
    p_drift.add_argument("--threshold", type=float, default=0.5)
    p_drift.set_defaults(func=_cmd_drift)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
