"""Tests for the clean diff-row parser (the prettier diff renderer's core)."""
from __future__ import annotations

from ronin_cli.code_tools import diff_rows, unified_diff


def test_new_file() -> None:
    rows = diff_rows(unified_diff("test.py", "", 'print("hi")\n'))
    adds = [r for r in rows if r["kind"] == "add"]
    assert len(adds) == 1
    assert adds[0]["text"] == 'print("hi")' and adds[0]["lineno"] == 1


def test_drops_git_noise() -> None:
    rows = diff_rows(unified_diff("a.py", "x\n", "y\n"))
    texts = [r["text"] for r in rows]
    assert not any(t.startswith("a/") or t.startswith("b/") or t.startswith("@@") for t in texts)


def test_add_del_context_kinds() -> None:
    before = "line1\nline2\nline3\n"
    after = "line1\nCHANGED\nline3\n"
    rows = diff_rows(unified_diff("f.py", before, after))
    kinds = [r["kind"] for r in rows]
    assert "add" in kinds and "del" in kinds and "ctx" in kinds


def test_line_numbers_track_new_file_side() -> None:
    before = "a\nb\nc\nd\ne\n"
    after = "a\nb\nB2\nc\nd\ne\n"   # insert B2 after b
    rows = diff_rows(unified_diff("f.py", before, after))
    add = next(r for r in rows if r["kind"] == "add")
    assert add["text"] == "B2"
    # the added line is line 3 in the new file
    assert add["lineno"] == 3


def test_deletes_have_no_new_lineno() -> None:
    rows = diff_rows(unified_diff("f.py", "keep\ndrop\nkeep\n", "keep\nkeep\n"))
    dels = [r for r in rows if r["kind"] == "del"]
    assert dels and all(r["lineno"] is None for r in dels)


def test_empty_diff() -> None:
    assert diff_rows("(no change)") == []
    assert diff_rows("") == []


def test_separator_between_hunks() -> None:
    # a long file with two far-apart edits → two hunks → a separator row
    before = "\n".join(f"line{i}" for i in range(40)) + "\n"
    after = before.replace("line1", "LINE1").replace("line38", "LINE38")
    rows = diff_rows(unified_diff("f.py", before, after))
    assert any(r["kind"] == "sep" for r in rows)
