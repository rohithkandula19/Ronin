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

from ro_claude_kit_agent_patterns import Tool

# Tools that must be approved before they run (write + execute).
SENSITIVE_TOOLS = {"write_file", "edit_file", "multi_edit", "run_command"}

MAX_READ_BYTES = 100_000
MAX_LIST_ENTRIES = 500


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


def _resolve(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; raise if it escapes root."""
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"path {rel!r} escapes the project root")
    return target


def build_code_tools(root: Path | str = ".", *, undo_stack: list | None = None) -> list[Tool]:
    """Build the coding tools rooted at ``root``.

    ``undo_stack``: optional list the write/edit tools push (path, prior_content
    | None) onto before modifying a file. Pass a list and the interactive
    session can pop it to restore the previous state (`:undo`).
    """
    root_path = Path(root).resolve()

    def _record_undo(target: Path) -> None:
        if undo_stack is None:
            return
        prior = target.read_text(encoding="utf-8") if target.is_file() else None
        undo_stack.append((str(target), prior))

    # ---- read_file ----
    def read_file(path: str) -> str:
        target = _resolve(root_path, path)
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
    def list_files(directory: str = ".", pattern: str = "*") -> list[str]:
        base = _resolve(root_path, directory)
        if not base.is_dir():
            return [f"ERROR: {directory} is not a directory"]
        out: list[str] = []
        for p in sorted(base.rglob(pattern)):
            if any(part in {".git", "node_modules", ".venv", "__pycache__", ".next"} for part in p.parts):
                continue
            if p.is_file():
                out.append(str(p.relative_to(root_path)))
            if len(out) >= MAX_LIST_ENTRIES:
                break
        return out

    # ---- search_files (grep-like) ----
    def search_files(query: str, directory: str = ".") -> list[dict]:
        base = _resolve(root_path, directory)
        hits: list[dict] = []
        for p in base.rglob("*"):
            if any(part in {".git", "node_modules", ".venv", "__pycache__", ".next"} for part in p.parts):
                continue
            if not p.is_file():
                continue
            try:
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query in line:
                        hits.append({"file": str(p.relative_to(root_path)), "line": n, "text": line.strip()[:200]})
                        if len(hits) >= 100:
                            return hits
            except OSError:
                continue
        return hits

    # ---- write_file (SENSITIVE) ----
    def write_file(path: str, content: str) -> str:
        target = _resolve(root_path, path)
        _record_undo(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    # ---- edit_file (SENSITIVE) — Claude Code's core primitive: surgical replace ----
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        target = _resolve(root_path, path)
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
    def glob(pattern: str, directory: str = ".") -> list[str]:
        base = _resolve(root_path, directory)
        if not base.is_dir():
            return [f"ERROR: {directory} is not a directory"]
        out: list[str] = []
        for p in sorted(base.glob(pattern)):
            if any(part in {".git", "node_modules", ".venv", "__pycache__", ".next"} for part in p.parts):
                continue
            if p.is_file():
                out.append(str(p.relative_to(root_path)))
            if len(out) >= MAX_LIST_ENTRIES:
                break
        return out

    # ---- multi_edit (SENSITIVE) — several surgical replaces in ONE file/approval ----
    def multi_edit(path: str, edits: list) -> str:
        target = _resolve(root_path, path)
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
    def run_command(command: str) -> str:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (proc.stdout or "")[:8000]
        err = (proc.stderr or "")[:4000]
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
