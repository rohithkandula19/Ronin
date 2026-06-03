"""Tests for the tool-result preview summarizer."""
from __future__ import annotations

from ronin_cli.streaming import _summarize_result


def test_run_command_success() -> None:
    out = "exit=0\n--- stdout ---\nhello world\nmore\n--- stderr ---\n"
    s = _summarize_result(out)
    assert s.startswith("✓ exit 0") and "hello world" in s


def test_run_command_failure() -> None:
    out = "exit=1\n--- stdout ---\n\n--- stderr ---\nTraceback: boom\n"
    s = _summarize_result(out)
    assert s.startswith("✗ exit 1") and "Traceback" in s


def test_run_command_no_output() -> None:
    assert _summarize_result("exit=0\n--- stdout ---\n\n--- stderr ---\n").strip() == "✓ exit 0"


def test_list_result_counts() -> None:
    s = _summarize_result('["a.py", "b.py", "c.py"]')
    assert "3 items" in s and "a.py" in s


def test_multiline_result() -> None:
    assert _summarize_result("line1\nline2\nline3").startswith("3 lines")


def test_empty() -> None:
    assert _summarize_result("") == ""
