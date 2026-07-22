"""``ronin scan --history`` — the git-history secret sweep.

Verifies the pure ``scan_history`` parser over synthetic ``git log -p`` output:
it flags secrets on *added* lines with the right commit/path/line, ignores
removed lines, tracks line numbers across context, and — like the rest of the
scanner — NEVER emits the raw secret value (only a masked hint or the kind).

Secret-shaped fixtures are assembled at runtime from harmless pieces so this
test file contains no literal that looks like a real credential.
"""
from __future__ import annotations

from ronin_cli.secret_scan import scan_history


def _openai_key() -> str:
    # sk- + 40 chars → matches the openai-key pattern (sk-[A-Za-z0-9_-]{32,}).
    return "sk-" + "x" * 40


def _diff(commit: str, path: str, hunk: str, body_lines: list[str]) -> str:
    return (
        f"commit {commit}\n"
        "Author: Dev <dev@example.com>\n"
        "Date:   Mon Jan 1 00:00:00 2026 +0000\n\n"
        "    a change\n\n"
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"{hunk}\n" + "\n".join(body_lines) + "\n"
    )


def test_finds_secret_on_added_line_with_location() -> None:
    key = _openai_key()
    text = _diff(
        "a1b2c3d4e5f6", "config.py", "@@ -1,2 +1,3 @@",
        [" import os", f'+TOKEN = "{key}"', " x = 1"],
    )
    hits = scan_history(text)
    assert len(hits) == 1
    h = hits[0]
    assert h["commit"] == "a1b2c3d4e5f6"
    assert h["path"] == "config.py"
    assert h["line"] == 2            # the added line is line 2 in the new file
    assert h["kind"] == "openai-key"


def test_never_emits_the_raw_secret() -> None:
    key = _openai_key()
    text = _diff(
        "deadbeef", "s.py", "@@ -0,0 +1 @@", [f"+k = '{key}'"],
    )
    hits = scan_history(text)
    assert hits, "expected a hit"
    for h in hits:
        assert key not in h["hint"]
        assert key not in h["kind"]


def test_ignores_removed_lines() -> None:
    key = _openai_key()
    # A secret being *removed* is not a new leak on that line; only added lines
    # are flagged (the whole point is that removed secrets still live earlier in
    # history, which the walk catches at the commit that ADDED them).
    text = _diff(
        "c0ffee", "s.py", "@@ -1,2 +1,1 @@",
        [" keep", f'-secret = "{key}"'],
    )
    hits = scan_history(text)
    assert hits == []


def test_line_numbers_track_context() -> None:
    key = _openai_key()
    text = _diff(
        "abc123", "deep.py", "@@ -10,3 +10,4 @@",
        [" ctx10", " ctx11", f'+leak = "{key}"', " ctx13"],
    )
    hits = scan_history(text)
    assert len(hits) == 1
    assert hits[0]["line"] == 12     # 10, 11, then the added line at 12


def test_tracks_multiple_commits_and_files() -> None:
    key = _openai_key()
    text = (
        _diff("1111111", "a.py", "@@ -0,0 +1 @@", [f'+x = "{key}"'])
        + _diff("2222222", "b.py", "@@ -0,0 +1 @@", [f'+y = "{key}"'])
    )
    hits = scan_history(text)
    assert {h["commit"] for h in hits} == {"1111111", "2222222"}
    assert {h["path"] for h in hits} == {"a.py", "b.py"}


def test_clean_history_returns_nothing() -> None:
    text = _diff(
        "f00d", "ok.py", "@@ -0,0 +2 @@",
        ["+import os", "+print('hello')"],
    )
    assert scan_history(text) == []
