"""Local, crash-resumable run state and hard execution budgets.

The module deliberately depends only on the standard library.  Hosts opt in by
passing a :class:`RunJournal` and :class:`RunBudget` to an agent run; the
journal records durable state locally and the budget blocks an action before it
can exceed a configured limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import threading
from typing import Any, Callable
import uuid


_SCHEMA_VERSION = 1


class DurableRunError(RuntimeError):
    """Raised when a durable run cannot be started or resumed safely."""


class BudgetExceeded(RuntimeError):
    """Raised before an action that would exceed a hard run limit."""


@dataclass(frozen=True)
class BudgetLimits:
    """Optional hard limits for one run; ``None`` leaves a dimension unlimited."""

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_time_seconds: float | None = None
    max_tool_calls: int | None = None
    max_concurrency: int | None = None
    max_subagent_depth: int | None = None
    warning_fraction: float = 0.8

    def __post_init__(self) -> None:
        for name in (
            "max_tokens", "max_cost_usd", "max_wall_time_seconds", "max_tool_calls",
            "max_concurrency", "max_subagent_depth",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when configured")
        if not 0 < self.warning_fraction < 1:
            raise ValueError("warning_fraction must be greater than 0 and less than 1")

    def as_dict(self) -> dict[str, int | float | None]:
        """Serialize explicit ceilings so a resumed host can retain them."""
        return {
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_concurrency": self.max_concurrency,
            "max_subagent_depth": self.max_subagent_depth,
            "warning_fraction": self.warning_fraction,
        }


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    warnings: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class RunBudget:
    """Mutable budget state with explicit reservations for active tool calls."""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    clock: Callable[[], float] = time.monotonic
    started_at: float = field(init=False)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    active_actions: int = 0
    subagent_depth: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.clock() - self.started_at)

    def before_provider(self) -> BudgetDecision:
        """Check the limits before asking a provider to start another request."""
        with self._lock:
            return self._check()

    def record_provider_usage(self, usage: dict[str, int | float]) -> BudgetDecision:
        """Add explicit provider usage without inferring prices from model names."""
        with self._lock:
            self.input_tokens += int(usage.get("input_tokens", 0) or 0)
            self.output_tokens += int(usage.get("output_tokens", 0) or 0)
            self.cost_usd += float(usage.get("cost_usd", 0) or 0)
            return self._check()

    def begin_tool(self) -> BudgetDecision:
        """Reserve one tool slot before invoking the handler."""
        with self._lock:
            decision = self._check(
                next_tool_calls=self.tool_calls + 1,
                next_active_actions=self.active_actions + 1,
            )
            if decision.allowed:
                self.tool_calls += 1
                self.active_actions += 1
            return decision

    def finish_tool(self) -> None:
        with self._lock:
            self.active_actions = max(0, self.active_actions - 1)

    def enter_subagent(self) -> BudgetDecision:
        with self._lock:
            decision = self._check(next_subagent_depth=self.subagent_depth + 1)
            if decision.allowed:
                self.subagent_depth += 1
            return decision

    def exit_subagent(self) -> None:
        with self._lock:
            self.subagent_depth = max(0, self.subagent_depth - 1)

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "cost_usd": self.cost_usd,
                "tool_calls": self.tool_calls,
                "active_actions": self.active_actions,
                "subagent_depth": self.subagent_depth,
                "elapsed_seconds": self.elapsed_seconds,
            }

    def restore(self, snapshot: dict[str, int | float]) -> None:
        """Restore a persisted budget snapshot before resuming a run."""
        with self._lock:
            self.input_tokens = max(0, int(snapshot.get("input_tokens", 0) or 0))
            self.output_tokens = max(0, int(snapshot.get("output_tokens", 0) or 0))
            self.cost_usd = max(0.0, float(snapshot.get("cost_usd", 0) or 0))
            self.tool_calls = max(0, int(snapshot.get("tool_calls", 0) or 0))
            self.active_actions = 0
            self.subagent_depth = max(0, int(snapshot.get("subagent_depth", 0) or 0))
            elapsed = max(0.0, float(snapshot.get("elapsed_seconds", 0) or 0))
            self.started_at = self.clock() - elapsed

    def _check(
        self,
        *,
        next_tool_calls: int | None = None,
        next_active_actions: int | None = None,
        next_subagent_depth: int | None = None,
    ) -> BudgetDecision:
        values = (
            ("tokens", self.total_tokens, self.limits.max_tokens, "tokens", True),
            ("cost", self.cost_usd, self.limits.max_cost_usd, "USD", True),
            ("wall time", self.elapsed_seconds, self.limits.max_wall_time_seconds, "seconds", True),
            ("tool calls", next_tool_calls if next_tool_calls is not None else self.tool_calls,
             self.limits.max_tool_calls, "calls", next_tool_calls is None),
            ("concurrency", next_active_actions if next_active_actions is not None else self.active_actions,
             self.limits.max_concurrency, "active actions", next_active_actions is None),
            ("subagent depth", next_subagent_depth if next_subagent_depth is not None else self.subagent_depth,
             self.limits.max_subagent_depth, "levels", next_subagent_depth is None),
        )
        warnings: list[str] = []
        for name, value, limit, unit, stop_at_limit in values:
            if limit is None:
                continue
            if value >= limit if stop_at_limit else value > limit:
                return BudgetDecision(False, tuple(warnings), f"{name} budget reached: {value:g}/{limit:g} {unit}")
            if value >= limit * self.limits.warning_fraction:
                warnings.append(f"{name} budget warning: {value:g}/{limit:g} {unit}")
        return BudgetDecision(True, tuple(warnings))


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    kind: str
    created_at: float
    details: dict[str, Any]


class RunJournal:
    """Versioned SQLite journal with append-only events and atomic JSON checkpoints."""

    def __init__(self, path: Path | str, *, checkpoint_dir: Path | str | None = None) -> None:
        self.path = Path(path).expanduser()
        self.checkpoint_dir = Path(checkpoint_dir).expanduser() if checkpoint_dir else self.path.parent / "checkpoints"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def start(self, state: dict[str, Any], *, run_id: str | None = None) -> str:
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        now = time.time()
        with self._connect() as con:
            exists = con.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
            if exists:
                raise DurableRunError(f"run already exists: {run_id}")
            con.execute(
                "INSERT INTO runs(id, status, created_at, updated_at, error) VALUES (?, 'running', ?, ?, '')",
                (run_id, now, now),
            )
        self.append_event(run_id, "run_started", {})
        self.checkpoint(run_id, state)
        return run_id

    def checkpoint(self, run_id: str, state: dict[str, Any]) -> Path:
        """Write full JSON state atomically, then reference it from SQLite."""
        self._require_run(run_id)
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
        digest = sha256(encoded.encode("utf-8")).hexdigest()
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM checkpoints WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
        target = self.checkpoint_dir / f"{run_id}-{sequence:06d}.json"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.checkpoint_dir, delete=False) as temp:
            temp.write(encoded)
            temp.flush()
            temp_path = Path(temp.name)
        try:
            temp_path.replace(target)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise DurableRunError(f"could not save checkpoint for {run_id}") from exc
        now = time.time()
        with self._connect() as con:
            con.execute(
                "INSERT INTO checkpoints(run_id, sequence, path, digest, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, str(target), digest, now),
            )
            con.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now, run_id))
        self.append_event(run_id, "checkpoint_saved", {"sequence": sequence, "digest": digest})
        return target

    def resume(self, run_id: str) -> dict[str, Any]:
        """Load the latest verified checkpoint and return the saved run state."""
        self._require_run(run_id)
        with self._connect() as con:
            run = con.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            row = con.execute(
                "SELECT path, digest FROM checkpoints WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if run is None or str(run["status"]) not in {"running", "interrupted"}:
            raise DurableRunError(f"run is not resumable: {run_id}")
        if row is None:
            raise DurableRunError(f"run has no checkpoint: {run_id}")
        path = Path(row["path"])
        try:
            encoded = path.read_text(encoding="utf-8")
            state = json.loads(encoded)
        except (OSError, ValueError) as exc:
            raise DurableRunError(f"checkpoint is unreadable for {run_id}") from exc
        if sha256(encoded.encode("utf-8")).hexdigest() != row["digest"]:
            raise DurableRunError(f"checkpoint integrity check failed for {run_id}")
        if not isinstance(state, dict):
            raise DurableRunError(f"checkpoint state is invalid for {run_id}")
        self._set_status(run_id, "running")
        self.append_event(run_id, "run_resumed", {})
        return state

    def append_event(self, run_id: str, kind: str, details: dict[str, Any]) -> JournalEvent:
        self._require_run(run_id)
        now = time.time()
        payload = json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            con.execute(
                "INSERT INTO events(run_id, sequence, kind, created_at, details_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, kind, now, payload),
            )
            con.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now, run_id))
        return JournalEvent(sequence=sequence, kind=kind, created_at=now, details=dict(details))

    def events(self, run_id: str) -> list[JournalEvent]:
        self._require_run(run_id)
        with self._connect() as con:
            rows = con.execute(
                "SELECT sequence, kind, created_at, details_json FROM events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            JournalEvent(
                sequence=int(row["sequence"]), kind=str(row["kind"]), created_at=float(row["created_at"]),
                details=_details(row["details_json"]),
            )
            for row in rows
        ]

    def interrupted_runs(self) -> list[str]:
        """Runs a host may resume after a process exit or an interrupted turn."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM runs WHERE status IN ('running', 'interrupted') ORDER BY created_at",
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def finish(self, run_id: str, *, status: str, error: str = "") -> None:
        if status not in {"completed", "failed", "blocked", "interrupted"}:
            raise ValueError("invalid terminal run status")
        self._set_status(run_id, status, error=error)
        self.append_event(run_id, "run_finished", {"status": status, "error": error})

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _migrate(self) -> None:
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
            current = con.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()["version"]
            if current > _SCHEMA_VERSION:
                raise DurableRunError("journal schema is newer than this Ronin version")
            if current < 1:
                con.executescript(
                    """
                    CREATE TABLE runs (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        error TEXT NOT NULL
                    );
                    CREATE TABLE events (
                        run_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        details_json TEXT NOT NULL,
                        PRIMARY KEY (run_id, sequence),
                        FOREIGN KEY (run_id) REFERENCES runs(id)
                    );
                    CREATE TABLE checkpoints (
                        run_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        path TEXT NOT NULL,
                        digest TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (run_id, sequence),
                        FOREIGN KEY (run_id) REFERENCES runs(id)
                    );
                    """
                )
                con.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (time.time(),))

    def _require_run(self, run_id: str) -> None:
        with self._connect() as con:
            found = con.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if found is None:
            raise DurableRunError(f"unknown run: {run_id}")

    def _set_status(self, run_id: str, status: str, *, error: str = "") -> None:
        self._require_run(run_id)
        with self._connect() as con:
            con.execute(
                "UPDATE runs SET status = ?, updated_at = ?, error = ? WHERE id = ?",
                (status, time.time(), error, run_id),
            )


def _details(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
