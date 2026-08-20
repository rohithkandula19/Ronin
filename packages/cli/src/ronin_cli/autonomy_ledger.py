"""Tamper-evident, project-local record of autonomous decisions and outcomes."""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


LEDGER_PATH = ".ronin/autonomy-ledger.jsonl"
_LOCK = threading.RLock()


@dataclass(frozen=True)
class LedgerVerification:
    valid: bool
    events: int
    error: str | None = None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AutonomyLedger:
    """Append-only hash chain. Event payloads are summaries, never raw prompts."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / LEDGER_PATH

    def append(self, kind: str, *, run_id: str | None, goal: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not kind or len(kind) > 80:
            raise ValueError("ledger event kind must be a short non-empty string")
        safe_payload = _json_safe(payload)
        with _LOCK:
            verification = verify_ledger(self.root)
            if not verification.valid:
                raise ValueError(f"refusing to append to an invalid autonomy ledger: {verification.error}")
            previous = _last_hash(self.path)
            event: dict[str, Any] = {
                "version": 1,
                "id": f"ledger-{uuid.uuid4().hex[:12]}",
                "at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds"),
                "kind": kind,
                "run_id": run_id,
                "goal_digest": digest(goal),
                "payload": safe_payload,
                "previous_hash": previous,
            }
            event["hash"] = _event_hash(event)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def verify(self) -> LedgerVerification:
        return verify_ledger(self.root)


def record_orchestration(root: Path | str, *, outcome: Any, goal: str) -> None:
    """Record only verifiable run metadata; ledger trouble must not sink a run."""
    profiles = [getattr(profile, "key", str(profile)) for profile in getattr(outcome, "agent_profiles", ())]
    diffs = getattr(outcome, "worktree_diffs", {}) or {}
    payload = {
        "success": bool(getattr(outcome, "success", False)),
        "error_digest": digest(str(getattr(outcome, "error", "") or "")) if getattr(outcome, "error", None) else None,
        "profiles": profiles,
        "providers": {
            name: str(value.get("status", "unknown"))
            for name, value in (getattr(outcome, "provider_health", {}) or {}).items()
            if isinstance(value, dict)
        },
        "worktree_roles": sorted(diffs),
        "diff_digest": digest(str(getattr(outcome, "diff", "") or "")),
        "proposal": {
            "run_id": getattr(outcome, "proposal", {}).get("run_id"),
            "status": getattr(outcome, "proposal", {}).get("status"),
        } if isinstance(getattr(outcome, "proposal", None), dict) else None,
    }
    AutonomyLedger(root).append("orchestration.completed", run_id=getattr(outcome, "run_id", None), goal=goal, payload=payload)


def verify_ledger(root: Path | str) -> LedgerVerification:
    path = Path(root).resolve() / LEDGER_PATH
    if not path.exists():
        return LedgerVerification(True, 0)
    previous: str | None = None
    events = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return LedgerVerification(False, 0, f"could not read ledger: {exc}")
    for number, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return LedgerVerification(False, events, f"line {number} is not JSON")
        if not isinstance(event, dict) or event.get("previous_hash") != previous:
            return LedgerVerification(False, events, f"line {number} has a broken previous hash")
        stored = event.get("hash")
        if not isinstance(stored, str) or stored != _event_hash(event):
            return LedgerVerification(False, events, f"line {number} hash does not verify")
        previous = stored
        events += 1
    return LedgerVerification(True, events)


def recent_events(root: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    path = Path(root).resolve() / LEDGER_PATH
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)][-max(1, limit):]


def _event_hash(event: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "hash"}
    text = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return digest(text)


def _last_hash(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1]) if lines else {}
        return last.get("hash") if isinstance(last, dict) and isinstance(last.get("hash"), str) else None
    except (OSError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    """Bound values so a huge or non-serializable payload cannot break the ledger."""
    try:
        encoded = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = json.dumps(str(value))
    if len(encoded) > 24_000:
        return {"truncated_digest": digest(encoded), "bytes": len(encoded)}
    return json.loads(encoded)
