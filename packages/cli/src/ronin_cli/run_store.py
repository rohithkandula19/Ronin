"""On-disk stores the dashboard reads: faithfulness scores and orchestration runs.

ronin already archives every conversation under ``.ronin/sessions/`` and tracks
Cost-Router savings in ``.ronin/savings.jsonl``. Two signals were computed at run
time but never persisted: the faithfulness/grounding verdict for an answer, and
the subagent tree an orchestrated run produced. This module persists both, using
the same project-local ``.ronin/`` conventions the rest of the CLI uses, so the
``ronin ui`` dashboard surfaces REAL stored data rather than recomputing it.

- Faithfulness scores append one JSON line per check to ``.ronin/faithfulness.jsonl``
  (same append-only shape as ``savings.jsonl``). ``load_faithfulness`` returns the
  most recent first.
- Orchestration runs write one file per run to ``.ronin/orchestrations/<id>.json``
  (same one-file-per-record shape as ``sessions/``), keeping the plan + every
  subtask result so the dashboard can render the subagent tree.

Everything here is pure I/O over JSON: no provider, no network, dependency-free.
Writes are best-effort and never raise into a turn (mirroring ``sessions.py``).
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

# Project-local, like sessions/ and savings.jsonl: gitignored, stays on the box.
_FAITHFULNESS_FILE = Path(".ronin") / "faithfulness.jsonl"
_ORCH_SUBDIR = Path(".ronin") / "orchestrations"

_MAX_FAITHFULNESS_RETURN = 200


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_run_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Faithfulness scores
# ---------------------------------------------------------------------------


def record_faithfulness(
    score: float,
    *,
    verdict: str,
    n_sources: int = 0,
    abstain: bool = False,
    answer: str = "",
    hallucinated_symbols: list[str] | None = None,
    session_id: str | None = None,
    root: Path | str = ".",
) -> Path | None:
    """Append one faithfulness check to ``.ronin/faithfulness.jsonl``.

    ``verdict`` is one of ``grounded`` / ``ungrounded`` / ``abstain`` (what the
    dashboard colours green/red/amber). Returns the file path, or ``None`` on any
    write error (persistence must never break the run that produced the score).
    """
    path = Path(root) / _FAITHFULNESS_FILE
    rec = {
        "ts": _now_iso(),
        "score": round(float(score), 4),
        "verdict": str(verdict),
        "abstain": bool(abstain),
        "n_sources": int(n_sources),
        # Trim the answer so the log stays small but stays honest about what was scored.
        "answer": (answer or "")[:280],
        "hallucinated_symbols": list(hallucinated_symbols or []),
        "session_id": session_id or "",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        return None
    return path


def load_faithfulness(root: Path | str = ".", *, limit: int = _MAX_FAITHFULNESS_RETURN) -> list[dict]:
    """Recent faithfulness checks, most-recent first. Empty list if none recorded.

    Tolerant of partial/corrupt lines (a truncated append is skipped, not fatal)."""
    path = Path(root) / _FAITHFULNESS_FILE
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    out.reverse()  # newest first
    return out[: max(1, limit)]


def faithfulness_summary(root: Path | str = ".") -> dict:
    """Aggregate counts for the dashboard header (totals by verdict + mean score)."""
    records = load_faithfulness(root, limit=_MAX_FAITHFULNESS_RETURN)
    if not records:
        return {"total": 0, "grounded": 0, "ungrounded": 0, "abstain": 0, "mean_score": None}
    grounded = sum(1 for r in records if r.get("verdict") == "grounded")
    ungrounded = sum(1 for r in records if r.get("verdict") == "ungrounded")
    abstain = sum(1 for r in records if r.get("verdict") == "abstain" or r.get("abstain"))
    scores = [float(r["score"]) for r in records if isinstance(r.get("score"), (int, float))]
    mean = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "total": len(records),
        "grounded": grounded,
        "ungrounded": ungrounded,
        "abstain": abstain,
        "mean_score": mean,
    }


# ---------------------------------------------------------------------------
# Orchestration runs (the subagent tree)
# ---------------------------------------------------------------------------


def record_orchestration(
    goal: str,
    *,
    success: bool,
    plan_subtasks: list[dict] | None = None,
    subtask_results: list[dict] | None = None,
    output: str = "",
    error: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    root: Path | str = ".",
) -> Path | None:
    """Persist one orchestrated run to ``.ronin/orchestrations/<id>.json``.

    ``plan_subtasks`` is the planner's decomposition (each: id, description,
    assignee, depends_on); ``subtask_results`` is what each named sub-agent
    produced (each: subtask_id, assignee, success, output, error, iterations).
    Together they are the subagent tree the dashboard renders. Returns the path or
    ``None`` on a write error.
    """
    rid = run_id or _new_run_id()
    path = Path(root) / _ORCH_SUBDIR / f"{rid}.json"
    data = {
        "id": rid,
        "ts": _now_iso(),
        "goal": goal,
        "success": bool(success),
        "plan": list(plan_subtasks or []),
        "subtasks": list(subtask_results or []),
        "output": output or "",
        "error": error,
        "session_id": session_id or "",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path


def list_orchestrations(root: Path | str = ".") -> list[dict]:
    """Metadata for every orchestrated run, most-recent first (no full subtask
    bodies, just enough for a list view)."""
    d = Path(root) / _ORCH_SUBDIR
    if not d.is_dir():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        out.append({
            "id": data.get("id", f.stem),
            "ts": data.get("ts", ""),
            "goal": data.get("goal", ""),
            "success": bool(data.get("success", False)),
            "subtasks": len(data.get("subtasks", []) or []),
            "session_id": data.get("session_id", ""),
        })
    out.sort(key=lambda d: (d["ts"], d["id"]), reverse=True)
    return out


def load_orchestration(run_id: str, root: Path | str = ".") -> dict | None:
    """The full orchestrated run (plan + every subtask result) by id, or ``None``."""
    path = Path(root) / _ORCH_SUBDIR / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
