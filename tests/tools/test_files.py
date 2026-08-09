"""read, write, edit, multi_edit against a real temp directory.

The ``edit`` section is the largest on purpose. It is the tool that can silently do
the wrong thing, so the tests spend their effort on the cases where a sloppier
implementation would succeed *incorrectly*: two matches, near-miss whitespace, CRLF
against LF, and identical repeated blocks.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from harness import call, context, write_file

from ronin.tools import (
    EditTool,
    MultiEditTool,
    ReadTool,
    ToolContext,
    WriteTool,
    apply_edit,
    number_lines,
)
from ronin.tools.base import ToolError
from ronin.tools.files import MAX_READ_LINES

READ = ReadTool()
WRITE = WriteTool()
EDIT = EditTool()
MULTI = MultiEditTool()


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #


async def test_read_returns_cat_n_formatted_lines(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "import sys\n\ndef main():\n    pass\n")
    result = await call(READ, context(tmp_path), path="a.py")
    assert result.ok
    lines = result.content.split("\n")
    assert lines[0].endswith("\timport sys")
    assert lines[0].strip().startswith("1")
    assert "\t" in lines[0], "the number and content must be tab-separated"


async def test_read_marks_the_file_as_read(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "x = 1\n")
    ctx = context(tmp_path)
    await call(READ, ctx, path="a.py")
    assert ctx.has_been_read(tmp_path / "a.py")


async def test_read_honours_offset_and_limit(tmp_path: Path) -> None:
    write_file(tmp_path, "big.txt", "\n".join(f"line {i}" for i in range(1, 101)) + "\n")
    result = await call(READ, context(tmp_path), path="big.txt", offset=50, limit=3)
    assert "line 50" in result.content
    assert "line 52" in result.content
    assert "line 53" not in result.content
    assert "   50\tline 50" in result.content, "numbering continues from the offset"


async def test_read_truncates_at_the_line_cap_with_an_actionable_marker(tmp_path: Path) -> None:
    total = MAX_READ_LINES + 500
    write_file(tmp_path, "huge.txt", "\n".join(str(i) for i in range(total)) + "\n")
    result = await call(READ, context(tmp_path), path="huge.txt")
    assert "truncated" in result.content
    assert f"offset={MAX_READ_LINES + 1}" in result.content, "must say how to get the rest"
    body = result.content.split("\n…[")[0]
    assert len(body.split("\n")) == MAX_READ_LINES


async def test_read_clips_an_absurdly_long_line(tmp_path: Path) -> None:
    write_file(tmp_path, "min.js", "x" * 10_000 + "\n")
    result = await call(READ, context(tmp_path), path="min.js")
    assert "more chars on this line" in result.content
    assert len(result.content) < 5_000


async def test_read_reports_an_empty_file_as_empty(tmp_path: Path) -> None:
    write_file(tmp_path, "empty.txt", "")
    result = await call(READ, context(tmp_path), path="empty.txt")
    assert result.ok
    assert "empty" in result.content


async def test_read_refuses_a_binary_file_with_a_useful_message(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02" * 100)
    result = await call(READ, context(tmp_path), path="data.bin")
    assert not result.ok
    assert "binary" in result.error
    assert "context" in result.error, "say why it is refused, not just that it is"


async def test_read_names_the_format_for_a_known_binary_type(tmp_path: Path) -> None:
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.7 not really")
    result = await call(READ, context(tmp_path), path="doc.pdf")
    assert not result.ok
    assert "a PDF" in result.error
    assert "pdftotext" in result.error, "point at the workaround"


async def test_read_falls_back_to_latin1_rather_than_refusing_mostly_ascii(
    tmp_path: Path,
) -> None:
    (tmp_path / "odd.txt").write_bytes(b"caf\xe9 latte\n")
    result = await call(READ, context(tmp_path), path="odd.txt")
    assert result.ok
    assert "latte" in result.content


async def test_read_a_missing_file_says_how_to_find_the_right_one(tmp_path: Path) -> None:
    result = await call(READ, context(tmp_path), path="nope.py")
    assert not result.ok
    assert "does not exist" in result.error
    assert "glob" in result.error or "ls" in result.error


async def test_read_a_directory_points_at_ls(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    result = await call(READ, context(tmp_path), path="pkg")
    assert not result.ok
    assert "ls" in result.error


async def test_read_refuses_a_path_outside_the_root(tmp_path: Path) -> None:
    result = await call(READ, context(tmp_path), path="../../etc/passwd")
    assert not result.ok
    assert "outside the working directory" in result.error


async def test_read_refuses_a_symlink_escaping_the_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret\n")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "link.txt").symlink_to(outside)
    result = await call(READ, context(root), path="link.txt")
    assert not result.ok
    assert "outside the working directory" in result.error
    outside.unlink()


async def test_read_rejects_an_offset_past_the_end(tmp_path: Path) -> None:
    write_file(tmp_path, "short.txt", "one\ntwo\n")
    result = await call(READ, context(tmp_path), path="short.txt", offset=99)
    assert not result.ok
    assert "past the end" in result.error


async def test_read_an_image_without_vision_explains_instead_of_dumping_base64(
    tmp_path: Path,
) -> None:
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    result = await call(READ, context(tmp_path, vision=False), path="shot.png")
    assert result.ok
    assert "no vision capability" in result.content
    assert "base64" not in result.content.lower() or len(result.content) < 500


async def test_read_an_image_with_vision_returns_it_as_an_artifact(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    result = await call(READ, context(tmp_path, vision=True), path="shot.png")
    assert result.ok
    assert result.artifacts and result.artifacts[0].startswith("image/png;base64,")


def test_number_lines_pads_consistently() -> None:
    rendered = number_lines("a\nb")
    assert rendered == "    1\ta\n    2\tb"


# --------------------------------------------------------------------------- #
# write — the read-before-overwrite guard
# --------------------------------------------------------------------------- #


async def test_write_creates_a_new_file(tmp_path: Path) -> None:
    result = await call(WRITE, context(tmp_path), path="new.py", content="x = 1\n")
    assert result.ok
    assert (tmp_path / "new.py").read_text() == "x = 1\n"
    assert "wrote" in result.content


async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    result = await call(WRITE, context(tmp_path), path="a/b/c.py", content="pass\n")
    assert result.ok
    assert (tmp_path / "a/b/c.py").exists()


async def test_write_refuses_to_overwrite_an_unread_file(tmp_path: Path) -> None:
    """The single rule that prevents most destructive edits."""
    write_file(tmp_path, "existing.py", "important = True\n")
    result = await call(WRITE, context(tmp_path), path="existing.py", content="gone\n")
    assert not result.ok
    assert "has not been read in this session" in result.error
    assert "read first" in result.error
    assert (tmp_path / "existing.py").read_text() == "important = True\n", "must not have written"


async def test_write_allows_an_overwrite_after_a_read(tmp_path: Path) -> None:
    write_file(tmp_path, "existing.py", "old\n")
    ctx = context(tmp_path)
    await call(READ, ctx, path="existing.py")
    result = await call(WRITE, ctx, path="existing.py", content="new\n")
    assert result.ok
    assert "overwrote" in result.content
    assert (tmp_path / "existing.py").read_text() == "new\n"


async def test_a_write_counts_as_a_read_for_the_next_write(
    tmp_path: Path,
) -> None:
    ctx = context(tmp_path)
    await call(WRITE, ctx, path="new.py", content="first\n")
    result = await call(WRITE, ctx, path="new.py", content="second\n")
    assert result.ok, "after writing it, the model knows what is in the file"


async def test_write_reports_line_and_byte_counts(tmp_path: Path) -> None:
    result = await call(WRITE, context(tmp_path), path="a.txt", content="one\ntwo\n")
    assert "2 lines" in result.content
    assert "8 bytes" in result.content


async def test_write_refuses_a_directory_target(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    result = await call(WRITE, context(tmp_path), path="dir", content="x")
    assert not result.ok
    assert "directory" in result.error


async def test_write_requires_content(tmp_path: Path) -> None:
    result = await call(WRITE, context(tmp_path), path="a.txt")
    assert not result.ok
    assert "'content' is required" in result.error


# --------------------------------------------------------------------------- #
# edit — exact match, and ambiguity is an error
# --------------------------------------------------------------------------- #


async def read_then(tmp_path: Path, name: str, body: str) -> tuple[ToolContext, Path]:
    """Create a file, read it (satisfying the guard), and hand back ctx + path."""
    ctx = context(tmp_path)
    write_file(tmp_path, name, body)
    await call(READ, ctx, path=name)
    return ctx, tmp_path / name


async def test_edit_replaces_one_occurrence(tmp_path: Path) -> None:
    ctx, path = await read_then(tmp_path, "a.py", "timeout = 30\nretries = 3\n")
    result = await call(
        EDIT, ctx, path="a.py", old_string="timeout = 30", new_string="timeout = 60"
    )
    assert result.ok
    assert "1 replacement" in result.content
    assert path.read_text() == "timeout = 60\nretries = 3\n"


async def test_edit_refuses_when_the_match_is_ambiguous_and_says_how_many(tmp_path: Path) -> None:
    """The most important refusal in the system."""
    body = "return None\nx = 1\nreturn None\ny = 2\nreturn None\n"
    ctx, path = await read_then(tmp_path, "a.py", body)
    result = await call(EDIT, ctx, path="a.py", old_string="return None", new_string="return []")
    assert not result.ok
    assert "appears 3 times" in result.error
    assert "surrounding" in result.error, "tell the model the repair"
    assert "replace_all" in result.error
    assert path.read_text() == body, "an ambiguous edit must change nothing"


async def test_edit_replaces_all_when_asked(tmp_path: Path) -> None:
    ctx, path = await read_then(tmp_path, "a.py", "a\na\na\n")
    result = await call(EDIT, ctx, path="a.py", old_string="a", new_string="b", replace_all=True)
    assert result.ok
    assert "3 replacements" in result.content
    assert path.read_text() == "b\nb\nb\n"


async def test_edit_never_fuzzy_matches(tmp_path: Path) -> None:
    """A near miss must fail, not "helpfully" match the closest thing."""
    ctx, path = await read_then(tmp_path, "a.py", "def  f(x):\n    return x\n")
    result = await call(EDIT, ctx, path="a.py", old_string="def f(x):", new_string="def g(x):")
    assert not result.ok
    assert "not found" in result.error
    assert "whitespace" in result.error
    assert path.read_text() == "def  f(x):\n    return x\n"


async def test_edit_requires_the_file_to_have_been_read(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "x = 1\n")
    result = await call(EDIT, context(tmp_path), path="a.py", old_string="x", new_string="y")
    assert not result.ok
    assert "has not been read" in result.error


async def test_edit_rejects_identical_old_and_new(tmp_path: Path) -> None:
    ctx, _ = await read_then(tmp_path, "a.py", "x = 1\n")
    result = await call(EDIT, ctx, path="a.py", old_string="x", new_string="x")
    assert not result.ok
    assert "identical" in result.error


async def test_edit_rejects_an_empty_old_string(tmp_path: Path) -> None:
    ctx, _ = await read_then(tmp_path, "a.py", "x = 1\n")
    result = await call(EDIT, ctx, path="a.py", old_string="", new_string="y")
    assert not result.ok
    assert "empty" in result.error


async def test_edit_can_delete_by_passing_an_empty_new_string(tmp_path: Path) -> None:
    ctx, path = await read_then(tmp_path, "a.py", "keep\nDELETE ME\nkeep\n")
    result = await call(EDIT, ctx, path="a.py", old_string="DELETE ME\n", new_string="")
    assert result.ok
    assert path.read_text() == "keep\nkeep\n"


# --------------------------------------------------------------------------- #
# edit fuzz: unicode, CRLF, tabs, identical repeated blocks
# --------------------------------------------------------------------------- #

FUZZ_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "unicode-emoji",
        "status = '✅ done'\nother = 1\n",
        "'✅ done'",
        "'🚧 wip'",
    ),
    (
        "unicode-cjk",
        "label = '日本語のテキスト'\n",
        "'日本語のテキスト'",
        "'English text'",
    ),
    (
        "unicode-combining",
        "name = 'café'\n",  # e + combining acute
        "'café'",
        "'cafe'",
    ),
    (
        "unicode-rtl",
        "msg = 'שלום עולם'\n",
        "'שלום עולם'",
        "'hello world'",
    ),
    ("crlf", "a = 1\r\nb = 2\r\n", "a = 1\r\n", "a = 99\r\n"),
    ("crlf-mixed", "a = 1\r\nb = 2\n", "b = 2\n", "b = 3\n"),
    ("tabs", "def f():\n\treturn 1\n", "\treturn 1", "\treturn 2"),
    ("tabs-vs-spaces", "if x:\n\tpass\n", "\tpass", "    pass"),
    ("trailing-whitespace", "x = 1   \ny = 2\n", "x = 1   ", "x = 1"),
    ("form-feed", "a\n\x0c\nb\n", "\x0c", "#"),
    ("nul-adjacent-text", "a\\x00b\n", "\\x00", "-"),
    ("zero-width-space", "a\u200bb\n", "a\u200bb", "ab"),
    ("nbsp", "a\u00a0b\n", "\u00a0", " "),
    ("regex-metachars", "pattern = .*+?[]()\n", ".*+?[]()", "literal"),
    ("backslashes", 'path = "C:\\\\Users\\\\x"\n', "C:\\\\Users", "D:\\\\Users"),
    ("dollar-braces", "cmd = ${HOME}/bin\n", "${HOME}", "$PREFIX"),
    ("very-long-line", "x = " + "a" * 5000 + "\n", "a" * 5000, "b" * 10),
    ("no-trailing-newline", "last line without newline", "without", "with no"),
    ("only-newlines", "\n\n\n\n", "\n\n", "\n"),
)


@pytest.mark.parametrize(("label", "body", "old", "new"), FUZZ_CASES, ids=lambda v: str(v)[:24])
async def test_edit_fuzz_exact_match_holds(
    tmp_path: Path, label: str, body: str, old: str, new: str
) -> None:
    """Every case must either apply exactly, or refuse — never silently mangle."""
    path = tmp_path / f"{label}.txt"
    path.write_bytes(body.encode("utf-8"))
    ctx = context(tmp_path)
    await call(READ, ctx, path=path.name)

    expected_count = body.count(old)
    result = await call(
        EDIT, ctx, path=path.name, old_string=old, new_string=new, replace_all=expected_count > 1
    )
    # Bytes, not read_text: text mode applies universal-newline translation, which
    # would silently turn \r\n into \n and hide a renderer that rewrote line endings.
    after = path.read_bytes().decode("utf-8")

    if expected_count == 0:
        assert not result.ok, f"{label}: a non-match must fail"
        assert after == body, f"{label}: a failed edit must not change the file"
    else:
        assert result.ok, f"{label}: {result.error}"
        assert after == body.replace(old, new), f"{label}: content diverged from an exact replace"


async def test_edit_crlf_is_not_silently_normalised(tmp_path: Path) -> None:
    """Rewriting line endings would produce a diff touching every line in the file."""
    path = tmp_path / "win.txt"
    path.write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    ctx = context(tmp_path)
    await call(READ, ctx, path="win.txt")
    result = await call(EDIT, ctx, path="win.txt", old_string="b = 2", new_string="b = 22")
    assert result.ok
    assert path.read_bytes() == b"a = 1\r\nb = 22\r\nc = 3\r\n"


async def test_edit_identical_repeated_blocks_need_context_to_disambiguate(tmp_path: Path) -> None:
    """The realistic ambiguity: the same three lines twice, one of them wrong."""
    block = "    try:\n        run()\n    except Exception:\n        pass\n"
    body = f"def a():\n{block}\ndef b():\n{block}"
    ctx, path = await read_then(tmp_path, "dup.py", body)

    bare = await call(EDIT, ctx, path="dup.py", old_string=block, new_string="    run()\n")
    assert not bare.ok
    assert "appears 2 times" in bare.error

    # With the enclosing def as context it is unique, and only that one changes.
    scoped = await call(
        EDIT,
        ctx,
        path="dup.py",
        old_string=f"def b():\n{block}",
        new_string="def b():\n    run()\n",
    )
    assert scoped.ok
    after = path.read_text()
    assert after.count("except Exception") == 1
    assert "def a():\n    try:" in after


def test_apply_edit_is_pure_and_reports_the_count() -> None:
    text, count = apply_edit("a a a", "a", "b", replace_all=True)
    assert (text, count) == ("b b b", 3)


def test_apply_edit_raises_with_the_path_label_in_the_message() -> None:
    with pytest.raises(ToolError, match=r"src/x\.py"):
        apply_edit("abc", "zzz", "y", replace_all=False, path_label="src/x.py")


# --------------------------------------------------------------------------- #
# multi_edit — sequential, all-or-nothing
# --------------------------------------------------------------------------- #


async def test_multi_edit_applies_in_order_so_later_edits_see_earlier_ones(
    tmp_path: Path,
) -> None:
    ctx, path = await read_then(tmp_path, "a.py", "def old(x):\n    return old(x)\n")
    result = await call(
        MULTI,
        ctx,
        path="a.py",
        edits=[
            {"old_string": "def old(", "new_string": "def new("},
            {"old_string": "return old(x)", "new_string": "return new(x)"},
        ],
    )
    assert result.ok, result.error
    assert path.read_text() == "def new(x):\n    return new(x)\n"
    assert "2 edits" in result.content


async def test_multi_edit_is_all_or_nothing(tmp_path: Path) -> None:
    body = "a = 1\nb = 2\n"
    ctx, path = await read_then(tmp_path, "a.py", body)
    result = await call(
        MULTI,
        ctx,
        path="a.py",
        edits=[
            {"old_string": "a = 1", "new_string": "a = 11"},
            {"old_string": "NOT PRESENT", "new_string": "x"},
        ],
    )
    assert not result.ok
    assert "edits[1] failed" in result.error
    assert "no edits were applied" in result.error
    assert path.read_text() == body, "the first edit must have been rolled back"


async def test_multi_edit_counts_replace_all_within_one_entry(tmp_path: Path) -> None:
    ctx, _ = await read_then(tmp_path, "a.py", "x\nx\nx\ny\n")
    result = await call(
        MULTI,
        ctx,
        path="a.py",
        edits=[
            {"old_string": "x", "new_string": "z", "replace_all": True},
            {"old_string": "y", "new_string": "w"},
        ],
    )
    assert result.ok
    assert "4 replacements" in result.content


async def test_multi_edit_rejects_an_empty_list(tmp_path: Path) -> None:
    ctx, _ = await read_then(tmp_path, "a.py", "x\n")
    result = await call(MULTI, ctx, path="a.py", edits=[])
    assert not result.ok
    assert "nothing to do" in result.error


async def test_multi_edit_rejects_a_non_list(tmp_path: Path) -> None:
    ctx, _ = await read_then(tmp_path, "a.py", "x\n")
    result = await call(MULTI, ctx, path="a.py", edits="not a list")
    assert not result.ok
    assert "must be a list" in result.error


async def test_multi_edit_requires_the_file_to_have_been_read(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "x\n")
    result = await call(
        MULTI, context(tmp_path), path="a.py", edits=[{"old_string": "x", "new_string": "y"}]
    )
    assert not result.ok
    assert "has not been read" in result.error


# --------------------------------------------------------------------------- #
# Descriptions are prompts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", [READ, WRITE, EDIT, MULTI])
def test_every_file_tool_says_when_not_to_use_it(tool: object) -> None:
    spec = tool.spec()  # type: ignore[attr-defined]
    assert "WHEN TO USE" in spec.description
    assert "WHEN NOT TO USE" in spec.description
    assert "EXAMPLE" in spec.description


def test_the_mutating_tools_all_require_approval() -> None:
    for tool in (WRITE, EDIT, MULTI):
        assert tool.spec().requires_approval, tool.name
