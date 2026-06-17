from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.git_stash import (
    StashEntry,
    fallback_label,
    format_stash_list,
    parse_stash_list,
    summarize_diff,
)
from ronin_cli.main import app

runner = CliRunner()


# ---------- pure core: parse_stash_list ----------

def test_parse_stash_list_basic() -> None:
    out = (
        "stash@{0}: On main: refactor: extract parser\n"
        "stash@{1}: WIP on feature/x: a1b2c3d add tests\n"
    )
    entries = parse_stash_list(out)
    assert len(entries) == 2
    assert entries[0] == StashEntry(ref="stash@{0}", index=0, branch="main",
                                    label="refactor: extract parser")
    assert entries[1].branch == "feature/x"
    assert entries[1].label == "a1b2c3d add tests"


def test_parse_stash_list_empty_and_junk() -> None:
    assert parse_stash_list("") == []
    assert parse_stash_list("not a stash line\n\n") == []


def test_parse_stash_list_no_branch_prefix() -> None:
    # A message that does not follow the "On <branch>:" convention still parses.
    entries = parse_stash_list("stash@{0}: just a label\n")
    assert len(entries) == 1
    assert entries[0].branch == ""
    assert entries[0].label == "just a label"


# ---------- pure core: fallback_label ----------

def test_fallback_label_uses_files_and_churn() -> None:
    label = fallback_label(
        "2 files changed, 10 insertions(+), 3 deletions(-)",
        ["a.py", "b.py"],
    )
    assert "2 files" in label
    assert "a.py" in label and "b.py" in label
    assert "+10/-3" in label


def test_fallback_label_truncates_many_files() -> None:
    names = [f"f{i}.py" for i in range(6)]
    label = fallback_label("6 files changed, 1 insertion(+)", names)
    assert "+3 more" in label
    assert "6 files" in label


def test_fallback_label_singular_and_no_files() -> None:
    assert "1 file" in fallback_label("1 file changed", ["only.py"])
    assert "working tree" in fallback_label("", [])


# ---------- pure core: summarize_diff ----------

def test_summarize_diff_uses_drafter() -> None:
    calls = []

    def drafter(system: str, prompt: str) -> str:
        calls.append((system, prompt))
        return "add retry to the http client"

    label = summarize_diff("diff --git a b\n+x", "1 file changed", ["http.py"], drafter=drafter)
    assert label == "add retry to the http client"
    assert calls  # the drafter was actually consulted


def test_summarize_diff_cleans_model_output() -> None:
    def drafter(system: str, prompt: str) -> str:
        return '- "Refactor the parser"\nsecond line should be dropped'

    label = summarize_diff("diff", "1 file changed", ["p.py"], drafter=drafter)
    assert label == "Refactor the parser"


def test_summarize_diff_falls_back_when_drafter_raises() -> None:
    def drafter(system: str, prompt: str) -> str:
        raise RuntimeError("no network")

    label = summarize_diff("diff", "1 file changed, 2 insertions(+)", ["p.py"], drafter=drafter)
    assert label.startswith("wip:")  # deterministic fallback


def test_summarize_diff_no_drafter_is_offline_fallback() -> None:
    label = summarize_diff("diff", "1 file changed", ["p.py"], drafter=None)
    assert label.startswith("wip:")


def test_summarize_diff_empty_diff_skips_drafter() -> None:
    def drafter(system: str, prompt: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("drafter should not be called on empty diff")

    label = summarize_diff("   ", "", [], drafter=drafter)
    assert label.startswith("wip:")


# ---------- pure core: format_stash_list ----------

def test_format_stash_list() -> None:
    entries = [
        StashEntry(ref="stash@{0}", index=0, branch="main", label="add parser"),
        StashEntry(ref="stash@{1}", index=1, branch="", label="quick fix"),
    ]
    text = format_stash_list(entries)
    assert "stash@{0}  [main] add parser" in text
    assert "stash@{1}  quick fix" in text


def test_format_stash_list_empty() -> None:
    assert format_stash_list([]) == ""


# ---------- CLI integration (real temp git repo) ----------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "base.txt").write_text("base\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")


def test_stash_save_list_pop_roundtrip_offline(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "base.txt").write_text("base\nchanged\n")
    (tmp_path / "new.txt").write_text("brand new\n")

    # --no-ai => deterministic label, no provider needed.
    r = runner.invoke(app, ["stash", "--no-ai", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "stashed" in r.output

    # working tree should be clean after stashing.
    status = subprocess.run(["git", "-C", str(tmp_path), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""

    listing = runner.invoke(app, ["stash", "list", "--root", str(tmp_path)])
    assert listing.exit_code == 0
    assert "stash@{0}" in listing.output
    assert "wip:" in listing.output

    popped = runner.invoke(app, ["stash", "pop", "--root", str(tmp_path)])
    assert popped.exit_code == 0
    assert "restored" in popped.output
    assert (tmp_path / "new.txt").exists()
    assert "changed" in (tmp_path / "base.txt").read_text()


def test_stash_save_uses_provider_label(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "base.txt").write_text("base\nmore\n")

    provider = FakeProvider(responses=[
        LLMResponse(text="tweak base content", stop_reason="end_turn", usage={})])
    with patch("ronin_cli.runner.build_provider", return_value=provider), \
         patch("ronin_cli.config.RoninConfig.has_provider_auth", return_value=True):
        r = runner.invoke(app, ["stash", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    listing = subprocess.run(["git", "-C", str(tmp_path), "stash", "list"],
                             capture_output=True, text=True).stdout
    assert "tweak base content" in listing


def test_stash_clean_tree_noops(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["stash", "--no-ai", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "clean" in r.output


def test_stash_list_empty(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["stash", "list", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "no stashes" in r.output


def test_stash_not_a_repo(tmp_path: Path) -> None:
    r = runner.invoke(app, ["stash", "--no-ai", "--root", str(tmp_path)])
    assert r.exit_code == 2
    assert "not a git repository" in r.output
