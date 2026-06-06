"""Tests for .gitignore generation."""
from __future__ import annotations

from ronin_cli.gitignore_gen import available, gitignore_for


def test_basic_python_node() -> None:
    out = gitignore_for(["python", "node"])
    assert "__pycache__/" in out and "node_modules/" in out
    assert "# python" in out and "# node" in out


def test_aliases_resolve() -> None:
    out = gitignore_for(["py", "js", "osx"])
    assert "__pycache__/" in out and "node_modules/" in out and ".DS_Store" in out


def test_dedupes_shared_patterns() -> None:
    # both java and go would add 'build/' style entries; ensure no dupes overall
    out = gitignore_for(["node", "java"])
    assert out.count("build/") == 1


def test_unknown_skipped() -> None:
    out = gitignore_for(["python", "totally-unknown"])
    assert "__pycache__/" in out and "totally-unknown" not in out


def test_available_lists_templates() -> None:
    a = available()
    assert "python" in a and "macos" in a and "rust" in a
