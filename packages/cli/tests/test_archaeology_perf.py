"""Tests for archaeology (git-history parsing) + perf (benchmark stats)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.archaeology import file_history, history_brief, parse_log
from ronin_cli.perf import benchmark, stats

_US = "\x1f"


def test_parse_log() -> None:
    line = _US.join(["fullsha", "abc123", "Alice", "2026-01-01", "feat: add thing"])
    rows = parse_log(line + "\n" + _US.join(["s2", "def456", "Bob", "2026-02-02", "fix: bug"]))
    assert len(rows) == 2
    assert rows[0]["short"] == "abc123" and rows[0]["author"] == "Alice"
    assert rows[1]["subject"] == "fix: bug"


def test_parse_log_ignores_malformed() -> None:
    assert parse_log("not a real log line") == []


def _git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True)


def test_file_history_real(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "feat: create a.py")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "fix: bump x")
    hist = file_history(tmp_path, "a.py")
    assert len(hist) == 2
    assert hist[0]["subject"] == "fix: bump x"        # newest first
    assert "feat: create a.py" in history_brief(hist)


# ---- perf stats ----

def test_stats_basic() -> None:
    s = stats([0.1, 0.2, 0.3])
    assert s["runs"] == 3 and s["min"] == 0.1 and s["max"] == 0.3
    assert abs(s["mean"] - 0.2) < 1e-9 and s["median"] == 0.2


def test_stats_even_count_median() -> None:
    s = stats([1.0, 2.0, 3.0, 4.0])
    assert s["median"] == 2.5


def test_stats_empty() -> None:
    assert stats([])["runs"] == 0


def test_benchmark_runs_a_command(tmp_path: Path) -> None:
    r = benchmark("true", runs=3, warmup=1, root=tmp_path)
    assert r["runs"] == 3 and r["failures"] == 0
    assert r["mean"] >= 0.0


def test_benchmark_counts_failures(tmp_path: Path) -> None:
    r = benchmark("false", runs=2, warmup=0, root=tmp_path)
    assert r["failures"] == 2          # `false` always exits non-zero
