"""Tests for secret scanning + the pre-commit hook installer."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.git_hook import MARKER, hook_path, install_hook, is_installed, uninstall_hook
from ronin_cli.scan import scan_staged, scan_tree, staged_files

_LEAK = 'AWS = "AKIAZX7Q2RSTUV3BWXYC"\n'  # ronin:allow-secret   # well-formed fake key (not a known example)


def test_scan_tree_finds_secret(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text(_LEAK, encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    findings = scan_tree(tmp_path)
    paths = {f.path for f in findings}
    assert "config.py" in paths and "clean.py" not in paths


def test_scan_tree_skips_vendor(tmp_path: Path) -> None:
    vend = tmp_path / "node_modules"
    vend.mkdir()
    (vend / "leak.js").write_text(_LEAK, encoding="utf-8")
    assert scan_tree(tmp_path) == []


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def test_scan_staged_only_staged(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "staged.py").write_text(_LEAK, encoding="utf-8")
    (tmp_path / "unstaged.py").write_text(_LEAK, encoding="utf-8")
    _git(tmp_path, "add", "staged.py")
    assert "staged.py" in staged_files(tmp_path)
    findings = {f.path for f in scan_staged(tmp_path)}
    assert findings == {"staged.py"}   # unstaged leak not flagged


def test_hook_install_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    path, fresh = install_hook(tmp_path)
    assert fresh and is_installed(tmp_path)
    assert MARKER in path.read_text(encoding="utf-8")
    _, fresh2 = install_hook(tmp_path)
    assert not fresh2   # no double-add


def test_hook_appends_and_uninstalls_cleanly(tmp_path: Path) -> None:
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    hp = hook_path(tmp_path)
    hp.write_text("#!/usr/bin/env bash\necho existing-hook\n", encoding="utf-8")
    install_hook(tmp_path)
    assert "existing-hook" in hp.read_text(encoding="utf-8")   # preserved
    assert is_installed(tmp_path)
    assert uninstall_hook(tmp_path)
    text = hp.read_text(encoding="utf-8")
    assert "existing-hook" in text and MARKER not in text      # ours gone, theirs kept
