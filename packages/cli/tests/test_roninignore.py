"""Tests for .roninignore support in the secret scanner."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.scan import is_ignored, load_ignore, scan_tree

_LEAK = 'AWS = "AKIAZX7Q2RSTUV3BWXYC"\n'  # ronin:allow-secret


def test_load_ignore_skips_comments_and_blanks(tmp_path: Path) -> None:
    (tmp_path / ".roninignore").write_text("# a comment\n\n*.tmp\nfixtures/\n", encoding="utf-8")
    assert load_ignore(tmp_path) == ["*.tmp", "fixtures/"]


def test_is_ignored_matches_glob_basename_segment() -> None:
    pats = ["*.tmp", "secrets.py", "tests/"]
    assert is_ignored("a/b/c.tmp", pats)            # glob on full/basename
    assert is_ignored("pkg/secrets.py", pats)       # basename match
    assert is_ignored("pkg/tests/x.py", pats)       # path segment match
    assert not is_ignored("pkg/main.py", pats)


def test_scan_tree_respects_roninignore(tmp_path: Path) -> None:
    (tmp_path / "fixture_leak.py").write_text(_LEAK.replace("  # ronin:allow-secret", ""),
                                              encoding="utf-8")
    (tmp_path / "real_leak.py").write_text('K = "AKIAZX7Q2RSTUV3BWXYC"\n', encoding="utf-8")
    (tmp_path / ".roninignore").write_text("fixture_leak.py\n", encoding="utf-8")
    paths = {f.path for f in scan_tree(tmp_path)}
    assert "real_leak.py" in paths
    assert "fixture_leak.py" not in paths   # ignored
