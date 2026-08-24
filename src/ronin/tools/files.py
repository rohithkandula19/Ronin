"""read, write, edit, multi_edit — the four tools that touch the user's work.

``edit`` is the most important tool in the system, and the reason is that it is the
one that can silently do the wrong thing. A search-and-replace that matches in two
places and picks one has corrupted a file in a way the model will confidently report
as success. So:

* **Exact string match only.** No fuzzy matching, no whitespace normalization, no
  "did you mean". If the model's ``old_string`` is not in the file byte for byte, the
  edit fails and says so.
* **Ambiguity is an error, not a coin flip.** More than one match with
  ``replace_all=False`` fails, and the error says *how many* matches there were and
  what to do about it — add surrounding context — because that is a repair the model
  can actually make.

``write`` carries the other load-bearing rule: it refuses to overwrite a file that
has not been read in this session. One rule, and it prevents most destructive edits
— a model that has read the file knows what it is throwing away, and a model that
has not is guessing.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from ..core.types import DangerLevel, ToolResult
from .base import Example, Tool, ToolContext, ToolError, optional_bool, optional_int, require_str

#: Line cap for one ``read``. Beyond this the model is not reading, it is filling
#: its context. The marker tells it how to get the rest.
MAX_READ_LINES = 2000
#: Characters per line before we clip. One minified bundle line can be megabytes.
MAX_LINE_CHARS = 2000

#: Extensions we hand back as image blocks rather than text.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

#: Extensions that are definitely not text, checked before the content sniff so the
#: error message can name the format instead of saying "binary".
KNOWN_BINARY_SUFFIXES: Mapping[str, str] = {
    ".pdf": "a PDF",
    ".zip": "a zip archive",
    ".gz": "a gzip archive",
    ".tar": "a tar archive",
    ".whl": "a Python wheel",
    ".so": "a shared library",
    ".dylib": "a shared library",
    ".dll": "a shared library",
    ".pyc": "compiled Python bytecode",
    ".class": "compiled Java bytecode",
    ".wasm": "a WebAssembly module",
    ".mp4": "a video",
    ".mp3": "an audio file",
    ".woff": "a font",
    ".woff2": "a font",
    ".ttf": "a font",
    ".sqlite": "a SQLite database",
    ".db": "a database file",
}


def _read_text(path: Path) -> str:
    """Read a file as text, or raise a :class:`ToolError` explaining why not."""
    return _read_bytes_and_text(path)[1]


#: Passed to every write. ``newline=None`` — the default — translates ``"\n"`` to
#: ``os.linesep`` on the way out, so on Windows a file that legitimately contains
#: ``"\r\n"`` (which ``_read_text`` decodes faithfully, byte for byte) is written back
#: as ``"\r\r\n"``. One ``edit`` doubles every carriage return in the file. ``""``
#: means "write the string exactly as it is", which is the only correct answer when
#: the string came from reading that same file.
KEEP_LINE_ENDINGS = ""


def _read_bytes_and_text(path: Path) -> tuple[bytes, str]:
    """The same read, handing back the raw bytes alongside the decoded text.

    ``read`` needs both: the text to show, and the bytes to hand the gate so the
    stale-edit digest is of what the model actually saw rather than of a second read
    that may already have raced. Re-encoding the text is not a substitute — see
    :meth:`~ronin.tools.base.ToolContext.mark_read`.
    """
    if not path.exists():
        raise ToolError(
            f"{path} does not exist. Check the path, or use glob/ls to find the file you meant."
        )
    if path.is_dir():
        raise ToolError(f"{path} is a directory. Use ls to list it, or read a file inside it.")

    described = KNOWN_BINARY_SUFFIXES.get(path.suffix.lower())
    if described:
        raise ToolError(
            f"{path.name} is {described}, not text, so there is nothing useful to "
            "show you. If you need something from it, run a command that extracts "
            "text (for example `pdftotext`) with bash and read the output."
        )

    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ToolError(
            f"{path.name} contains null bytes in its first 8 KB, so it is binary "
            "rather than text. Reading it would fill your context with noise."
        )
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError:
        # Latin-1 always decodes. Better a mojibake read the model can reason about
        # than a refusal on a file that is 99% ASCII with one stray byte.
        return raw, raw.decode("latin-1")


def number_lines(text: str, *, start: int = 1) -> str:
    """Render text in ``cat -n`` format: right-aligned line number, tab, content.

    The model needs line numbers to write a useful ``edit``, and it needs them in a
    format it will not mistake for file content — hence the tab, which almost never
    appears immediately after a bare integer in real source.

    The gutter is exactly as wide as the largest number it has to hold, and no wider.
    It used to have a floor of five, which cost a flat six characters on every line of
    every read — measured at 14-15% of a source file, ~6,400 tokens across four of
    this repo's own modules — and bought nothing: the numbers in one read still line
    up, because the width is computed from that read's own last line. Padding to five
    only aligns reads against *each other*, which nothing compares.
    """
    lines = text.split("\n")
    width = len(str(start + len(lines) - 1))
    out: list[str] = []
    for offset, line in enumerate(lines):
        clipped = line
        if len(clipped) > MAX_LINE_CHARS:
            cut = len(clipped) - MAX_LINE_CHARS
            clipped = f"{clipped[:MAX_LINE_CHARS]}…[{cut} more chars on this line]"
        out.append(f"{start + offset:>{width}}\t{clipped}")
    return "\n".join(out)


class ReadTool(Tool):
    name = "read"
    summary = "Read a file from disk and return its contents with line numbers."
    when_to_use = (
        "Before editing anything — write and edit both need to know what is already "
        "there, and write refuses to overwrite a file you have not read. Also for "
        "reading config, tests, or any file a grep hit pointed you at."
    )
    when_not_to_use = (
        "Do not read a file you already read this session unless you changed it — a "
        "repeat read of an unchanged file is answered with a short note instead of "
        "the contents, so it buys nothing. Do "
        "not read a whole directory tree hoping to find something; use glob or grep "
        "to narrow first, then read the two or three files that matter. Do not use "
        "read to check whether a file exists — ls or glob answers that far cheaper. "
        "Do not read a large file whole when you already know which part you need: "
        "grep for the symbol, then read with offset and limit around the hit. A "
        "2000-line module costs roughly 6000 tokens of context that stays spent for "
        "the rest of the session."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to read, absolute or relative."},
            "offset": {
                "type": "integer",
                "description": (
                    "1-indexed line to start at. Pair with limit to read a window "
                    "instead of a whole file — the cheapest way to look at one "
                    "function in a large module."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "How many lines to read. A window of 60-120 lines around a grep "
                    f"hit is usually enough; the default reads up to {MAX_READ_LINES}."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Re-send the contents even if you have already read this file "
                    "unchanged. A repeat read is normally answered with a short note "
                    "instead of the file, because the contents are still above in "
                    "the conversation. Set this when they are not — after a "
                    "compaction, or when you genuinely cannot find them."
                ),
            },
        },
        "required": ["path"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='read(path="src/main.py")',
            result="1\timport sys\n2\t\n3\tdef main() -> None:",
        ),
        Example(
            call='read(path="logs/big.log", offset=4000, limit=100)',
            result="lines 4000-4099 of a 50,000-line file",
        ),
        Example(
            call='read(path="src/main.py")',
            result="src/main.py is unchanged since you read it … call read again "
            "with force=true and the file will be re-sent",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(require_str(args, "path"))
        offset = optional_int(args, "offset")
        limit = optional_int(args, "limit")

        if path.suffix.lower() in IMAGE_SUFFIXES:
            return self._read_image(path, ctx)

        raw, text = _read_bytes_and_text(path)
        # Completeness is settled below, once the cap is known; record the bytes now
        # so the digest is of what was read even if the render raises.
        ctx.mark_read(path, raw)

        if text == "":
            return ToolResult(
                ok=True,
                content=f"{path.name} exists but is empty (0 bytes).",
            )

        lines = text.split("\n")
        start = 1 if offset is None else max(offset, 1)
        if start > len(lines):
            raise ToolError(
                f"offset {start} is past the end of {path.name}, which has {len(lines)} lines."
            )
        window = lines[start - 1 :]
        requested = limit if limit is not None else MAX_READ_LINES
        capped = min(requested, MAX_READ_LINES)
        shown, remaining = window[:capped], len(window) - capped

        body = number_lines("\n".join(shown), start=start)
        if remaining > 0:
            # The model is getting a prefix, not the file. Re-stamp the handoff so the
            # stale-edit record cannot later stand in for content it never carried.
            ctx.mark_read(path, raw, complete=False)
            next_offset = start + capped
            body += (
                f"\n…[truncated: {remaining} of {len(lines)} lines not shown. "
                f"Read the rest with offset={next_offset}]"
            )
        return ToolResult(ok=True, content=body, tokens_estimate=len(body) // 4)

    def _read_image(self, path: Path, ctx: ToolContext) -> ToolResult:
        if not path.exists():
            raise ToolError(f"{path} does not exist.")
        ctx.mark_read(path)
        raw = path.read_bytes()
        if not ctx.vision:
            # Saying "this model cannot see images" is far more useful than
            # returning base64 the model will try to interpret as text.
            return ToolResult(
                ok=True,
                content=(
                    f"{path.name} is an image ({len(raw):,} bytes, {path.suffix[1:]}). "
                    "This model has no vision capability, so the image cannot be "
                    "shown. Describe what you need from it and use bash with an "
                    "image tool if one is available."
                ),
            )
        encoded = base64.b64encode(raw).decode("ascii")
        return ToolResult(
            ok=True,
            content=f"[image: {path.name}, {len(raw):,} bytes, base64 in artifacts]",
            artifacts=(f"image/{path.suffix[1:]};base64,{encoded}",),
        )


class WriteTool(Tool):
    name = "write"
    summary = "Write a file, replacing its entire contents."
    when_to_use = (
        "Creating a new file, or replacing one small enough that a full rewrite is "
        "clearer than a series of edits."
    )
    when_not_to_use = (
        "Do not use write to change part of an existing file — use edit, which "
        "cannot accidentally drop the parts you did not mention. Writing a file you "
        "have not read this session will be refused, because a full overwrite of "
        "something you have not seen destroys work you do not know about."
    )
    danger_level = DangerLevel.MUTATING
    requires_approval = True
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to write."},
            "content": {"type": "string", "description": "The complete new contents."},
        },
        "required": ["path", "content"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='write(path="tests/test_new.py", content="def test_x():\\n    assert True\\n")',
            result="wrote tests/test_new.py (2 lines, 34 bytes)",
        ),
        Example(
            call='write(path="src/existing.py", content="...")',
            result="REFUSED: src/existing.py exists and has not been read this session",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(require_str(args, "path"))
        content = require_str(args, "content")

        if path.is_dir():
            raise ToolError(f"{path} is a directory, not a file.")
        if path.exists() and not ctx.has_been_read(path):
            raise ToolError(
                f"{path} already exists and has not been read in this session. "
                "Writing would replace its entire contents with something chosen "
                "without seeing what is there. Call read first — then either write, "
                "or use edit to change only the part you mean to."
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content, encoding="utf-8", newline=KEEP_LINE_ENDINGS)
        # A write is also a read: the model now knows exactly what is in the file,
        # so a follow-up edit or write should not be blocked by the guard.
        ctx.mark_read(path)
        verb = "overwrote" if existed else "wrote"
        lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        return ToolResult(
            ok=True,
            content=f"{verb} {path} ({lines} lines, {len(content.encode()):,} bytes)",
            artifacts=(str(path),),
        )


def apply_edit(
    text: str, old: str, new: str, *, replace_all: bool, path_label: str = "the file"
) -> tuple[str, int]:
    """Apply one exact-match replacement. Returns ``(new_text, replacements)``.

    Pure, so the fuzz tests can hammer it directly without touching a filesystem.
    Every failure mode raises :class:`ToolError` with a message that tells the model
    what to change about its next attempt.
    """
    if old == new:
        raise ToolError(
            "old_string and new_string are identical, so this edit would change "
            "nothing. If you meant to delete the text, pass an empty new_string."
        )
    if not old:
        raise ToolError(
            "old_string is empty. An empty match would insert at every position; "
            "give the exact text to replace, or use write to create the file."
        )

    count = text.count(old)
    if count == 0:
        raise ToolError(
            f"old_string was not found in {path_label}. The match is exact — "
            "whitespace, indentation, quote style and line endings all count. Read "
            "the file again and copy the text you want to replace verbatim from "
            "what read returned (without the line-number prefix)."
        )
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string appears {count} times in {path_label}, so replacing it "
            "would be ambiguous. Either extend old_string with the surrounding "
            "lines until it is unique, or pass replace_all=true if you genuinely "
            f"mean to change all {count} occurrences."
        )
    replacements = count if replace_all else 1
    return text.replace(old, new, -1 if replace_all else 1), replacements


class EditTool(Tool):
    name = "edit"
    summary = "Replace an exact string in a file with another."
    when_to_use = (
        "Any change to an existing file. This is the default editing tool: it "
        "changes exactly what you name and provably leaves the rest alone."
    )
    when_not_to_use = (
        "Do not use it to create a file — use write. Do not guess at old_string; if "
        "the match fails, read the file and copy the text verbatim rather than "
        "trying a near-miss again. For several changes to one file, use multi_edit "
        "so they are all-or-nothing."
    )
    danger_level = DangerLevel.MUTATING
    requires_approval = True
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_string": {
                "type": "string",
                "description": "Exact text to replace, copied verbatim from the file.",
            },
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence. Default false.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='edit(path="src/app.py", old_string="timeout=30", new_string="timeout=60")',
            result="edited src/app.py (1 replacement)",
        ),
        Example(
            call='edit(path="src/app.py", old_string="return None", new_string="return []")',
            result=(
                "REFUSED: old_string appears 4 times — extend it with surrounding "
                "lines, or pass replace_all=true"
            ),
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(require_str(args, "path"))
        old = require_str(args, "old_string")
        new = require_str(args, "new_string")
        replace_all = optional_bool(args, "replace_all")

        text = _read_text(path)
        if not ctx.has_been_read(path):
            raise ToolError(
                f"{path} has not been read in this session. Call read first — an "
                "edit written without seeing the file is a guess."
            )

        updated, replacements = apply_edit(
            text, old, new, replace_all=replace_all, path_label=str(path)
        )
        path.write_text(updated, encoding="utf-8", newline=KEEP_LINE_ENDINGS)
        ctx.mark_read(path)
        return ToolResult(
            ok=True,
            content=(
                f"edited {path} ({replacements} replacement{'s' if replacements != 1 else ''})"
            ),
            artifacts=(str(path),),
        )


class MultiEditTool(Tool):
    name = "multi_edit"
    summary = "Apply several exact-string edits to one file, all or nothing."
    when_to_use = (
        "Two or more changes to the same file. Edits apply in order to an in-memory "
        "buffer, so a later edit sees the result of an earlier one — which is what "
        "you want when renaming a symbol and then changing a line that used it."
    )
    when_not_to_use = (
        "Not for a single change (use edit) and not across several files (call it "
        "once per file). If any edit in the list fails, none of them are written, so "
        "do not use it to make a best-effort batch of independent changes."
    )
    danger_level = DangerLevel.MUTATING
    requires_approval = True
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "edits": {
                "type": "array",
                "description": "Edits applied in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["path", "edits"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call=(
                'multi_edit(path="src/app.py", edits=[\n'
                '  {"old_string": "def old_name(", "new_string": "def new_name("},\n'
                '  {"old_string": "old_name(x)", "new_string": "new_name(x)",'
                ' "replace_all": true}\n])'
            ),
            result="edited src/app.py (2 edits, 4 replacements)",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(require_str(args, "path"))
        raw_edits = args.get("edits")
        if not isinstance(raw_edits, Sequence) or isinstance(raw_edits, str):
            raise ToolError("'edits' must be a list of {old_string, new_string} objects")
        if not raw_edits:
            raise ToolError("'edits' is empty — there is nothing to do")

        text = _read_text(path)
        if not ctx.has_been_read(path):
            raise ToolError(
                f"{path} has not been read in this session. Call read first — edits "
                "written without seeing the file are guesses."
            )

        buffer = text
        total = 0
        for index, entry in enumerate(raw_edits):
            if not isinstance(entry, Mapping):
                raise ToolError(f"edits[{index}] must be an object, got {type(entry).__name__}")
            old = require_str(entry, "old_string")
            new = require_str(entry, "new_string")
            replace_all = optional_bool(entry, "replace_all")
            try:
                buffer, replacements = apply_edit(
                    buffer, old, new, replace_all=replace_all, path_label=str(path)
                )
            except ToolError as exc:
                # All-or-nothing: nothing is written, and the message names which
                # edit failed so the model fixes that one rather than resending all.
                raise ToolError(
                    f"edits[{index}] failed, so no edits were applied to {path}: {exc}"
                ) from exc
            total += replacements

        path.write_text(buffer, encoding="utf-8", newline=KEEP_LINE_ENDINGS)
        ctx.mark_read(path)
        return ToolResult(
            ok=True,
            content=f"edited {path} ({len(raw_edits)} edits, {total} replacements)",
            artifacts=(str(path),),
        )
