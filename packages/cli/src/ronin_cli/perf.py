"""perf — a tiny benchmark runner (hyperfine-lite) + optional AI analysis.

`ronin perf "<command>"` runs a command several times and reports timing stats
(min / mean / median / max), then ronin can suggest where the time goes. The
stats math is pure and unit-tested; the runs shell out.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path


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


def benchmark(command: str, *, runs: int = 5, root: Path | str = ".",
              warmup: int = 1, timeout: int = 300) -> dict:
    """Time ``command`` over ``runs`` runs (after ``warmup`` discarded runs).
    Returns the stats dict plus ``failures`` (non-zero exits) and ``last_output``."""
    cwd = str(Path(root).resolve())
    durations: list[float] = []
    failures = 0
    last_output = ""
    for i in range(warmup + runs):
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                                  text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            failures += 1
            continue
        dt = time.perf_counter() - t0
        if i >= warmup:                      # discard warmups
            durations.append(dt)
            if proc.returncode != 0:
                failures += 1
            last_output = (proc.stdout or "") + (proc.stderr or "")
    out = stats(durations)
    out["failures"] = failures
    out["last_output"] = last_output[-2000:]
    return out
