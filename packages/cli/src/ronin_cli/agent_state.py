"""Atomic, project-local shared task state for orchestrated agent teams.

``run_store`` archives a finished run for the dashboard.  This store records a
live task board from planning through synthesis, so a stopped process leaves an
honest, inspectable record of what was planned, started, completed, or failed.
It is intentionally JSON-only and lives in ``.ronin/agent-runs`` beside the
project; no provider, database, or background service is required.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_EVENT_TEXT = 8_000
MAX_EVENTS = 500


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _run_id() -> str:
    return f"agent-{_datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class AgentTaskStateStore:
    """Best-effort durable state store. A write failure never stops the run."""

    def __init__(self, root: Path | str) -> None:
        self.directory = Path(root).resolve() / ".ronin" / "agent-runs"
        self._lock = threading.RLock()

    def create(
        self,
        goal: str,
        *,
        selection: Mapping[str, Any],
        workflow: Mapping[str, Any] | None,
        governance: Mapping[str, Any],
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "id": _run_id(),
            "created": _now(),
            "updated": _now(),
            "status": "planning",
            "goal": goal,
            "selection": dict(selection),
            "workflow": dict(workflow or {}),
            "governance": dict(governance),
            "plan": [],
            "subtasks": {},
            "events": [],
            "provider_health": {},
            "output": "",
            "error": None,
        }
        self.save(state)
        return state

    def save(self, state: dict[str, Any]) -> Path | None:
        """Write one whole state atomically and return its path on success."""
        with self._lock:
            state["updated"] = _now()
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f"{state['id']}.json"
                fd, temp_name = tempfile.mkstemp(prefix=f".{state['id']}.", suffix=".tmp", dir=self.directory)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, indent=2, default=str)
                    handle.write("\n")
                os.replace(temp_name, path)
            except (OSError, TypeError, ValueError):
                try:
                    os.unlink(temp_name)
                except (OSError, UnboundLocalError):
                    pass
                return None
        return path

    def apply_step(self, state: dict[str, Any], step: Any) -> None:
        """Record a core ``Step`` as a task-board event and persist it."""
        with self._lock:
            kind = str(getattr(step, "kind", "event"))
            content = getattr(step, "content", "")
            metadata = getattr(step, "metadata", {}) or {}
            if kind == "plan" and isinstance(content, dict):
                state["status"] = "running"
                state["plan"] = list(content.get("subtasks", []))
            elif kind == "tool_result" and isinstance(content, dict):
                subtask = str(content.get("subtask", ""))
                if subtask:
                    state["subtasks"][subtask] = {
                        "assignee": content.get("assignee"),
                        "provider": content.get("provider", ""),
                        "status": "completed" if content.get("success") else "failed",
                        "success": bool(content.get("success")),
                        "output": _short(content.get("output", "")),
                        "error": _short(content.get("error", "")),
                        "iterations": metadata.get("iterations", 0),
                        "updated": _now(),
                    }
            elif kind == "error":
                state["error"] = _short(content)

            self._event(state, kind, content, metadata)
            self.save(state)

    def mark_started(self, state: dict[str, Any], subtask: Any, label: str) -> None:
        sid = str(getattr(subtask, "id", ""))
        if sid:
            with self._lock:
                state["status"] = "running"
                state["subtasks"][sid] = {
                    "assignee": getattr(subtask, "assignee", ""),
                    "status": "running",
                    "provider": label,
                    "description": _short(getattr(subtask, "description", "")),
                    "updated": _now(),
                }
                self._event(state, "subtask_started", {"subtask": sid, "provider": label}, {})
                self.save(state)

    def finish(
        self,
        state: dict[str, Any],
        *,
        success: bool,
        output: str,
        error: str | None,
        provider_health: Mapping[str, Any],
        handoffs: Mapping[str, Any] | None = None,
    ) -> Path | None:
        with self._lock:
            state["status"] = "completed" if success else "failed"
            state["output"] = _short(output)
            state["error"] = _short(error or "") or None
            state["provider_health"] = dict(provider_health)
            if handoffs is not None:
                state["handoffs"] = dict(handoffs)
            self._event(state, "completed", {"success": success}, {})
            return self.save(state)

    @staticmethod
    def _event(state: dict[str, Any], kind: str, content: Any, metadata: Mapping[str, Any]) -> None:
        events = state["events"]
        events.append({
            "at": _now(),
            "kind": kind,
            "content": _short(content),
            "metadata": dict(metadata),
        })
        if len(events) > MAX_EVENTS:
            del events[:-MAX_EVENTS]


def load_agent_task_state(root: Path | str, run_id: str) -> dict[str, Any] | None:
    """Load one project-local agent task state without permitting path escape."""
    safe = Path(run_id).name
    if not safe:
        return None
    path = Path(root).resolve() / ".ronin" / "agent-runs" / f"{safe}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= MAX_EVENT_TEXT else text[:MAX_EVENT_TEXT] + "\n[truncated]"
