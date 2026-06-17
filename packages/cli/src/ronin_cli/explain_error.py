"""Explain an error / stack trace — pure core for ``ronin explain-error``.

``ronin explain-error`` takes a stack trace or error text (as an argument or piped
stdin, like ``ronin ask``), extracts the referenced source files and line numbers
with a pure multi-language parser (Python tracebacks, Node/JS stacks, Go panics,
Rust panics), pulls the cited lines from the repo as context, and asks the
configured model for a plain-English root cause plus a concrete fix suggestion.

Read-only: it never edits files or runs commands. The trace -> file:line extraction
and the source-snippet gathering are pure and unit-tested; with no provider the
command still prints the parsed frames + cited source, so it stays useful offline.

This module is the PURE core. The typer command lives in ``main.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---- frame extraction ----------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """One source location referenced by a trace."""

    file: str
    line: int
    func: str = ""       # function/method name when the trace gives one
    language: str = ""   # "python" | "node" | "go" | "rust" (best-effort tag)


# Python:  File "path/to/x.py", line 42, in func
_PY = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\S+))?')
# Node/JS: at func (path/to/x.js:42:13)   OR   at path/to/x.js:42:13
_NODE_PAREN = re.compile(r"at\s+(.+?)\s+\(([^():]+):(\d+):(\d+)\)")
_NODE_BARE = re.compile(r"at\s+([^()\s][^()]*?):(\d+):(\d+)\s*$")
# Go:  \tpath/to/x.go:42 +0x1d   (leading tab, ".go" file, ":line")
_GO = re.compile(r"^\s+(\S+\.go):(\d+)(?:\s+\+0x[0-9a-f]+)?")
# Rust: at path/to/x.rs:42:13   OR   path/to/x.rs:42
_RUST = re.compile(r"(\S+\.rs):(\d+)(?::\d+)?")


# File extensions that pin a frame to a specific language even when the trace
# syntax is shared (Rust's "at file:line:col" looks just like Node's).
_EXT_LANG = {".rs": "rust", ".go": "go", ".py": "python"}


def _lang_for(file: str, default: str) -> str:
    """Tag a frame's language by file extension, falling back to ``default``. Pure."""
    for ext, lang in _EXT_LANG.items():
        if file.endswith(ext):
            return lang
    return default


def parse_trace(text: str) -> list[Frame]:
    """Extract source frames from a stack trace / panic. Pure.

    Recognizes Python, Node/JS, Go, and Rust formats. Frames are returned in the
    order they appear, de-duplicated on (file, line). Lines that don't look like a
    frame are ignored, so prose mixed in with a trace is fine.
    """
    frames: list[Frame] = []
    seen: set[tuple[str, int]] = set()

    def _add(file: str, line: int, func: str, language: str) -> None:
        file = file.strip()
        if not file:
            return
        key = (file, line)
        if key in seen:
            return
        seen.add(key)
        frames.append(Frame(file=file, line=line, func=func.strip(), language=language))

    for raw in text.splitlines():
        line = raw.rstrip()

        m = _PY.search(line)
        if m:
            _add(m.group(1), int(m.group(2)), m.group(3) or "", "python")
            continue

        m = _NODE_PAREN.search(line)
        if m:
            _add(m.group(2), int(m.group(3)), m.group(1), _lang_for(m.group(2), "node"))
            continue
        m = _NODE_BARE.search(line)
        if m:
            # `at <file>:line:col` is also Rust's backtrace form — tag by extension.
            _add(m.group(1), int(m.group(2)), "", _lang_for(m.group(1), "node"))
            continue

        m = _GO.match(line)
        if m:
            _add(m.group(1), int(m.group(2)), "", "go")
            continue

        m = _RUST.search(line)
        if m and (".rs:" in line):
            _add(m.group(1), int(m.group(2)), "", "rust")
            continue

    return frames


def extract_error_message(text: str) -> str:
    """Best-effort: the headline error line from a trace. Pure.

    Python puts the exception type+message on the LAST non-empty line; Node puts it
    on the FIRST; Go on the line after ``panic:``; Rust after ``thread '...' panicked``.
    We try those in turn and fall back to the first non-empty line.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    for ln in lines:
        if ln.startswith("panic:"):
            return ln[len("panic:"):].strip()
    for ln in lines:
        if "panicked at" in ln:
            return ln.strip()
    last = lines[-1]
    # A Python exception line looks like "ValueError: bad input".
    if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Warning)\b", last):
        return last
    # Node's first line is usually the message, e.g. "TypeError: x is not a function".
    first = lines[0]
    if re.match(r"^[A-Za-z_][\w.]*(Error|Exception)\b", first):
        return first
    return last


# ---- source gathering ----------------------------------------------------------


@dataclass(frozen=True)
class SourceSnippet:
    """A cited source location plus the surrounding lines pulled from disk."""

    frame: Frame
    found: bool
    snippet: str  # rendered "  41 | ...\n> 42 | ...\n  43 | ..." or "" when not found


def gather_snippets(
    frames: list[Frame],
    root: Path,
    *,
    context: int = 3,
    max_frames: int = 8,
    reader=None,
) -> list[SourceSnippet]:
    """Pull the cited lines (+/- context) for each frame from the repo. Pure-ish.

    ``reader`` is an injectable ``(Path) -> str`` for tests; by default it reads the
    file from disk. Files outside ``root`` or that can't be read yield ``found=False``
    with an empty snippet, so this never raises and stays read-only.
    """
    root = Path(root).resolve()
    if reader is None:
        def reader(p: Path) -> str:  # noqa: ANN001
            return p.read_text(encoding="utf-8", errors="replace")

    out: list[SourceSnippet] = []
    for frame in frames[:max_frames]:
        path = _resolve_in_root(frame.file, root)
        if path is None:
            out.append(SourceSnippet(frame=frame, found=False, snippet=""))
            continue
        try:
            text = reader(path)
        except (OSError, ValueError):
            out.append(SourceSnippet(frame=frame, found=False, snippet=""))
            continue
        snippet = _render_snippet(text, frame.line, context)
        out.append(SourceSnippet(frame=frame, found=bool(snippet), snippet=snippet))
    return out


def _resolve_in_root(file: str, root: Path) -> Path | None:
    """Resolve a trace's file path to something inside ``root``, or None. Pure.

    Tries the path as-is (absolute or relative to root); for absolute paths that
    already live under root we keep them. Anything that escapes root (``..`` tricks,
    unrelated absolute paths) is rejected so we never read outside the repo.
    """
    candidate = Path(file)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved == root or root in resolved.parents:
        return resolved
    return None


def _render_snippet(text: str, line: int, context: int) -> str:
    """Render the lines around ``line`` (1-based) with a caret on the target. Pure."""
    lines = text.splitlines()
    if line < 1 or line > len(lines):
        return ""
    start = max(1, line - context)
    end = min(len(lines), line + context)
    width = len(str(end))
    rendered = []
    for n in range(start, end + 1):
        marker = ">" if n == line else " "
        rendered.append(f"{marker} {str(n).rjust(width)} | {lines[n - 1]}")
    return "\n".join(rendered)


# ---- prompt assembly -----------------------------------------------------------


def build_prompt(error_message: str, snippets: list[SourceSnippet], raw_trace: str) -> str:
    """Assemble the model prompt from the parsed error + cited source. Pure."""
    parts = ["An error occurred. Explain the most likely ROOT CAUSE in plain "
             "English, then give a concrete fix suggestion (what to change and "
             "why). Be specific and brief.\n"]
    if error_message:
        parts.append(f"Error: {error_message}\n")
    cited = [s for s in snippets if s.found]
    if cited:
        parts.append("Cited source (> marks the failing line):")
        for s in cited:
            loc = f"{s.frame.file}:{s.frame.line}"
            if s.frame.func:
                loc += f"  (in {s.frame.func})"
            parts.append(f"\n--- {loc} ---\n{s.snippet}")
    parts.append("\n--- full trace ---\n" + raw_trace.strip()[:4000])
    return "\n".join(parts)


def format_frames(frames: list[Frame]) -> str:
    """Render parsed frames as plain text for the offline / header view. Pure."""
    if not frames:
        return "(no source frames recognized in the trace)"
    lines = []
    for f in frames:
        loc = f"{f.file}:{f.line}"
        tag = f"  [{f.language}]" if f.language else ""
        fn = f"  in {f.func}" if f.func else ""
        lines.append(f"  {loc}{fn}{tag}")
    return "\n".join(lines)
