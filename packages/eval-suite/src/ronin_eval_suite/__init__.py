"""LLM-as-a-judge eval suite for Claude agents.

Exports:
- ``Rubric`` — scoring criteria + scale + judge instructions.
- ``EvalCase`` / ``GoldenDataset`` — JSONL-backed test cases.
- ``EvalSuite`` — runs a target system and judges its outputs.
- ``RunReport`` — typed result of a run; serializable to JSON.
- ``render_html_report`` — self-contained HTML report.
- ``detect_drift`` — compare two runs, flag regressions.
"""
from .dataset import GoldenDataset
from .drift import DriftReport, detect_drift
from .judge import judge_one
from .report import render_html_report
from .suite import EvalSuite
from .types import EvalCase, EvalScore, RunReport, Rubric

__all__ = [
    "DriftReport",
    "EvalCase",
    "EvalScore",
    "EvalSuite",
    "GoldenDataset",
    "RunReport",
    "Rubric",
    "detect_drift",
    "judge_one",
    "render_html_report",
]
