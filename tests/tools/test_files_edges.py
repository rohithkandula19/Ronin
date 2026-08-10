"""Two refusals in `files.py` that had no test, both about answering honestly.

`test_files.py` is thorough on the rules that matter most — the write-before-read
guard, exact-match editing, the ambiguity refusal with its match count and
fix-instruction, unicode/CRLF/tab fuzzing, identical repeated blocks. What was left
were two error paths where the alternative to a clear refusal is not a crash but
something worse: a confusing answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import call, context, write_file

from ronin.tools import MultiEditTool, ReadTool

READ = ReadTool()
MULTI = MultiEditTool()


async def test_reading_an_image_that_does_not_exist_says_so(tmp_path: Path) -> None:
    """`read` dispatches on the file *suffix* before it checks existence, so a
    missing `.png` goes down the image path rather than the text one.

    Without this guard the next line is `path.read_bytes()`, and the model gets a
    raw `FileNotFoundError` traceback across the boundary instead of the sentence
    it gets for every other missing file. The tool contract is a value, not an
    exception.
    """
    result = await call(READ, context(tmp_path), path="nope.png")
    assert not result.ok
    assert "does not exist" in (result.error or "")


async def test_a_missing_text_file_and_a_missing_image_refuse_the_same_way(
    tmp_path: Path,
) -> None:
    """Consistency is the point: the model should not have to learn that a missing
    file reports differently depending on its extension."""
    text = await call(READ, context(tmp_path), path="nope.py")
    image = await call(READ, context(tmp_path), path="nope.png")
    assert not text.ok and not image.ok
    assert "does not exist" in (text.error or "")
    assert "does not exist" in (image.error or "")


@pytest.mark.parametrize(
    "entry",
    ["not an object", 42, None, ["old", "new"]],
    ids=["string", "int", "null", "list"],
)
async def test_multi_edit_names_the_index_of_a_malformed_edit(
    tmp_path: Path, entry: object
) -> None:
    """`edits` is a list of objects, and a model that gets that wrong needs to know
    *which* element was wrong.

    `multi_edit` is all-or-nothing, so the whole batch is rejected — which means a
    message saying only "bad edits" leaves the model re-guessing every entry. The
    index and the received type are the two facts that make this repairable.
    """
    write_file(tmp_path, "a.py", "x = 1\n")
    ctx = context(tmp_path)
    await call(READ, ctx, path="a.py")

    result = await call(
        MULTI,
        ctx,
        path="a.py",
        edits=[{"old_string": "x = 1", "new_string": "x = 2"}, entry],
    )
    assert not result.ok
    assert "edits[1]" in (result.error or ""), result.error
    assert (tmp_path / "a.py").read_text() == "x = 1\n", "a rejected batch must change nothing"


async def test_a_malformed_edit_at_the_first_index_is_named_too(tmp_path: Path) -> None:
    """Guards against an off-by-one in the message: index 0 must report as 0."""
    write_file(tmp_path, "a.py", "x = 1\n")
    ctx = context(tmp_path)
    await call(READ, ctx, path="a.py")

    result = await call(MULTI, ctx, path="a.py", edits=["nonsense"])
    assert not result.ok
    assert "edits[0]" in (result.error or "")
