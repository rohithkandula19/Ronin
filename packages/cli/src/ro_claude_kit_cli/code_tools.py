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
SENSITIVE_TOOLS = {"write_file", "run_command"}

MAX_READ_BYTES = 100_000
MAX_LIST_ENTRIES = 500


def _resolve(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; raise if it escapes root."""
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"path {rel!r} escapes the project root")
    return target


def build_code_tools(root: Path | str = ".") -> list[Tool]:
    root_path = Path(root).resolve()

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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

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
        _tool("write_file", "Write (or overwrite) a file. SENSITIVE — gated by approval.",
              {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
              write_file),
        _tool("run_command", "Run a shell command in the project root. SENSITIVE — gated by approval.",
              {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
              run_command),
    ]
