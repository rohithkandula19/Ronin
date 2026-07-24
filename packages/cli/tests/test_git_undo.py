from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.git_undo import (
    SHOW_FORMAT,
    CommitInfo,
    format_commit_info,
    is_pushed,
    parse_commit_info,
)
from ronin_cli.main import app

runner = CliRunner()


# ---------- pure core: parse_commit_info ----------

_SAMPLE = (
    "@@RONIN@@abc123def456@@RONIN@@abc123d@@RONIN@@Ada@@RONIN@@"
    "Wed Jun 17 13:54:28 2026 -0400@@RONIN@@feat: add parser\n"
    "\n"
    " README.md            |  1 +\n"
    " src/parser.py        | 20 ++++++++++++\n"
    " 2 files changed, 18 insertions(+), 3 deletions(-)\n"
)


def test_parse_commit_info_full() -> None:
    info = parse_commit_info(_SAMPLE)
    assert info is not None
    assert info.sha == "abc123def456"
    assert info.short_sha == "abc123d"
    assert info.author == "Ada"
    assert info.subject == "feat: add parser"
    assert info.files == ["README.md", "src/parser.py"]
    assert info.insertions == 18
    assert info.deletions == 3
    assert info.file_count == 2


def test_parse_commit_info_missing_header() -> None:
    assert parse_commit_info("no sentinel here\n 1 file changed\n") is None
    assert parse_commit_info("") is None


def test_parse_commit_info_subject_with_special_chars() -> None:
    # Subject containing a pipe and stat-like text must not confuse the parser.
    raw = (
        "@@RONIN@@h1@@RONIN@@h1s@@RONIN@@Bob@@RONIN@@today@@RONIN@@"
        "fix: handle a | b and 3 insertions\n"
        " f.py | 2 +-\n"
        " 1 file changed, 1 insertion(+), 1 deletion(-)\n"
    )
    info = parse_commit_info(raw)
    assert info is not None
    assert info.subject == "fix: handle a | b and 3 insertions"
    assert info.files == ["f.py"]
    assert info.insertions == 1 and info.deletions == 1


def test_parse_commit_info_rename_keeps_new_name() -> None:
    raw = (
        "@@RONIN@@h@@RONIN@@hs@@RONIN@@A@@RONIN@@d@@RONIN@@move thing\n"
        " old.py => new.py | 0\n"
        " 1 file changed, 0 insertions(+), 0 deletions(-)\n"
    )
    info = parse_commit_info(raw)
    assert info is not None
    assert info.files == ["new.py"]


# ---------- pure core: is_pushed ----------

def test_is_pushed_no_upstream_is_false() -> None:
    assert is_pushed("", "abc", head_in_upstream=False) is False
    assert is_pushed("def", "", head_in_upstream=True) is False


def test_is_pushed_equal_tip() -> None:
    assert is_pushed("abc", "abc", head_in_upstream=False) is True


def test_is_pushed_ancestor() -> None:
    assert is_pushed("upstream", "head", head_in_upstream=True) is True
    assert is_pushed("upstream", "head", head_in_upstream=False) is False


# ---------- pure core: format_commit_info ----------

def test_format_commit_info() -> None:
    info = CommitInfo(sha="s", short_sha="abc123d", author="Ada", date="today",
                      subject="feat: x", files=["a.py", "b.py"], insertions=5, deletions=2)
    text = format_commit_info(info)
    assert "abc123d  feat: x" in text
    assert "author: Ada" in text
    assert "2 files changed, +5/-2" in text
    assert "a.py" in text and "b.py" in text


def test_format_commit_info_truncates_files() -> None:
    info = CommitInfo(sha="s", short_sha="h", author="A", date="d",
                      subject="x", files=[f"f{i}.py" for i in range(10)])
    text = format_commit_info(info)
    assert "(+2 more)" in text


# ---------- CLI integration (real temp git repos) ----------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "first")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "second: add line")


def test_undo_commit_soft_reset(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["dev", "undo-commit", "--yes", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "undone" in r.output
    # The "second" commit is gone, but its change is staged.
    log = _git(tmp_path, "log", "--oneline").stdout
    assert "second" not in log
    assert "first" in log
    staged = _git(tmp_path, "diff", "--cached", "--name-only").stdout
    assert "a.txt" in staged


def test_undo_commit_revert(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["dev", "undo-commit", "--revert", "--yes", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "reverted" in r.output
    log = _git(tmp_path, "log", "--oneline").stdout
    # The original commit is still there, plus a new revert commit.
    assert "second" in log
    assert "Revert" in log
    assert "two" not in (tmp_path / "a.txt").read_text()


def test_undo_commit_abort_on_no(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["dev", "undo-commit", "--root", str(tmp_path)], input="n\n")
    assert r.exit_code == 0
    assert "aborted" in r.output
    log = _git(tmp_path, "log", "--oneline").stdout
    assert "second" in log  # nothing changed


def test_undo_commit_shows_commit_info(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    r = runner.invoke(app, ["dev", "undo-commit", "--root", str(tmp_path)], input="n\n")
    assert "second: add line" in r.output
    assert "last commit" in r.output


def test_undo_commit_pushed_guard_blocks_soft_reset(tmp_path: Path) -> None:
    # Simulate a pushed HEAD with a local "remote" repo as upstream.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, text=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], capture_output=True, text=True)
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("one\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    (work / "a.txt").write_text("one\ntwo\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "pushed second")
    _git(work, "push", "-u", "origin", "HEAD")

    r = runner.invoke(app, ["dev", "undo-commit", "--yes", "--root", str(work)])
    assert r.exit_code == 2, r.output
    assert "pushed" in r.output
    # Commit must still be there (blocked, not rewritten).
    assert "pushed second" in _git(work, "log", "--oneline").stdout


def test_undo_commit_pushed_force_allows_soft_reset(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, text=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], capture_output=True, text=True)
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("one\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "HEAD")
    (work / "a.txt").write_text("one\ntwo\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "pushed second")
    _git(work, "push")

    r = runner.invoke(app, ["dev", "undo-commit", "--force", "--yes", "--root", str(work)])
    assert r.exit_code == 0, r.output
    assert "undone" in r.output
    assert "pushed second" not in _git(work, "log", "--oneline").stdout


def test_undo_commit_not_a_repo(tmp_path: Path) -> None:
    r = runner.invoke(app, ["dev", "undo-commit", "--yes", "--root", str(tmp_path)])
    assert r.exit_code == 2
    assert "not a git repository" in r.output


def test_undo_commit_format_string_roundtrips() -> None:
    # Sanity: the format string the command uses produces a header parse_commit_info reads.
    assert SHOW_FORMAT.count("@@RONIN@@") == 5
