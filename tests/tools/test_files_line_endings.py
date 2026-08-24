"""Writing a file back must not change its line endings.

``_read_text`` decodes bytes faithfully, so a CRLF file arrives at ``edit`` as a
string containing real ``"\\r\\n"`` pairs. Writing that string back with
``Path.write_text``'s default ``newline=None`` translates every ``"\\n"`` to
``os.linesep`` — which on Windows turns each ``"\\r\\n"`` into ``"\\r\\r\\n"``. One edit
doubles every carriage return in the file.

**This cannot be reproduced on Linux**, and that shapes the tests below. ``os.linesep``
is compiled into CPython's io layer rather than read from the ``os`` module, so
monkeypatching it changes nothing:

    with mock.patch.object(os, "linesep", "\\r\\n"):
        path.write_text("x\\ny\\n")      # still writes b"x\\ny\\n"

A behavioural test would therefore pass on CI whether or not the bug is present,
which is worse than no test. So the guard is structural: every write in the tools
layer must pass ``newline=``, checked over the AST. That also catches the real
regression — a *new* write added later without it — which a fixture-based test on one
file never would.

The round-trip test is kept anyway, because it states the intent in the form a reader
expects, and it is the one that would fail if someone changed the *reading* side.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ronin.tools.base import ToolContext
from ronin.tools.files import KEEP_LINE_ENDINGS, EditTool, MultiEditTool, WriteTool

FILES_SOURCE = Path(EditTool.__module__.replace(".", "/") + ".py")


def _write_text_calls(source: Path) -> list[ast.Call]:
    tree = ast.parse((Path("src") / source).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
    ]


def test_the_tools_layer_writes_at_least_one_file() -> None:
    # Guards the guard: an AST query that silently matches nothing would make the
    # test below pass forever, including after someone renames the call.
    assert len(_write_text_calls(FILES_SOURCE)) >= 3


def test_every_write_preserves_the_line_endings_it_was_given() -> None:
    """The structural check. A Windows-only corruption, caught from Linux."""
    offenders = [
        node.lineno
        for node in _write_text_calls(FILES_SOURCE)
        if not any(kw.arg == "newline" for kw in node.keywords)
    ]
    assert not offenders, (
        f"write_text without newline= at {FILES_SOURCE}:{offenders} — the default "
        "translates '\\n' to os.linesep, which doubles the carriage return in every "
        "CRLF line on Windows"
    )


def test_the_sentinel_means_write_exactly_what_you_were_given() -> None:
    assert KEEP_LINE_ENDINGS == ""


async def test_editing_a_crlf_file_leaves_its_line_endings_alone(tmp_path: Path) -> None:
    """Intent, stated in the obvious form. Passes on Linux either way; see the module
    docstring for why the structural test above is the one that does the work."""
    target = tmp_path / "crlf.py"
    target.write_bytes(b"one = 1\r\ntwo = 2\r\n")
    ctx = ToolContext(root=tmp_path)
    ctx.mark_read(target)

    await EditTool().run(
        {"path": str(target), "old_string": "one = 1", "new_string": "one = 11"}, ctx
    )

    assert target.read_bytes() == b"one = 11\r\ntwo = 2\r\n"
    assert target.read_bytes().count(b"\r") == 2


async def test_a_multi_edit_of_a_crlf_file_is_also_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "crlf.py"
    target.write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    ctx = ToolContext(root=tmp_path)
    ctx.mark_read(target)

    await MultiEditTool().run(
        {
            "path": str(target),
            "edits": [
                {"old_string": "a = 1", "new_string": "a = 11"},
                {"old_string": "c = 3", "new_string": "c = 33"},
            ],
        },
        ctx,
    )

    assert target.read_bytes() == b"a = 11\r\nb = 2\r\nc = 33\r\n"


async def test_an_lf_file_stays_lf(tmp_path: Path) -> None:
    # The control: `newline=""` must not stop LF files being written as LF.
    target = tmp_path / "lf.py"
    target.write_bytes(b"one = 1\ntwo = 2\n")
    ctx = ToolContext(root=tmp_path)
    ctx.mark_read(target)

    await EditTool().run(
        {"path": str(target), "old_string": "one = 1", "new_string": "one = 11"}, ctx
    )

    assert target.read_bytes() == b"one = 11\ntwo = 2\n"


async def test_write_creates_a_file_with_the_endings_it_was_handed(tmp_path: Path) -> None:
    target = tmp_path / "new.py"
    ctx = ToolContext(root=tmp_path)

    await WriteTool().run({"path": str(target), "content": "x = 1\r\ny = 2\r\n"}, ctx)

    assert target.read_bytes() == b"x = 1\r\ny = 2\r\n"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
