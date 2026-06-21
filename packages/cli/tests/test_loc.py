"""Unit tests for ``ronin loc`` — the pure code-stats core.

Covers the three load-bearing pure functions:
* ``classify_language`` — extension → language, plus the unknown fallback.
* ``count_lines`` — code / comment / blank split on a mixed snippet.
* ``aggregate`` — per-language rollup + totals across two files.

Plus a couple of edge cases and a smoke test of the impure ``scan`` walker on a
tmp tree (skip dirs + binary skipping). No network, no real repo.
"""
from __future__ import annotations

from pathlib import Path

from ronin_cli.loc import aggregate, classify_language, count_lines, scan


# --------------------------------------------------------------------------- #
# classify_language
# --------------------------------------------------------------------------- #
def test_classify_language_python() -> None:
    assert classify_language("a.py") == "Python"
    assert classify_language("pkg/sub/module.py") == "Python"
    assert classify_language("stubs.pyi") == "Python"


def test_classify_language_typescript() -> None:
    assert classify_language("a.ts") == "TypeScript"
    assert classify_language("Component.tsx") == "TypeScript"


def test_classify_language_other_for_unknown() -> None:
    assert classify_language("a.unknownext") == "Other"
    assert classify_language("noextension") == "Other"
    assert classify_language("archive.tar.zzz") == "Other"


def test_classify_language_covers_the_common_set() -> None:
    # the families the spec calls out explicitly
    assert classify_language("main.js") == "JavaScript"
    assert classify_language("lib.rs") == "Rust"
    assert classify_language("server.go") == "Go"
    assert classify_language("README.md") == "Markdown"


def test_classify_language_well_known_filenames() -> None:
    # extension-less but recognised by name
    assert classify_language("Dockerfile") == "Dockerfile"
    assert classify_language("project/Makefile") == "Makefile"


# --------------------------------------------------------------------------- #
# count_lines
# --------------------------------------------------------------------------- #
def test_count_lines_code_comment_blank() -> None:
    # 2 code, 1 comment, 1 blank — and a trailing newline must NOT add a blank.
    text = "x = 1\n# a comment\n\ny = 2\n"
    counts = count_lines(text, "Python")
    assert counts == {"code": 2, "comment": 1, "blank": 1}
    # the three always sum to the real line count (4 lines here)
    assert sum(counts.values()) == 4


def test_count_lines_indented_comment_is_a_comment() -> None:
    text = "def f():\n    # inner\n    return 1\n"
    assert count_lines(text, "Python") == {"code": 2, "comment": 1, "blank": 0}


def test_count_lines_uses_language_comment_prefix() -> None:
    # In TypeScript the '#' line is code, the '//' line is the comment.
    ts = "const x = 1;\n// note\n#notacomment\n"
    assert count_lines(ts, "TypeScript") == {"code": 2, "comment": 1, "blank": 0}
    # SQL uses '--'
    sql = "select 1;\n-- pick one\n"
    assert count_lines(sql, "SQL") == {"code": 1, "comment": 1, "blank": 0}


def test_count_lines_language_without_comments_counts_all_code() -> None:
    # JSON has no line-comment syntax → every non-blank line is code.
    js = '{\n  "a": 1\n}\n'
    assert count_lines(js, "JSON") == {"code": 3, "comment": 0, "blank": 0}


def test_count_lines_empty_text() -> None:
    assert count_lines("", "Python") == {"code": 0, "comment": 0, "blank": 0}


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
def test_aggregate_rolls_up_two_languages_and_totals() -> None:
    rows = [
        {"path": "a.py", "language": "Python", "code": 10, "comment": 2, "blank": 3},
        {"path": "b.ts", "language": "TypeScript", "code": 5, "comment": 1, "blank": 1},
        {"path": "c.py", "language": "Python", "code": 20, "comment": 0, "blank": 4},
    ]
    agg = aggregate(rows)

    # totals
    assert agg["files"] == 3
    assert agg["code"] == 35
    assert agg["comment"] == 3
    assert agg["blank"] == 8
    assert agg["lines"] == 35 + 3 + 8

    # per-language rollup, busiest (by code) first → Python (30) then TypeScript (5)
    langs = {d["language"]: d for d in agg["languages"]}
    assert [d["language"] for d in agg["languages"]] == ["Python", "TypeScript"]
    assert langs["Python"]["files"] == 2
    assert langs["Python"]["code"] == 30
    assert langs["Python"]["comment"] == 2
    assert langs["TypeScript"]["code"] == 5

    # comment ratio: overall 3 / (35 + 3)
    assert abs(agg["comment_ratio"] - 3 / 38) < 1e-9
    # Python's own ratio: 2 / (30 + 2)
    assert abs(langs["Python"]["comment_ratio"] - 2 / 32) < 1e-9


def test_aggregate_biggest_files_ordered_by_code() -> None:
    rows = [
        {"path": "small.py", "language": "Python", "code": 1, "comment": 0, "blank": 0},
        {"path": "big.py", "language": "Python", "code": 100, "comment": 0, "blank": 0},
        {"path": "mid.py", "language": "Python", "code": 50, "comment": 0, "blank": 0},
    ]
    agg = aggregate(rows)
    assert [f["path"] for f in agg["biggest"]] == ["big.py", "mid.py", "small.py"]


def test_aggregate_empty_is_all_zeros() -> None:
    agg = aggregate([])
    assert agg["files"] == 0
    assert agg["code"] == 0
    assert agg["languages"] == []
    assert agg["biggest"] == []
    assert agg["comment_ratio"] == 0.0


def test_aggregate_tolerates_malformed_rows() -> None:
    rows = [
        {"path": "ok.py", "language": "Python", "code": "7", "comment": None, "blank": 2},
        "not-a-dict",  # type: ignore[list-item]
        {"path": "bad.py", "language": "Python", "code": -5, "comment": 1, "blank": 0},
    ]
    agg = aggregate(rows)  # type: ignore[arg-type]
    # the string row is skipped; "7" coerces to 7; -5 clamps to 0
    assert agg["files"] == 2
    assert agg["code"] == 7
    assert agg["comment"] == 1


# --------------------------------------------------------------------------- #
# scan (impure smoke test on a tmp tree)
# --------------------------------------------------------------------------- #
def test_scan_walks_text_files_and_skips_vendored_and_binary(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n# c\n\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text("const x = 1;\n// note\n", encoding="utf-8")

    # vendored dir → must be skipped entirely
    vendored = tmp_path / "node_modules" / "dep"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("module.exports = {};\n", encoding="utf-8")

    # a binary file → must be skipped (NUL byte)
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02ELF")

    rows = scan(tmp_path)
    paths = {r["path"] for r in rows}
    assert paths == {"main.py", "app.ts"}  # no node_modules, no blob.bin

    by_path = {r["path"]: r for r in rows}
    assert by_path["main.py"]["language"] == "Python"
    assert by_path["main.py"] == {
        "path": "main.py",
        "language": "Python",
        "code": 1,
        "comment": 1,
        "blank": 1,
    }


def test_scan_missing_root_is_empty() -> None:
    assert scan("/this/path/does/not/exist/anywhere") == []
