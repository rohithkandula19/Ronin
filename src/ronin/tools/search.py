"""glob, grep, ls — the tools that make exploration cheap enough to do first.

All three shell out to ``rg`` (ripgrep) rather than reimplementing search in Python.
That is not laziness: ripgrep already handles gitignore, binary detection, encoding
sniffing, parallel traversal and multiline matching, and a Python reimplementation
would be slower and wrong in ways nobody would notice until it mattered.

The consequence to be honest about: if ``rg`` is missing, these tools say so with an
install hint rather than silently falling back to something worse. A quiet fallback
that skips gitignored files differently is how a model concludes a symbol does not
exist when it does.

Two flags are passed that ripgrep does not default to, both found by tests:

* ``--no-require-git`` — by default ripgrep honours ``.gitignore`` only inside a git
  repository. An agent working in a checkout of a subdirectory, or a scratch tree with
  a ``.gitignore`` and no ``.git``, would then get ``build/`` and ``node_modules/`` in
  its results. Honouring the file whenever it exists is the behaviour that does not
  depend on how the directory came to be.
* ``--with-filename`` — given exactly one file, ripgrep omits the filename, so a
  single-file search returns ``12:    return None`` with no path. The model then has a
  line number and nothing to apply it to. Always printing the path costs a few tokens
  and removes a whole class of confusion.
"""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from ..core.types import ToolResult
from .base import Example, Tool, ToolContext, ToolError, optional_bool, optional_int, require_str

#: Results past this point are noise: a model that gets 500 hits should narrow the
#: query, not read the list.
MAX_MATCHES = 200
#: Seconds before we give up on a search. A repo big enough to exceed this needs a
#: narrower path argument, and hanging tells the model nothing.
SEARCH_TIMEOUT = 30.0


def _require_ripgrep() -> str:
    path = shutil.which("rg")
    if path is None:
        raise ToolError(
            "ripgrep (rg) is not installed, and these tools wrap it rather than "
            "reimplementing search. Install it (`brew install ripgrep`, "
            "`apt install ripgrep`, `cargo install ripgrep`) and try again."
        )
    return path


async def run_ripgrep(argv: Sequence[str], *, cwd: Path, timeout: float = SEARCH_TIMEOUT) -> str:
    """Run ripgrep and return stdout. Exit code 1 means "no matches", not an error."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ToolError(
            f"search timed out after {timeout:.0f}s. Narrow it with a path or glob "
            "argument — searching an entire monorepo from the root is rarely what "
            "you want."
        ) from exc

    if process.returncode not in (0, 1):
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"ripgrep failed: {detail[:500]}")
    return stdout.decode("utf-8", errors="replace")


def _strip_dot_slash(line: str) -> str:
    """Drop a leading ``./``. Cosmetic, but the model feeds these paths back to read."""
    return line[2:] if line.startswith("./") else line


def _relative_to(target: Path, root: Path) -> str:
    """``target`` as a path relative to ``root``, so match lines stay short.

    Absolute paths in every match line are the same prefix repeated hundreds of
    times, which is pure token cost for a model that already knows where it is.
    """
    try:
        relative = target.relative_to(root)
    except ValueError:
        return str(target)
    return str(relative) if str(relative) != "." else "."


def _cap(lines: Sequence[str], *, what: str) -> tuple[list[str], str]:
    """Trim to :data:`MAX_MATCHES`, returning the note explaining what was cut."""
    if len(lines) <= MAX_MATCHES:
        return list(lines), ""
    remaining = len(lines) - MAX_MATCHES
    return (
        list(lines[:MAX_MATCHES]),
        f"\n…[{remaining} more {what} not shown. Narrow the search rather than "
        "paging through this.]",
    )


class GlobTool(Tool):
    name = "glob"
    summary = "Find files by name pattern, newest first."
    when_to_use = (
        "You know roughly what a file is called but not where it is: "
        '`glob(pattern="**/test_*.py")`. Newest-first ordering means the files '
        "someone touched recently — usually the relevant ones — come up top."
    )
    when_not_to_use = (
        "Not for searching file *contents* — that is grep. Not for listing one "
        "directory you already know the path of — that is ls, and it is cheaper."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": 'Glob such as "**/*.py" or "src/**/test_*.py".',
            },
            "path": {"type": "string", "description": "Directory to search. Defaults to root."},
        },
        "required": ["pattern"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='glob(pattern="**/*.toml")',
            result="pyproject.toml\nexamples/models.toml\n(2 files)",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = require_str(args, "pattern")
        base = ctx.resolve(args["path"]) if args.get("path") else ctx.root
        if not base.is_dir():
            raise ToolError(f"{base} is not a directory")

        rg = _require_ripgrep()
        stdout = await run_ripgrep(
            [rg, "--files", "--no-require-git", "--glob", pattern, "."], cwd=base
        )
        names = [_strip_dot_slash(line) for line in stdout.split("\n") if line.strip()]
        if not names:
            return ToolResult(
                ok=True,
                content=(
                    f"no files match {pattern!r} under {base}. Check the pattern — "
                    '"*.py" matches only the top level, "**/*.py" recurses.'
                ),
            )

        # Newest first. Stat failures are dropped rather than fatal: a file that
        # vanished between listing and stat is not worth failing a search over.
        def mtime(name: str) -> float:
            try:
                return (base / name).stat().st_mtime
            except OSError:
                return 0.0

        names.sort(key=mtime, reverse=True)
        shown, note = _cap(names, what="files")
        return ToolResult(
            ok=True,
            content="\n".join(shown) + f"\n({len(names)} files, newest first)" + note,
        )


class GrepTool(Tool):
    name = "grep"
    summary = "Search file contents with a regular expression."
    when_to_use = (
        "Finding where something is defined or used, which is most of exploring an "
        "unfamiliar codebase. Start with mode='files' to see which files are "
        "involved, then mode='content' with -C for the ones that matter."
    )
    when_not_to_use = (
        "Do not call grep through bash — this wraps ripgrep, respects gitignore, and "
        "returns capped output, none of which `bash grep -r` does. Do not use it to "
        "find files by *name*; that is glob."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression (Rust regex)."},
            "path": {"type": "string", "description": "File or directory to search."},
            "glob": {"type": "string", "description": 'Restrict to matching files, e.g. "*.py".'},
            "type": {"type": "string", "description": 'File type, e.g. "py", "ts", "rust".'},
            "mode": {
                "type": "string",
                "enum": ["content", "files", "count"],
                "description": "content = matching lines, files = paths, count = per-file counts.",
            },
            "-A": {"type": "integer", "description": "Lines of trailing context."},
            "-B": {"type": "integer", "description": "Lines of leading context."},
            "-C": {"type": "integer", "description": "Lines of context on both sides."},
            "-i": {"type": "boolean", "description": "Case-insensitive."},
            "-n": {"type": "boolean", "description": "Show line numbers. Default true."},
            "multiline": {"type": "boolean", "description": "Let the pattern span lines."},
        },
        "required": ["pattern"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='grep(pattern="class \\\\w+Client", type="py", mode="files")',
            result="src/ronin/providers/anthropic.py\nsrc/ronin/providers/openai_compat.py",
        ),
        Example(
            call='grep(pattern="TODO", glob="*.py", mode="content", **{"-C": 2})',
            result="src/x.py:12:    # TODO: fix\n(with two lines of context each side)",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = require_str(args, "pattern")
        mode = args.get("mode", "content")
        if mode not in ("content", "files", "count"):
            raise ToolError(f"mode must be content, files or count — got {mode!r}")

        target = ctx.resolve(args["path"]) if args.get("path") else ctx.root
        rg = _require_ripgrep()
        # See the module docstring for why these two are not ripgrep's defaults.
        argv = [rg, "--color", "never", "--no-require-git", "--with-filename"]

        if mode == "files":
            argv.append("--files-with-matches")
        elif mode == "count":
            argv.append("--count-matches")
        else:
            if optional_bool(args, "-n", default=True):
                argv.append("--line-number")
            for flag, key in (("--after-context", "-A"), ("--before-context", "-B")):
                value = optional_int(args, key)
                if value is not None:
                    argv += [flag, str(value)]
            context = optional_int(args, "-C")
            if context is not None:
                argv += ["--context", str(context)]

        if optional_bool(args, "-i"):
            argv.append("--ignore-case")
        if optional_bool(args, "multiline"):
            argv += ["--multiline", "--multiline-dotall"]
        if args.get("glob"):
            argv += ["--glob", require_str(args, "glob")]
        if args.get("type"):
            argv += ["--type", require_str(args, "type")]

        argv += ["--regexp", pattern, "--", _relative_to(target, ctx.root)]
        stdout = await run_ripgrep(argv, cwd=ctx.root)

        lines = [_strip_dot_slash(line) for line in stdout.split("\n") if line.strip()]
        if not lines:
            hint = " Try -i for case-insensitivity, or a shorter pattern."
            return ToolResult(ok=True, content=f"no matches for {pattern!r} in {target}.{hint}")

        shown, note = _cap(lines, what="matches")
        label = {"files": "files", "count": "files with counts", "content": "matching lines"}[mode]
        return ToolResult(
            ok=True,
            content="\n".join(shown) + f"\n({len(lines)} {label})" + note,
        )


class LsTool(Tool):
    name = "ls"
    summary = "List the contents of one directory."
    when_to_use = (
        "Orienting in a directory you have a path for — seeing what is at the root "
        "of a repo, or what is inside a package."
    )
    when_not_to_use = (
        "Not for searching. If you are looking for a file whose location you do not "
        "know, glob finds it in one call instead of several ls calls walking down. "
        "Do not use ls to explore a whole tree; it lists one level on purpose."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list."},
            "ignore": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Glob patterns to skip, e.g. ["*.pyc", "__pycache__"].',
            },
        },
        "required": ["path"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='ls(path="src/ronin", ignore=["__pycache__"])',
            result="core/\nproviders/\ntools/\n__init__.py\ndemo.py",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        target = ctx.resolve(require_str(args, "path"))
        if not target.exists():
            raise ToolError(f"{target} does not exist")
        if not target.is_dir():
            raise ToolError(f"{target} is a file, not a directory. Use read to see its contents.")

        raw_ignore = args.get("ignore") or ()
        if isinstance(raw_ignore, str) or not isinstance(raw_ignore, Sequence):
            raise ToolError("'ignore' must be a list of glob patterns")
        patterns = [str(item) for item in raw_ignore]

        entries: list[str] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if any(child.match(pattern) for pattern in patterns):
                continue
            entries.append(f"{child.name}/" if child.is_dir() else child.name)

        if not entries:
            return ToolResult(ok=True, content=f"{target} is empty (or everything was ignored).")
        shown, note = _cap(entries, what="entries")
        return ToolResult(
            ok=True, content="\n".join(shown) + f"\n({len(entries)} entries)" + note
        )
