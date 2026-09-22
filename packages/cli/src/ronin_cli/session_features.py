"""Coding-agent behaviors, not extra commands.

Plan mode, secret-file refusal, permission globs, a write budget, an edit
journal with rewind, a checkpoint of the previous bytes, project-instruction
loading, a todo list, shell danger checks, and transcript compaction.
"""
from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path

_SECRET_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials.json"}
_SHELL_DENY = (
    re.compile(r"\brm\s+(-[^\s]*f[^\s]*\s+|-[^\s]*r[^\s]*\s+)+\s*/\s*$"),
    re.compile(r"\brm\s+-rf\s+/\b"),
    re.compile(r"(curl|wget)\b[^|\n]*\|\s*(ba)?sh\b"),
    re.compile(r":\(\)\s*\{\s*:\|:&?\s*\};:"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if=.*\bof=/dev/"),
)


class SessionFeatures:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.dir = self.root / ".ronin"

    def _state_path(self) -> Path:
        return self.dir / "session.json"

    def _load(self) -> dict:
        path = self._state_path()
        if not path.is_file():
            return {"mode": "default", "writes": 0, "budget": 200}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"mode": "default", "writes": 0, "budget": 200}

    def _save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state), encoding="utf-8")

    def mode(self) -> str:
        return str(self._load().get("mode") or "default")

    def set_mode(self, mode: str) -> str:
        if mode not in {"default", "plan"}:
            raise ValueError("mode must be default or plan")
        state = self._load()
        state["mode"] = mode
        self._save(state)
        return mode

    def budget(self) -> int:
        raw = self._load().get("budget")
        return 200 if raw is None else int(raw)

    def set_budget(self, limit: int) -> int:
        if limit < 0:
            raise ValueError("budget must be >= 0")
        state = self._load()
        state["budget"] = limit
        self._save(state)
        return limit

    def writes(self) -> int:
        return int(self._load().get("writes") or 0)

    def deny_globs(self) -> list[str]:
        path = self.dir / "permissions.txt"
        if not path.is_file():
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

    def gate_write(self, path: Path) -> str | None:
        name = path.name
        if name in _SECRET_NAMES or name.endswith(".pem") or name.endswith(".key"):
            return f"refused: {name} is a secret file and cannot be written"
        if self.mode() == "plan":
            return "refused: plan mode is on, so file writes are blocked"
        try:
            rel = str(path.resolve().relative_to(self.root))
        except ValueError:
            rel = path.name
        for pattern in self.deny_globs():
            if fnmatch(rel, pattern) or fnmatch(path.name, pattern):
                return f"refused: {rel} matches permission rule {pattern}"
        if self.writes() >= self.budget():
            return "refused: write budget for this session is used up"
        return None

    def gate_shell(self, command: str) -> str | None:
        for pattern in _SHELL_DENY:
            if pattern.search(command):
                return "refused: that shell command is destructive"
        return None

    def note_write(self, path: Path, prior: str | None) -> None:
        state = self._load()
        state["writes"] = int(state.get("writes") or 0) + 1
        self._save(state)
        self.dir.mkdir(parents=True, exist_ok=True)
        journal = self.dir / "journal.jsonl"
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": str(path), "prior": prior}) + "\n")
        if prior is not None:
            bucket = self.dir / "checkpoints"
            bucket.mkdir(parents=True, exist_ok=True)
            stamp = f"{self.writes():04d}-{path.name}"
            (bucket / stamp).write_text(prior, encoding="utf-8")

    def rewind(self) -> str:
        journal = self.dir / "journal.jsonl"
        if not journal.is_file():
            return "nothing to rewind"
        lines = journal.read_text(encoding="utf-8").splitlines()
        if not lines:
            return "nothing to rewind"
        last = json.loads(lines[-1])
        target = Path(last["path"])
        prior = last.get("prior")
        if prior is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(prior, encoding="utf-8")
        journal.write_text("\n".join(lines[:-1]) + ("\n" if lines[:-1] else ""), encoding="utf-8")
        return f"rewound {target.name}"

    def instructions(self, limit: int = 8000) -> str:
        chunks = []
        for name in ("RONIN.md", "CLAUDE.md", "AGENTS.md"):
            path = self.root / name
            if path.is_file():
                chunks.append(f"# {name}\n" + path.read_text(encoding="utf-8", errors="replace"))
        text = "\n\n".join(chunks)
        if len(text) <= limit:
            return text
        return text[:limit] + "\n…(instructions truncated)"

    def add_todo(self, title: str) -> int:
        todos = self._todos()
        todos.append({"title": title, "done": False})
        self._write_todos(todos)
        return len(todos)

    def complete_todo(self, index: int) -> str:
        todos = self._todos()
        if index < 1 or index > len(todos):
            raise IndexError("no todo at that index")
        todos[index - 1]["done"] = True
        self._write_todos(todos)
        return todos[index - 1]["title"]

    def todo_lines(self) -> str:
        todos = self._todos()
        if not todos:
            return "none"
        rows = []
        for i, item in enumerate(todos, 1):
            mark = "x" if item.get("done") else " "
            rows.append(f"{i}. [{mark}] {item.get('title', '')}")
        return "\n".join(rows)

    def compact(self, transcript: str, keep: int = 4) -> str:
        lines = [line for line in transcript.splitlines() if line.strip()]
        if len(lines) <= keep:
            return transcript
        older = len(lines) - keep
        tail = "\n".join(lines[-keep:])
        return f"[compacted {older} earlier lines]\n{tail}"

    def _todos(self) -> list[dict]:
        path = self.dir / "todos.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _write_todos(self, todos: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "todos.json").write_text(json.dumps(todos), encoding="utf-8")
