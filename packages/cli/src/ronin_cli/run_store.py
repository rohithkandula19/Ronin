"""Durable record of orchestrated agent runs — the data the web dashboard reads.

The multi-agent orchestrator (``ronin orchestrate``) decomposes a goal into
subtasks, assigns each to a provider-agnostic specialist sub-agent, runs them,
and synthesizes a final answer. None of that survived the process before: the
result was printed and thrown away. This module archives each run to its own
timestamped JSON file so the run, its planner -> sub-agent tree, and any
per-answer faithfulness scores can be read back later (e.g. by the ``ronin ui``
dashboard).

Files live under ``<RONIN_HOME or ~/.ronin>/runs/`` so they are user-global and
honour ``RONIN_HOME`` (the same override bushido/memory use). The store is pure
JSON on disk — no DB, no network — and is fully offline. A run record carries:

- ``id`` / ``created`` / ``goal`` / ``success``
- ``planner``: the provider/model label that planned + synthesized
- ``subagents``: the roster, each ``{role, provider}`` (the tree's branches)
- ``subtasks``: each ``{id, description, assignee, depends_on}`` from the plan
- ``results``: per subtask ``{subtask_id, assignee, success, output, error,
  iterations, faithfulness}`` where ``faithfulness`` is ``{score, grounded,
  abstain}`` or ``None`` when not scored
- ``output``: the synthesized final answer

Read-only consumers should treat every field as optional and tolerate older
records (forward/backward compatible).
"""
from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any


def runs_dir() -> Path:
    """Directory holding archived run records. Honours ``RONIN_HOME``."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "runs"


def _new_run_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def save_run(record: dict[str, Any]) -> Path | None:
    """Archive one run ``record`` to its own file. Returns the path (or None on
    an unwritable home). An ``id``/``created`` are filled in when absent."""
    rec = dict(record)
    rec.setdefault("id", _new_run_id())
    rec.setdefault("created", datetime.datetime.now().isoformat(timespec="seconds"))
    rec.setdefault("kind", "orchestrate")
    d = runs_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    path = d / f"{rec['id']}.json"
    try:
        path.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    except OSError:
        return None
    return path


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Summary metadata for archived runs, most recent first.

    Each summary is small (no full sub-agent output): ``id``, ``created``,
    ``goal``, ``success``, ``kind``, ``n_subtasks``, ``planner``. Tolerates and
    skips any unreadable/legacy file."""
    d = runs_dir()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.json")):
        rec = _read(f)
        if rec is None:
            continue
        subtasks = rec.get("subtasks") or []
        out.append({
            "id": rec.get("id", f.stem),
            "created": rec.get("created", ""),
            "goal": rec.get("goal", ""),
            "success": bool(rec.get("success", False)),
            "kind": rec.get("kind", "orchestrate"),
            "n_subtasks": len(subtasks) if isinstance(subtasks, list) else 0,
            "planner": rec.get("planner", ""),
        })
    out.sort(key=lambda r: (r["created"], r["id"]), reverse=True)
    return out[: max(1, limit)]


def load_run(run_id: str) -> dict[str, Any] | None:
    """The full record for a run id, or ``None`` if missing/corrupt. The id is
    used as a filename; path separators are stripped so it cannot escape the
    runs directory."""
    safe = Path(run_id).name
    if not safe:
        return None
    path = runs_dir() / f"{safe}.json"
    return _read(path)


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_from_outcome(
    goal: str,
    outcome: Any,
    *,
    planner_label: str = "",
    roster: dict[str, str] | None = None,
    faithfulness: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a run record from an ``OrchestrateOutcome`` (or any object exposing
    ``success``/``output``/``plan_subtasks``/``subtask_results``).

    ``roster`` maps role -> provider label (the sub-agent tree's branches).
    ``faithfulness`` maps subtask id -> ``{score, grounded, abstain}``; merged
    onto the matching result. All inputs are plain data so this stays pure and
    offline-testable.
    """
    roster = roster or {}
    faithfulness = faithfulness or {}
    subtasks = list(getattr(outcome, "plan_subtasks", []) or [])
    results = []
    for r in getattr(outcome, "subtask_results", []) or []:
        r = dict(r)
        sid = r.get("subtask_id")
        if sid in faithfulness:
            r["faithfulness"] = faithfulness[sid]
        else:
            r.setdefault("faithfulness", None)
        results.append(r)
    # The roster surfaces every registered specialist; the plan may only use
    # some of them. Branches the plan never invoked are still real members of
    # the tree, so keep the full roster.
    assignees = {st.get("assignee") for st in subtasks}
    subagents = [
        {"role": role, "provider": prov, "used": role in assignees}
        for role, prov in roster.items()
    ]
    return {
        "kind": "orchestrate",
        "goal": goal,
        "success": bool(getattr(outcome, "success", False)),
        "error": getattr(outcome, "error", None),
        "planner": planner_label,
        "subagents": subagents,
        "subtasks": subtasks,
        "results": results,
        "output": getattr(outcome, "output", "") or "",
    }
