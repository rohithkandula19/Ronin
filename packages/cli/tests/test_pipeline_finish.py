"""Tests for the Wave 5 gated commit/PR finish."""
from __future__ import annotations

import io
import subprocess
from pathlib import Path

from rich.console import Console

from ronin_cli.config import RoninConfig
from ronin_cli.pipeline import plan_pipeline
from ronin_cli.pipeline_finish import commit_gate, finish_pipeline


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=100), buf


def _verifier_state(verdict: str):
    st = plan_pipeline("x", ["architect", "implementer", "verifier"])
    for s in st.stages:
        s.status = "completed"
    st.stages[-1].artifact = {"kind": "verification_report", "final_verdict": verdict}
    st.verdict = verdict
    return st


# --- commit_gate (pure) ------------------------------------------------------

def test_gate_allows_on_passed_verdict() -> None:
    allowed, reason = commit_gate(_verifier_state("passed"))
    assert allowed is True and "passed" in reason


def test_gate_blocks_on_failed_unknown_blocked() -> None:
    for verdict in ("failed", "unknown", "blocked"):
        allowed, reason = commit_gate(_verifier_state(verdict))
        assert allowed is False
        assert verdict in reason
        # force overrides (the CLI turns this into an explicit confirmation)
        assert commit_gate(_verifier_state(verdict), force=True)[0] is True


def test_gate_blocks_when_pipeline_stopped() -> None:
    st = plan_pipeline("x", ["architect", "implementer"])
    st.stages[0].status = "completed"
    st.stages[1].status = "failed"
    allowed, reason = commit_gate(st)
    assert allowed is False and "stopped at implementer" in reason


def test_gate_allows_completed_without_verifier() -> None:
    st = plan_pipeline("x", ["architect", "implementer"])
    for s in st.stages:
        s.status = "completed"
    allowed, reason = commit_gate(st)
    assert allowed is True and "no verifier" in reason


def test_gate_blocks_dry_run() -> None:
    st = plan_pipeline("x", ["architect"], dry_run=True)
    assert commit_gate(st)[0] is False


# --- finish_pipeline (IO, real temp git repo) --------------------------------

def _init_repo(tmp_path: Path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"),
                 ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
                   check=True, capture_output=True)


def _commit_count(tmp_path: Path) -> int:
    r = subprocess.run(["git", "-C", str(tmp_path), "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True, check=True)
    return int(r.stdout.strip())


def test_dry_run_finish_makes_no_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")  # dirty
    before = _commit_count(tmp_path)
    console, buf = _console()
    res = finish_pipeline(console, tmp_path, RoninConfig(provider="cerebras"),
                          _verifier_state("passed"), do_commit=True, dry_run=True)
    assert res["would_commit"] is True and res["committed"] is False
    assert _commit_count(tmp_path) == before          # nothing committed
    assert "dry run" in buf.getvalue()


def test_failed_verifier_blocks_commit_without_confirmation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")
    before = _commit_count(tmp_path)
    # console=None → no way to confirm → must NOT commit on a failed verdict
    res = finish_pipeline(None, tmp_path, RoninConfig(provider="cerebras"),
                          _verifier_state("failed"), do_commit=True)
    assert res["committed"] is False
    assert res["skipped_reason"] and "failed" in res["skipped_reason"]
    assert _commit_count(tmp_path) == before


def test_finish_noop_when_no_flags(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    res = finish_pipeline(None, tmp_path, RoninConfig(provider="cerebras"),
                          _verifier_state("passed"))
    assert res == {"committed": False, "pr_opened": False, "skipped_reason": None,
                   "would_commit": False, "would_pr": False}


def test_finish_skips_outside_git_repo(tmp_path: Path) -> None:
    res = finish_pipeline(None, tmp_path, RoninConfig(provider="cerebras"),
                          _verifier_state("passed"), do_commit=True)
    assert res["skipped_reason"] == "not a git repository"


# --- Wave 6: commit gate keyed on the combined final verdict -----------------

def test_gate_uses_final_verdict_when_present() -> None:
    st = _verifier_state("passed")          # verifier stage said passed…
    st.final_verdict = "failed"             # …but the combined verdict failed (e.g. verify)
    allowed, reason = commit_gate(st)
    assert allowed is False and "failed" in reason
    # and a passing combined verdict allows it
    st.final_verdict = "passed"
    assert commit_gate(st)[0] is True
