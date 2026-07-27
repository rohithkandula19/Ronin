"""Repeatable local command benchmarking with structured reports.

``ronin perf`` remains a small, local-first timer. This module gives it a stable
report schema, deterministic executor injection for tests, and honest baseline
comparison without pretending that timings from different machines are directly
comparable.
"""
from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


REPORT_SCHEMA_VERSION = 1


def stats(durations: list[float]) -> dict:
    """min/mean/median/max/stdev over a list of run durations (seconds). Pure."""
    if not durations:
        return {"runs": 0, "min": 0.0, "mean": 0.0, "median": 0.0, "max": 0.0, "stdev": 0.0}
    s = sorted(durations)
    n = len(s)
    mean = sum(s) / n
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    var = sum((d - mean) ** 2 for d in s) / n
    return {"runs": n, "min": s[0], "mean": mean, "median": median, "max": s[-1],
            "stdev": var ** 0.5}


def percentile(durations: list[float], percent: float) -> float:
    """Nearest-rank percentile for a non-empty duration list. Pure."""
    if not durations:
        return 0.0
    rank = max(0, min(len(durations) - 1, math.ceil(len(durations) * percent) - 1))
    return sorted(durations)[rank]


@dataclass(frozen=True)
class CommandSample:
    duration: float
    exit_code: int | None
    timed_out: bool = False
    error: str | None = None
    output_tail: str = ""
    phase: str = "timed"

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "error": self.error,
            "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "CommandSample":
        if not isinstance(raw, dict):
            raise ValueError("benchmark sample must be an object")
        duration = raw.get("duration")
        if not isinstance(duration, (int, float)) or duration < 0:
            raise ValueError("benchmark sample duration must be non-negative")
        exit_code = raw.get("exit_code")
        if exit_code is not None and not isinstance(exit_code, int):
            raise ValueError("benchmark sample exit_code must be an integer or null")
        return cls(
            duration=float(duration),
            exit_code=exit_code,
            timed_out=bool(raw.get("timed_out", False)),
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
            output_tail=raw.get("output_tail") if isinstance(raw.get("output_tail"), str) else "",
            phase=raw.get("phase") if raw.get("phase") in {"warmup", "timed"} else "timed",
        )


@dataclass(frozen=True)
class BenchmarkReport:
    command: str
    root: str
    runs: int
    warmup: int
    timeout: int
    samples: tuple[CommandSample, ...]
    created_at: str
    environment: dict[str, str]
    schema_version: int = REPORT_SCHEMA_VERSION

    @property
    def timed_samples(self) -> tuple[CommandSample, ...]:
        return tuple(sample for sample in self.samples if sample.phase == "timed")

    @property
    def durations(self) -> list[float]:
        return [sample.duration for sample in self.timed_samples]

    @property
    def failures(self) -> int:
        return sum(not sample.succeeded for sample in self.timed_samples)

    @property
    def last_output(self) -> str:
        for sample in reversed(self.timed_samples):
            if sample.output_tail:
                return sample.output_tail
        return ""

    def summary(self) -> dict:
        result = stats(self.durations)
        result.update({
            "p95": percentile(self.durations, 0.95),
            "failures": self.failures,
            "successful_runs": sum(sample.succeeded for sample in self.timed_samples),
            "timed_out": sum(sample.timed_out for sample in self.timed_samples),
        })
        return result

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "command": self.command,
            "root": self.root,
            "runs": self.runs,
            "warmup": self.warmup,
            "timeout": self.timeout,
            "created_at": self.created_at,
            "environment": self.environment,
            "samples": [sample.to_dict() for sample in self.samples],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "BenchmarkReport":
        if not isinstance(raw, dict) or raw.get("schema_version") != REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported benchmark report schema")
        for field in ("command", "root", "created_at"):
            if not isinstance(raw.get(field), str):
                raise ValueError(f"benchmark report {field} must be a string")
        for field in ("runs", "warmup", "timeout"):
            if not isinstance(raw.get(field), int) or raw[field] < 0:
                raise ValueError(f"benchmark report {field} must be a non-negative integer")
        samples = raw.get("samples")
        if not isinstance(samples, list):
            raise ValueError("benchmark report samples must be a list")
        environment = raw.get("environment")
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()
        ):
            raise ValueError("benchmark report environment must be a string mapping")
        report = cls(
            command=raw["command"],
            root=raw["root"],
            runs=raw["runs"],
            warmup=raw["warmup"],
            timeout=raw["timeout"],
            samples=tuple(CommandSample.from_dict(sample) for sample in samples),
            created_at=raw["created_at"],
            environment=environment,
        )
        if len(report.timed_samples) != report.runs:
            raise ValueError("benchmark report timed sample count does not match runs")
        if len(report.samples) - len(report.timed_samples) != report.warmup:
            raise ValueError("benchmark report warmup sample count does not match warmup")
        return report


@dataclass(frozen=True)
class BenchmarkComparison:
    baseline_median: float
    candidate_median: float
    delta_percent: float | None
    baseline_failures: int
    candidate_failures: int
    status: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "baseline_median": self.baseline_median,
            "candidate_median": self.candidate_median,
            "delta_percent": self.delta_percent,
            "baseline_failures": self.baseline_failures,
            "candidate_failures": self.candidate_failures,
            "status": self.status,
            "reason": self.reason,
        }


Executor = Callable[[str, Path, int], CommandSample]


def run_benchmark(
    command: str,
    *,
    runs: int = 5,
    root: Path | str = ".",
    warmup: int = 1,
    timeout: int = 300,
    executor: Executor | None = None,
    created_at: str | None = None,
) -> BenchmarkReport:
    """Run a command and return a structured report.

    ``executor`` is injectable so the timing schema and comparison policy are
    tested without depending on scheduler noise or a real shell command.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")
    if timeout < 1:
        raise ValueError("timeout must be at least 1 second")
    cwd = Path(root).resolve()
    run_one = executor or _subprocess_executor
    samples: list[CommandSample] = []
    for index in range(warmup + runs):
        phase = "warmup" if index < warmup else "timed"
        sample = run_one(command, cwd, timeout)
        samples.append(replace(sample, phase=phase))
    return BenchmarkReport(
        command=command,
        root=str(cwd),
        runs=runs,
        warmup=warmup,
        timeout=timeout,
        samples=tuple(samples),
        created_at=created_at or datetime.now(UTC).isoformat(),
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    )


def _subprocess_executor(command: str, cwd: Path, timeout: int) -> CommandSample:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CommandSample(
            duration=time.perf_counter() - started,
            exit_code=None,
            timed_out=True,
            error=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return CommandSample(
            duration=time.perf_counter() - started,
            exit_code=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    output = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
    return CommandSample(
        duration=time.perf_counter() - started,
        exit_code=proc.returncode,
        output_tail=output,
    )


def compare_reports(
    baseline: BenchmarkReport,
    candidate: BenchmarkReport,
    *,
    max_regression_percent: float = 10.0,
) -> BenchmarkComparison:
    """Compare timed medians and failure counts without manufacturing precision."""
    if max_regression_percent < 0:
        raise ValueError("max_regression_percent must be non-negative")
    base = baseline.summary()
    current = candidate.summary()
    if not base["runs"] or not current["runs"]:
        return BenchmarkComparison(
            base["median"], current["median"], None, base["failures"], current["failures"],
            "insufficient", "one report has no completed timed samples",
        )
    if current["failures"] > base["failures"]:
        return BenchmarkComparison(
            base["median"], current["median"], None, base["failures"], current["failures"],
            "regressed", "candidate has more failed timed runs",
        )
    if base["median"] == 0:
        return BenchmarkComparison(
            base["median"], current["median"], None, base["failures"], current["failures"],
            "insufficient", "baseline median is zero; relative timing delta is undefined",
        )
    delta = (current["median"] - base["median"]) / base["median"] * 100
    if delta > max_regression_percent:
        status = "regressed"
        reason = f"median increased by {delta:.1f}% (limit {max_regression_percent:.1f}%)"
    elif delta < 0:
        status = "improved"
        reason = f"median decreased by {abs(delta):.1f}%"
    else:
        status = "unchanged"
        reason = f"median changed by {delta:.1f}% within the allowed regression"
    return BenchmarkComparison(
        base["median"], current["median"], delta, base["failures"], current["failures"], status, reason,
    )


def save_report(report: BenchmarkReport, path: Path | str) -> Path:
    """Atomically save a report so interrupted benchmarks never leave partial JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def load_report(path: Path | str) -> BenchmarkReport:
    """Load a report with schema validation rather than trusting arbitrary JSON."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read benchmark report: {exc}") from exc
    return BenchmarkReport.from_dict(raw)


def benchmark(command: str, *, runs: int = 5, root: Path | str = ".",
              warmup: int = 1, timeout: int = 300) -> dict:
    """Compatibility wrapper returning the original dict-shaped timing result."""
    report = run_benchmark(command, runs=runs, root=root, warmup=warmup, timeout=timeout)
    result = report.summary()
    result["last_output"] = report.last_output
    return result
