from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from ronin_cli.todo_scan import (
    parse_markers,
    priority,
    render_todos,
    scan_repo,
    sort_todos,
)


# ---- parse_markers ---------------------------------------------------------

def test_parse_markers_finds_todo_and_fixme_with_line_numbers() -> None:
    text = "x = 1  # TODO: fix later\ny = 2\n# FIXME: broken"
    hits = parse_markers(text, "a.py")
    assert len(hits) == 2
    assert hits[0] == {"path": "a.py", "line": 1, "kind": "TODO", "text": "fix later"}
    assert hits[1] == {"path": "a.py", "line": 3, "kind": "FIXME", "text": "broken"}


def test_parse_markers_ignores_lines_without_a_marker() -> None:
    # No comment marker, and a marker-shaped word that is NOT in a comment.
    text = "y = 2\nlist_of_todos = []\nmessage = 'this is a BUG report'\n"
    assert parse_markers(text, "a.py") == []


def test_parse_markers_handles_all_comment_styles() -> None:
    text = "\n".join([
        "code()  // HACK: temporary shim",   # line 1, C-style line comment
        "/* XXX: revisit this block */",      # line 2, C-style block comment
        "value = 0  # BUG no colon here",     # line 3, hash, no colon
    ])
    hits = parse_markers(text, "f.ts")
    kinds = {h["kind"] for h in hits}
    assert kinds == {"HACK", "XXX", "BUG"}
    block = next(h for h in hits if h["kind"] == "XXX")
    assert block["text"] == "revisit this block"  # trailing */ stripped
    nocolon = next(h for h in hits if h["kind"] == "BUG")
    assert nocolon["text"] == "no colon here"


# ---- priority --------------------------------------------------------------

def test_priority_orders_fixme_over_todo_over_hack() -> None:
    assert priority("FIXME") < priority("TODO") < priority("HACK")
    # BUG ranks with FIXME; XXX ranks with TODO.
    assert priority("BUG") == priority("FIXME")
    assert priority("XXX") == priority("TODO")
    assert priority("todo") == priority("TODO")  # case-insensitive


# ---- sort_todos ------------------------------------------------------------

def test_sort_todos_puts_fixme_first() -> None:
    items = [
        {"path": "z.py", "line": 9, "kind": "HACK", "text": ""},
        {"path": "a.py", "line": 1, "kind": "TODO", "text": ""},
        {"path": "m.py", "line": 5, "kind": "FIXME", "text": ""},
    ]
    ordered = sort_todos(items)
    assert [it["kind"] for it in ordered] == ["FIXME", "TODO", "HACK"]
    # Pure: original list is not mutated.
    assert items[0]["kind"] == "HACK"


def test_sort_todos_tie_breaks_by_path_then_line() -> None:
    items = [
        {"path": "b.py", "line": 2, "kind": "TODO", "text": ""},
        {"path": "a.py", "line": 7, "kind": "TODO", "text": ""},
        {"path": "a.py", "line": 3, "kind": "TODO", "text": ""},
    ]
    ordered = sort_todos(items)
    assert [(it["path"], it["line"]) for it in ordered] == [
        ("a.py", 3), ("a.py", 7), ("b.py", 2),
    ]


# ---- scan_repo (impure) ----------------------------------------------------

def test_scan_repo_walks_tree_and_skips_vendored_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# TODO: real one\nok = 1\n")
    (tmp_path / "README.md").write_text("nothing here\n")
    # These must be skipped: ignored dir + non-text file.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("// FIXME: vendored, ignore me\n")
    (tmp_path / "image.png").write_text("# TODO: not a text file\n")

    hits = scan_repo(tmp_path)
    assert len(hits) == 1
    assert hits[0]["kind"] == "TODO"
    assert hits[0]["path"] == str(Path("src") / "app.py")
    assert hits[0]["text"] == "real one"


def test_scan_repo_returns_sorted_results(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# HACK: ugly\n# FIXME: urgent\n")
    hits = scan_repo(tmp_path)
    assert [h["kind"] for h in hits] == ["FIXME", "HACK"]


# ---- render_todos ----------------------------------------------------------

def test_render_todos_shows_counts_and_rows() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    render_todos([
        {"path": "a.py", "line": 1, "kind": "TODO", "text": "fix later"},
        {"path": "b.py", "line": 3, "kind": "FIXME", "text": "broken"},
    ], console)
    out = buf.getvalue()
    assert "2 marker(s)" in out
    assert "FIXME" in out and "TODO" in out
    assert "a.py:1" in out and "b.py:3" in out
    assert "fix later" in out and "broken" in out


def test_render_todos_empty_is_clean() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    render_todos([], console)
    assert "clean" in buf.getvalue()
