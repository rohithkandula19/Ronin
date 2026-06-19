"""Coding-agent tools — the file + shell capabilities that make ``csk code``
behave like Claude Code / Cline / Aider.

Design follows the kit's core principle: reads are free, writes and shell
commands are gated. ``SENSITIVE_TOOLS`` names the ones that must pass through
an approval gate (wired in code_mode.py).

Everything is scoped to a ``root`` directory so the agent can't wander the
whole filesystem. Path traversal outside root is refused.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ronin_agent_patterns import Tool

from .repo_map import _IGNORE_DIRS as IGNORE_DIRS
from .repo_map import repo_map as _repo_map

# Tools that must be approved before they run (write + execute).
SENSITIVE_TOOLS = {"write_file", "edit_file", "multi_edit", "run_command", "run_background", "rewind"}

MAX_READ_BYTES = 100_000
MAX_LIST_ENTRIES = 500

# Directories never worth walking — keeps list/search/glob from drowning the
# context in vendored deps (a `venv/` or `node_modules/` can be tens of thousands
# of files). Shared with the repo map so the ignore policy is consistent.
_SKIP_DIRS = IGNORE_DIRS

# Junk files never worth surfacing in a listing — OS cruft and compiled
# artifacts. They only add noise when the model asks "what's in this project".
_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_SKIP_FILE_SUFFIXES = (".pyc", ".pyo")


def _is_junk_file(p: Path) -> bool:
    return p.name in _SKIP_FILES or p.suffix in _SKIP_FILE_SUFFIXES


def undo_last(undo_stack: list) -> str:
    """Restore the most recent file modification recorded on ``undo_stack``.

    Each entry is (path, prior_content | None). None means the file didn't
    exist before the edit, so undo deletes it.
    """
    if not undo_stack:
        return "nothing to undo"
    path_str, prior = undo_stack.pop()
    target = Path(path_str)
    if prior is None:
        if target.exists():
            target.unlink()
        return f"undid creation of {target.name} (deleted)"
    target.write_text(prior, encoding="utf-8")
    return f"reverted {target.name} to its previous contents"


def unified_diff(path: str, before: str, after: str) -> str:
    """Render a unified diff between two versions of a file (for approval preview)."""
    import difflib

    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    return "".join(lines) if lines else "(no change)"


def split_git_diff(diff: str) -> list[tuple[str | None, str]]:
    """Split a multi-file ``git diff`` into ``[(path, chunk), …]`` so each file's
    hunk can be syntax-highlighted by its own language. Pure."""
    import re
    out: list[tuple[str | None, str]] = []
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    for ch in chunks:
        if not ch.strip():
            continue
        m = re.search(r"^diff --git a/(?:.+?) b/(.+)$", ch, re.M)
        out.append((m.group(1).strip() if m else None, ch))
    return out


def diff_rows(diff: str) -> list[dict]:
    """Parse a unified diff into clean, renderable rows (drops the git ``--- a/``,
    ``+++ b/``, ``@@`` noise; tracks real line numbers). Pure + testable.

    Each row: ``{"kind": "add"|"del"|"ctx"|"sep", "lineno": int|None, "text": str}``.
    """
    import re
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    rows: list[dict] = []
    new_no = 0
    seen_hunk = False
    for raw in diff.splitlines():
        if raw.startswith(("--- ", "+++ ", "diff --git", "index ", "new file mode",
                           "deleted file mode", "rename ", "similarity ", "\\ No newline")):
            continue
        m = hunk_re.match(raw)
        if m:
            new_no = int(m.group(1))
            if seen_hunk:
                rows.append({"kind": "sep", "lineno": None, "text": ""})
            seen_hunk = True
            continue
        if not raw:
            continue
        sign, text = raw[0], raw[1:]
        if sign == "+":
            rows.append({"kind": "add", "lineno": new_no, "text": text})
            new_no += 1
        elif sign == "-":
            rows.append({"kind": "del", "lineno": None, "text": text})
        elif sign == " ":
            rows.append({"kind": "ctx", "lineno": new_no, "text": text})
            new_no += 1
    return rows


def _resolve(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; raise if it escapes root."""
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"path {rel!r} escapes the project root")
    return target


def build_code_tools(root: Path | str = ".", *, undo_stack: list | None = None,
                     sandbox: bool = True,
                     deny: "Callable[[Path], bool] | None" = None) -> list[Tool]:
    """Build the coding tools rooted at ``root``.

    ``undo_stack``: optional list the write/edit tools push (path, prior_content
    | None) onto before modifying a file. Pass a list and the interactive
    session can pop it to restore the previous state (`:undo`).
    ``sandbox``: when True (default) paths are confined to ``root``; when False
    (full-access mode) absolute paths and paths outside root are allowed, and
    run_command gets a longer timeout + bigger output caps.
    ``deny``: optional predicate ``deny(resolved_path) -> bool``. When it returns
    True for a resolved path, the read tools (read_file/list_files/search_files/
    glob) refuse or skip that path instead of returning its contents. Default
    None leaves every tool's behavior unchanged.
    """
    root_path = Path(root).resolve()

    def _denied(p: Path) -> bool:
        if deny is None:
            return False
        try:
            return bool(deny(p))
        except Exception:  # noqa: BLE001 - a broken predicate must fail safe (skip)
            return True

    def _resolve_path(rel: str) -> Path:
        """Resolve ``rel`` honoring the sandbox flag. In full-access mode an
        absolute path is taken as-is and containment is not enforced."""
        target = Path(rel)
        resolved = target.resolve() if target.is_absolute() else (root_path / rel).resolve()
        if sandbox and root_path != resolved and root_path not in resolved.parents:
            raise ValueError(f"path {rel!r} escapes the project root "
                             "(enable full-access mode to reach outside it)")
        return resolved

    def _display(p: Path) -> str:
        """Path shown in results: relative to root when possible (sandbox), else
        the absolute path (full-access reaching outside root)."""
        try:
            return str(p.relative_to(root_path))
        except ValueError:
            return str(p)

    def _record_undo(target: Path) -> None:
        if undo_stack is None:
            return
        prior = target.read_text(encoding="utf-8") if target.is_file() else None
        undo_stack.append((str(target), prior))

    # ---- read_file ----
    def read_file(path: str) -> str:
        target = _resolve_path(path)
        if _denied(target):
            return "refused: that path is protected"
        if not target.is_file():
            return f"ERROR: {path} is not a file"
        data = target.read_bytes()[:MAX_READ_BYTES]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"ERROR: {path} is not UTF-8 text"
        truncated = "\n…(truncated)" if target.stat().st_size > MAX_READ_BYTES else ""
        return text + truncated

    # ---- list_files ----
    # ``path`` is accepted as an alias for ``directory`` — models reach for it
    # constantly, and a bare TypeError just wastes a round-trip.
    def list_files(directory: str = ".", pattern: str = "*", path: str | None = None) -> list[str]:
        directory = path if path is not None else directory
        base = _resolve_path(directory)
        if not base.is_dir():
            return [f"ERROR: {directory} is not a directory"]
        out: list[str] = []
        for p in sorted(base.rglob(pattern)):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if _denied(p):  # skip protected paths so they never surface in a listing
                continue
            # Directories are listed too (trailing "/") so the model can see the
            # project's shape — frontend/, backend/, … — not just a flat file dump.
            if p.is_dir():
                out.append(_display(p) + "/")
            elif p.is_file() and not _is_junk_file(p):
                out.append(_display(p))
            if len(out) >= MAX_LIST_ENTRIES:
                break
        return out

    # ---- search_files (grep-like) ----
    def search_files(query: str, directory: str = ".", path: str | None = None) -> list[dict]:
        directory = path if path is not None else directory
        base = _resolve_path(directory)
        hits: list[dict] = []
        for p in base.rglob("*"):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            if _denied(p):  # never grep into a protected file
                continue
            try:
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query in line:
                        hits.append({"file": _display(p), "line": n, "text": line.strip()[:200]})
                        if len(hits) >= 100:
                            return hits
            except OSError:
                continue
        return hits

    # ---- write_file (SENSITIVE) ----
    def write_file(path: str, content: str) -> str:
        target = _resolve_path(path)
        _record_undo(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    # ---- edit_file (SENSITIVE) — Claude Code's core primitive: surgical replace ----
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        target = _resolve_path(path)
        if not target.is_file():
            return f"ERROR: {path} does not exist (use write_file to create it)"
        content = target.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return (
                f"ERROR: old_string not found in {path}. Read the file again and match "
                "the exact text including whitespace."
            )
        if count > 1:
            return (
                f"ERROR: old_string appears {count} times in {path} — it must be unique. "
                "Include more surrounding context to disambiguate."
            )
        _record_undo(target)
        target.write_text(content.replace(old_string, new_string), encoding="utf-8")
        return f"edited {path}: replaced 1 occurrence ({len(old_string)}→{len(new_string)} chars)"

    # ---- glob (find files by pattern) ----
    def glob(pattern: str, directory: str = ".", path: str | None = None) -> list[str]:
        directory = path if path is not None else directory
        base = _resolve_path(directory)
        if not base.is_dir():
            return [f"ERROR: {directory} is not a directory"]
        out: list[str] = []
        for p in sorted(base.glob(pattern)):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if _denied(p):  # skip protected paths so they never surface
                continue
            if p.is_file() and not _is_junk_file(p):
                out.append(_display(p))
            if len(out) >= MAX_LIST_ENTRIES:
                break
        return out

    # ---- multi_edit (SENSITIVE) — several surgical replaces in ONE file/approval ----
    def multi_edit(path: str, edits: list) -> str:
        target = _resolve_path(path)
        if not target.is_file():
            return f"ERROR: {path} does not exist (use write_file to create it)"
        content = target.read_text(encoding="utf-8")
        # validate every edit first (all-or-nothing)
        working = content
        for i, e in enumerate(edits):
            old, new = e.get("old_string", ""), e.get("new_string", "")
            c = working.count(old)
            if c == 0:
                return f"ERROR: edit #{i + 1}: old_string not found (after prior edits). Re-read the file."
            if c > 1:
                return f"ERROR: edit #{i + 1}: old_string appears {c} times — must be unique. Add context."
            working = working.replace(old, new, 1)
        _record_undo(target)
        target.write_text(working, encoding="utf-8")
        return f"applied {len(edits)} edit(s) to {path}"

    # ---- run_command (SENSITIVE) ----
    # Full-access mode gets a longer timeout + bigger output caps.
    _timeout = 600 if not sandbox else 120
    _out_cap, _err_cap = (40000, 20000) if not sandbox else (8000, 4000)

    def run_command(command: str) -> str:
        # Opt-in sandboxed execution: set RONIN_BACKEND=docker:<container> or
        # ssh:<user@host[:port]> to run the agent's shell INSIDE an isolated
        # backend instead of on the host. Unset/"local" → unchanged behaviour;
        # any backend error falls back to local so a turn never dies here.
        import os as _os
        _bspec = _os.environ.get("RONIN_BACKEND")
        if _bspec and _bspec.strip().lower() != "local":
            try:
                from .backends import parse_backend, run_in_backend
                code, out_s, err_s = run_in_backend(
                    command, parse_backend(_bspec), cwd=str(root_path), timeout=_timeout)
                return (f"exit={code}\n--- stdout ---\n{(out_s or '')[:_out_cap]}\n"
                        f"--- stderr ---\n{(err_s or '')[:_err_cap]}")
            except Exception:  # noqa: BLE001 - fall back to local execution
                pass
        proc = subprocess.run(
            command,
            shell=True,
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=_timeout,
        )
        out = (proc.stdout or "")[:_out_cap]
        err = (proc.stderr or "")[:_err_cap]
        return f"exit={proc.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    def _tool(name, desc, schema, handler) -> Tool:
        return Tool(name=name, description=desc, input_schema=schema, handler=handler)

    return [
        _tool("read_file", "Read a UTF-8 text file (relative to the project root).",
              {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
              read_file),
        _tool("list_files", "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.",
              {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}},
              list_files),
        _tool("search_files", "Grep for a literal string across files; returns file/line/text hits.",
              {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]},
              search_files),
        _tool("repo_map", "Relevance-RANKED code search (semantic-ish, not exact-match): give a concept or "
              "question — 'how is auth handled', 'where config is loaded' — and get the most relevant files "
              "plus their symbol outlines, ranked by BM25 over identifiers/paths/content. Use this FIRST to "
              "locate the right files in an unfamiliar repo, then read_file them. Complements search_files (exact grep).",
              {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]},
              lambda query, k=8: _repo_map(query, root=root_path, k=k)),
        _tool("glob", "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.",
              {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]},
              glob),
        _tool("multi_edit", "Apply SEVERAL surgical string replacements to ONE file in a single approved step "
              "(all-or-nothing; each old_string must be unique when applied). SENSITIVE — gated by approval.",
              {"type": "object", "properties": {
                  "path": {"type": "string"},
                  "edits": {"type": "array", "items": {"type": "object", "properties": {
                      "old_string": {"type": "string"}, "new_string": {"type": "string"}},
                      "required": ["old_string", "new_string"]}},
              }, "required": ["path", "edits"]},
              multi_edit),
        _tool("write_file", "Create a file or fully overwrite it. SENSITIVE — gated by approval. "
              "Prefer edit_file for changes to existing files.",
              {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
              write_file),
        _tool("edit_file", "Surgically replace an exact, unique string in an existing file (Claude-Code style). "
              "old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.",
              {"type": "object", "properties": {
                  "path": {"type": "string"},
                  "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."},
                  "new_string": {"type": "string", "description": "Replacement text."},
              }, "required": ["path", "old_string", "new_string"]},
              edit_file),
        _tool("run_command", "Run a shell command in the project root. SENSITIVE — gated by approval.",
              {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
              run_command),
    ]
