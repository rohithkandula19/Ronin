"""Tests for `ronin version` git context and the `ronin update` command.

All offline. No network. The git repo cases build a throwaway local repo in a
tmp dir; the non-git case uses a bare tmp dir. We patch find_repo_root so the
commands operate on the tmp tree instead of the real install.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli import selfupdate
from ronin_cli.main import app

runner = CliRunner()


# ---------- ronin version ----------

def test_version_output_contains_version() -> None:
    from ronin_cli import __version__, display_version
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0, r.stdout
    assert display_version(__version__) in r.stdout   # friendly display (e.g. 1.0.0-rc.1)
    assert "ronin" in r.stdout


def test_version_line_plain_when_not_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selfupdate, "find_repo_root", lambda start=None: None)
    line = selfupdate.version_line()
    assert line == f"ronin {selfupdate.display_version(selfupdate.__version__)}"
    assert "(" not in line


def test_version_line_shows_sha_and_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(selfupdate, "find_repo_root", lambda start=None: tmp_path)
    monkeypatch.setattr(selfupdate, "git_short_sha", lambda root: "abc1234")
    monkeypatch.setattr(selfupdate, "git_branch", lambda root: "main")
    assert selfupdate.version_line() == f"ronin {selfupdate.display_version(selfupdate.__version__)} (abc1234, main)"


# ---------- repo-root walk-up ----------

def test_find_repo_root_walks_up_to_dot_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "packages" / "cli" / "src"
    nested.mkdir(parents=True)
    found = selfupdate.find_repo_root(nested / "ronin_cli" / "main.py")
    assert found == tmp_path


def test_find_repo_root_none_outside_checkout(tmp_path: Path) -> None:
    # tmp_path has no .git anywhere up to the fs root we control.
    assert selfupdate.find_repo_root(tmp_path / "deep" / "file.py") is None


# ---------- ronin update: non-git temp dir ----------

def test_update_on_non_git_dir_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ronin_cli.selfupdate.find_repo_root", lambda start=None: None)
    r = runner.invoke(app, ["update"])
    assert r.exit_code == 0, r.stdout
    assert "not a git checkout" in r.stdout


def test_update_check_on_non_git_dir_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ronin_cli.selfupdate.find_repo_root", lambda start=None: None)
    r = runner.invoke(app, ["update", "--check"])
    assert r.exit_code == 0, r.stdout
    assert "not a git checkout" in r.stdout


# ---------- ronin update --check: does not mutate ----------

def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.test"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True,
                   capture_output=True, text=True)
    (path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True,
                   capture_output=True, text=True)


def test_update_check_does_not_mutate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--check must leave HEAD and the working tree untouched.

    We build a tmp repo whose origin/main == HEAD (so 'up to date'), point the
    command at it, run --check, and assert HEAD is unchanged.
    """
    upstream = tmp_path / "upstream"
    _make_repo(upstream)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(upstream), str(clone)], check=True,
                   capture_output=True, text=True)
    # name the remote 'origin' (clone already does) and ensure origin/main exists
    subprocess.run(["git", "-C", str(clone), "fetch", "origin"], check=True,
                   capture_output=True, text=True)

    monkeypatch.setattr("ronin_cli.selfupdate.find_repo_root", lambda start=None: clone)

    head_before = _git(clone, "rev-parse", "HEAD")
    status_before = _git(clone, "status", "--porcelain")

    r = runner.invoke(app, ["update", "--check"])
    assert r.exit_code == 0, r.stdout

    head_after = _git(clone, "rev-parse", "HEAD")
    status_after = _git(clone, "status", "--porcelain")
    assert head_before == head_after
    assert status_before == status_after


def test_update_check_reports_available_without_mutating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When origin/main is ahead, --check reports it but still does not mutate."""
    upstream = tmp_path / "upstream"
    _make_repo(upstream)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(upstream), str(clone)], check=True,
                   capture_output=True, text=True)

    # advance upstream so origin is ahead of the clone after a fetch
    (upstream / "f.txt").write_text("hello world\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(upstream), "commit", "-am", "more"], check=True,
                   capture_output=True, text=True)

    monkeypatch.setattr("ronin_cli.selfupdate.find_repo_root", lambda start=None: clone)

    head_before = _git(clone, "rev-parse", "HEAD")
    r = runner.invoke(app, ["update", "--check"])
    assert r.exit_code == 0, r.stdout
    assert "update available" in r.stdout
    head_after = _git(clone, "rev-parse", "HEAD")
    assert head_before == head_after
