"""Command-line entry point for the eval suite.

Wired as ``csk-eval`` via ``[project.scripts]``. Subcommands:

    csk-eval run <dataset.jsonl> --judge <model> --target <model> [--out report.html]
    csk-eval drift <baseline.json> <candidate.json> [--threshold 0.5]
    csk-eval swebench <tasks.jsonl> --predictions <preds.jsonl> --repo-root <path>
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
from .swebench import (
    SWEBenchDataset,
    SWEBenchHarness,
    SWEBenchTask,
    make_local_git_evaluator,
)
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


def _load_predictions(path: str | Path) -> dict[str, str]:
    """Load a SWE-bench predictions JSONL into ``{instance_id: model_patch}``.

    Accepts the standard prediction keys: ``instance_id`` plus ``model_patch``
    (or ``patch`` / ``prediction``). Blank lines and ``#`` comments are skipped.
    """
    preds: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        patch = obj.get("model_patch") or obj.get("patch") or obj.get("prediction") or ""
        preds[obj["instance_id"]] = patch
    return preds


def _cmd_swebench(args: argparse.Namespace) -> int:
    dataset = SWEBenchDataset.from_jsonl(args.tasks)
    predictions = _load_predictions(args.predictions)

    def runner(task: SWEBenchTask) -> str:
        return predictions.get(task.instance_id, "")

    harness = SWEBenchHarness(
        patch_runner=runner,
        evaluator=make_local_git_evaluator(args.repo_root, timeout=args.timeout),
        model=args.model,
        label=args.label,
    )
    report = harness.run(dataset)
    Path(args.json_out).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    s = report.summary
    print(
        f"Resolved {int(s['resolved'])}/{int(s['total'])} "
        f"({s['resolved_rate']:.1%})  ·  patches: {int(s['patch_generated'])}  ·  "
        f"errored: {int(s['errored'])}"
    )
    print(f"JSON: {args.json_out}")
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

    p_swe = sub.add_parser("swebench", help="Score SWE-bench predictions by running tests.")
    p_swe.add_argument("tasks", help="Path to SWE-bench tasks JSONL.")
    p_swe.add_argument(
        "--predictions",
        required=True,
        help="JSONL of {instance_id, model_patch} agent predictions.",
    )
    p_swe.add_argument(
        "--repo-root",
        dest="repo_root",
        required=True,
        help="Local git checkout the patches apply to (mutated: reset --hard + clean).",
    )
    p_swe.add_argument("--model", default="")
    p_swe.add_argument("--label", default=None)
    p_swe.add_argument("--timeout", type=int, default=1800, help="Per-test-run timeout (s).")
    p_swe.add_argument("--json-out", dest="json_out", default="swebench-report.json")
    p_swe.set_defaults(func=_cmd_swebench)

    p_drift = sub.add_parser("drift", help="Compare two run reports.")
    p_drift.add_argument("baseline")
    p_drift.add_argument("candidate")
    p_drift.add_argument("--threshold", type=float, default=0.5)
    p_drift.set_defaults(func=_cmd_drift)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
