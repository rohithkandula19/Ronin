"""Tests for the tool-result preview summarizer."""
from __future__ import annotations

from ronin_cli.streaming import _normalize_markdown, _summarize_result


def test_normalize_markdown_converts_html_breaks() -> None:
    # Models emit <br>, <br/>, <br /> (any case) inside cells/paragraphs; rich
    # would print them literally. They must become real newlines, none left over.
    src = "a<br>b<br/>c<BR />d<br   >e"
    out = _normalize_markdown(src)
    assert out == "a\nb\nc\nd\ne"
    assert "<br" not in out.lower()


def test_normalize_markdown_leaves_plain_text() -> None:
    src = "## Heading\n\n- **bold** item\n- `code` item"
    assert _normalize_markdown(src) == src


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
