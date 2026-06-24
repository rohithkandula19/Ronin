"""LLM-as-a-judge eval suite for Claude agents.

Exports:
- ``Rubric`` — scoring criteria + scale + judge instructions.
- ``EvalCase`` / ``GoldenDataset`` — JSONL-backed test cases.
- ``EvalSuite`` — runs a target system and judges its outputs.
- ``RunReport`` — typed result of a run; serializable to JSON.
- ``render_html_report`` — self-contained HTML report.
- ``detect_drift`` — compare two runs, flag regressions.

SWE-bench (execution-based, not judged):
- ``SWEBenchTask`` / ``SWEBenchDataset`` — JSONL-backed coding tasks.
- ``SWEBenchHarness`` — runs an agent, scores patches by running tests.
- ``SWEBenchReport`` — resolved-rate result; serializable to JSON.
- ``is_resolved`` — the canonical FAIL_TO_PASS/PASS_TO_PASS resolution rule.
- ``make_local_git_evaluator`` — reference executor over a local checkout.
"""
from .dataset import GoldenDataset
from .drift import DriftReport, detect_drift
from .judge import judge_one
from .report import render_html_report
from .suite import EvalSuite
from .swebench import (
    SWEBenchDataset,
    SWEBenchHarness,
    SWEBenchReport,
    SWEBenchResult,
    SWEBenchTask,
    TaskEvaluation,
    is_resolved,
    make_local_git_evaluator,
    oracle_runner,
    render_swebench_markdown,
)
from .types import EvalCase, EvalScore, RunReport, Rubric

__all__ = [
    "DriftReport",
    "EvalCase",
    "EvalScore",
    "EvalSuite",
    "GoldenDataset",
    "RunReport",
    "Rubric",
    "SWEBenchDataset",
    "SWEBenchHarness",
    "SWEBenchReport",
    "SWEBenchResult",
    "SWEBenchTask",
    "TaskEvaluation",
    "detect_drift",
    "is_resolved",
    "judge_one",
    "make_local_git_evaluator",
    "oracle_runner",
    "render_html_report",
    "render_swebench_markdown",
]
