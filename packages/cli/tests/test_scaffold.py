"""Tests for scaffold — stack detection + prompt."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.scaffold import detect_stack, scaffold_prompt


def test_detect_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert "python" in detect_stack(tmp_path)


def test_detect_node_and_ts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    s = detect_stack(tmp_path)
    assert "node" in s and "typescript" in s


def test_detect_unknown(tmp_path: Path) -> None:
    assert detect_stack(tmp_path) == ["unknown"]


def test_detect_dedup_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("x", encoding="utf-8")
    # both are python markers → 'python' listed once
    assert detect_stack(tmp_path).count("python") == 1


def test_scaffold_prompt() -> None:
    p = scaffold_prompt("a RetryClient with backoff", ["python"])
    assert "RetryClient with backoff" in p and "python project" in p
    assert "test" in p.lower() and "conventions" in p.lower()
