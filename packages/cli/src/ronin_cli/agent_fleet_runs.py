"""Durable, bounded execution state for persisted specialist fleet plans.

``agent_fleet`` decides which specialists belong in each phase. This module
decides which one *may* run next. It is deliberately provider-agnostic: the
caller supplies the bounded wave worker, while this store atomically records
claims and terminal evidence. A fleet run never stages, commits, or pushes a
proposal; write-capable waves still use Ronin's isolated-worktree path.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .agent_fleet import FleetPlan


FLEET_RUN_SCHEMA_VERSION = 1
_FLEET_RUN_ID = re.compile(r"^fleet-run-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
_PHASES = {"research", "implementation", "acceptance"}
_WAVE_STATES = {"waiting", "running", "completed", "failed", "cancelled"}
_RUN_STATES = {"queued", "running", "completed", "failed", "cancelled"}
_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled"}


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    stamp = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"fleet-run-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class FleetWaveRun:
    """One persisted phase/wave claim and its bounded execution evidence."""

    number: int
    phase: str
    profile_keys: tuple[str, ...]
    waits_for: tuple[int, ...]
    status: str = "waiting"
    attempts: int = 0
    started: str | None = None
    completed: str | None = None
    agent_run_id: str | None = None
    proposal_run_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "phase": self.phase,
            "profile_keys": list(self.profile_keys),
            "waits_for": list(self.waits_for),
            "status": self.status,
            "attempts": self.attempts,
            "started": self.started,
            "completed": self.completed,
            "agent_run_id": self.agent_run_id,
            "proposal_run_id": self.proposal_run_id,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "FleetWaveRun | None":
        if not isinstance(raw, dict):
            return None
        number, phase, keys, waits_for = (
            raw.get("number"), raw.get("phase"), raw.get("profile_keys"), raw.get("waits_for"),
        )
        status = raw.get("status", "waiting")
        attempts = raw.get("attempts", 0)
        if (
            not isinstance(number, int) or number < 1 or phase not in _PHASES
            or not isinstance(keys, list) or not keys
            or not all(isinstance(key, str) and _PROFILE_ID.fullmatch(key) for key in keys)
            or not isinstance(waits_for, list)
            or not all(isinstance(item, int) and item >= 1 for item in waits_for)
            or status not in _WAVE_STATES
            or not isinstance(attempts, int) or attempts < 0
        ):
            return None
        optional = ("started", "completed", "agent_run_id", "proposal_run_id", "error")
        if not all(raw.get(field) is None or isinstance(raw.get(field), str) for field in optional):
            return None
        return cls(
            number=number,
            phase=phase,
            profile_keys=tuple(keys),
            waits_for=tuple(waits_for),
            status=status,
            attempts=attempts,
            started=raw.get("started"),
            completed=raw.get("completed"),
            agent_run_id=raw.get("agent_run_id"),
            proposal_run_id=raw.get("proposal_run_id"),
            error=raw.get("error"),
        )


@dataclass(frozen=True)
class FleetRun:
    """A durable, project-local execution instance of one fleet plan."""

    id: str
    plan_id: str
    goal: str
    write: bool
    max_parallel: int
    created: str
    updated: str
    status: str
    waves: tuple[FleetWaveRun, ...]
    error: str | None = None

    @classmethod
    def from_plan(cls, plan: FleetPlan) -> "FleetRun":
        now = _now()
        return cls(
            id=_new_id(),
            plan_id=plan.id,
            goal=plan.goal,
            write=plan.write,
            max_parallel=plan.max_parallel,
            created=now,
            updated=now,
            status="queued",
            waves=tuple(
                FleetWaveRun(
                    number=wave.number,
                    phase=wave.phase,
                    profile_keys=wave.profile_keys,
                    waits_for=wave.waits_for,
                )
                for wave in plan.waves
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": FLEET_RUN_SCHEMA_VERSION,
            "id": self.id,
            "plan_id": self.plan_id,
            "goal": self.goal,
            "write": self.write,
            "max_parallel": self.max_parallel,
            "created": self.created,
            "updated": self.updated,
            "status": self.status,
            "waves": [wave.as_dict() for wave in self.waves],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "FleetRun | None":
        if not isinstance(raw, dict) or raw.get("schema_version") != FLEET_RUN_SCHEMA_VERSION:
            return None
        ident, plan_id, goal = raw.get("id"), raw.get("plan_id"), raw.get("goal")
        created, updated, status = raw.get("created"), raw.get("updated"), raw.get("status")
        write, max_parallel, raw_waves = raw.get("write"), raw.get("max_parallel"), raw.get("waves")
        if (
            not isinstance(ident, str) or not _FLEET_RUN_ID.fullmatch(ident)
            or not isinstance(plan_id, str) or not plan_id
            or not isinstance(goal, str) or not goal.strip()
            or not isinstance(write, bool)
            or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 32
            or not isinstance(created, str) or not isinstance(updated, str)
            or status not in _RUN_STATES or not isinstance(raw_waves, list) or not raw_waves
            or raw.get("error") is not None and not isinstance(raw.get("error"), str)
        ):
            return None
        waves = [FleetWaveRun.from_dict(item) for item in raw_waves]
        if any(wave is None for wave in waves):
            return None
        parsed = tuple(wave for wave in waves if wave is not None)
        if (
            [wave.number for wave in parsed] != list(range(1, len(parsed) + 1))
            or any(len(wave.profile_keys) > max_parallel for wave in parsed)
            or any(wait >= wave.number for wave in parsed for wait in wave.waits_for)
        ):
            return None
        return cls(
            id=ident,
            plan_id=plan_id,
            goal=goal,
            write=write,
            max_parallel=max_parallel,
            created=created,
            updated=updated,
            status=status,
            waves=parsed,
            error=raw.get("error"),
        )


@dataclass(frozen=True)
class FleetWaveResult:
    """Minimal terminal evidence returned by a provider-specific wave worker."""

    success: bool
    agent_run_id: str | None = None
    proposal_run_id: str | None = None
    error: str | None = None


class FleetRunStore:
    """Atomic local state for a fleet run.

    The store claims only one wave at a time. This is conservative by design:
    the plan's ``max_parallel`` remains a hard cap across the active work, and
    a restart cannot silently run multiple dependent write waves at once.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "fleet-runs"
        self._lock = threading.RLock()

    def create(self, plan: FleetPlan) -> FleetRun:
        with self._lock:
            run = FleetRun.from_plan(plan)
            self._save(run)
            return run

    def list(self, *, limit: int = 100) -> tuple[FleetRun, ...]:
        try:
            paths = sorted(self.directory.glob("fleet-run-*.json"))
        except OSError:
            return ()
        runs: list[FleetRun] = []
        for path in paths:
            try:
                runs.append(self.load(path.stem))
            except ValueError:
                continue
        runs.sort(key=lambda run: (run.updated, run.id), reverse=True)
        return tuple(runs[:max(1, limit)])

    def load(self, run_id: str) -> FleetRun:
        safe_id = _safe_id(run_id)
        try:
            raw = json.loads((self.directory / f"{safe_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"fleet run {safe_id!r} was not found") from exc
        run = FleetRun.from_dict(raw)
        if run is None:
            raise ValueError(f"fleet run {safe_id!r} is invalid")
        return run

    def claim_next(self, run_id: str) -> tuple[FleetRun, FleetWaveRun | None]:
        """Claim the next dependency-ready wave, or finish a run with no work left."""
        with self._lock:
            run = self.load(run_id)
            if run.status in _TERMINAL_RUN_STATES:
                raise ValueError(f"fleet run is already {run.status}")
            active = next((wave for wave in run.waves if wave.status == "running"), None)
            if active is not None:
                raise ValueError(
                    f"fleet run has active wave {active.number}; use fleet recover only after its worker stopped"
                )
            ready = next((wave for wave in run.waves if _ready(wave, run.waves)), None)
            if ready is None:
                terminal = "completed" if all(wave.status == "completed" for wave in run.waves) else "failed"
                error = None if terminal == "completed" else _blocked_reason(run.waves)
                finished = replace(run, status=terminal, error=error, updated=_now())
                self._save(finished)
                return finished, None
            claimed = replace(
                ready,
                status="running",
                attempts=ready.attempts + 1,
                started=_now(),
                completed=None,
                agent_run_id=None,
                proposal_run_id=None,
                error=None,
            )
            claimed_run = _replace_wave(run, claimed, status="running", error=None)
            self._save(claimed_run)
            return claimed_run, claimed

    def finish_wave(self, run_id: str, wave_number: int, result: FleetWaveResult) -> FleetRun:
        """Record a terminal worker outcome; failures block later waves until retry."""
        with self._lock:
            run = self.load(run_id)
            wave = _wave_by_number(run, wave_number)
            if wave.status != "running":
                raise ValueError(f"fleet wave {wave_number} is not running")
            finished = replace(
                wave,
                status="completed" if result.success else "failed",
                completed=_now(),
                agent_run_id=result.agent_run_id,
                proposal_run_id=result.proposal_run_id,
                error=None if result.success else (result.error or "wave worker failed"),
            )
            remaining = tuple(finished if item.number == wave_number else item for item in run.waves)
            all_completed = all(item.status == "completed" for item in remaining)
            status = "completed" if all_completed else ("running" if result.success else "failed")
            error = None if result.success else finished.error
            completed_run = replace(run, waves=remaining, status=status, error=error, updated=_now())
            self._save(completed_run)
            return completed_run

    def retry_failed(self, run_id: str, wave_number: int) -> FleetRun:
        """Explicitly make a failed wave eligible again after the operator reviewed it."""
        with self._lock:
            run = self.load(run_id)
            wave = _wave_by_number(run, wave_number)
            if wave.status != "failed":
                raise ValueError(f"fleet wave {wave_number} is not failed")
            if any(item.number > wave_number and item.status != "waiting" for item in run.waves):
                raise ValueError("cannot retry a fleet wave after a later wave changed state")
            retried = replace(
                wave,
                status="waiting",
                started=None,
                completed=None,
                agent_run_id=None,
                proposal_run_id=None,
                error=None,
            )
            updated = _replace_wave(run, retried, status="queued", error=None)
            self._save(updated)
            return updated

    def recover_interrupted(self, run_id: str) -> FleetRun:
        """Release an orphaned running wave after its worker has definitely stopped."""
        with self._lock:
            run = self.load(run_id)
            active = next((wave for wave in run.waves if wave.status == "running"), None)
            if active is None:
                raise ValueError("fleet run has no active wave to recover")
            recovered = replace(
                active,
                status="waiting",
                started=None,
                completed=None,
                agent_run_id=None,
                proposal_run_id=None,
                error=None,
            )
            updated = _replace_wave(run, recovered, status="queued", error=None)
            self._save(updated)
            return updated

    def cancel(self, run_id: str) -> FleetRun:
        """Cancel a run that has no active worker; completed evidence remains intact."""
        with self._lock:
            run = self.load(run_id)
            if any(wave.status == "running" for wave in run.waves):
                raise ValueError("cannot cancel a fleet run while a wave is running")
            if run.status in _TERMINAL_RUN_STATES:
                raise ValueError(f"fleet run is already {run.status}")
            cancelled = tuple(
                replace(wave, status="cancelled", completed=_now())
                if wave.status == "waiting" else wave
                for wave in run.waves
            )
            updated = replace(run, waves=cancelled, status="cancelled", error=None, updated=_now())
            self._save(updated)
            return updated

    def _save(self, run: FleetRun) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{run.id}.json"
        fd, temporary = tempfile.mkstemp(prefix=f".{run.id}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(run.as_dict(), handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def execute_next_wave(
    store: FleetRunStore,
    run_id: str,
    worker: Callable[[FleetRun, FleetWaveRun], FleetWaveResult],
) -> tuple[FleetRun, FleetWaveRun | None]:
    """Claim, execute, and persist one wave without retaining provider output."""
    run, wave = store.claim_next(run_id)
    if wave is None:
        return run, None
    try:
        result = worker(run, wave)
    except Exception as exc:  # noqa: BLE001 - durable terminal evidence matters most
        result = FleetWaveResult(False, error=f"{type(exc).__name__}: {exc}")
    if not isinstance(result, FleetWaveResult):
        result = FleetWaveResult(False, error="fleet wave worker returned an invalid result")
    return store.finish_wave(run.id, wave.number, result), wave


def _replace_wave(run: FleetRun, replacement: FleetWaveRun, *, status: str, error: str | None) -> FleetRun:
    return replace(
        run,
        waves=tuple(replacement if wave.number == replacement.number else wave for wave in run.waves),
        status=status,
        error=error,
        updated=_now(),
    )


def _wave_by_number(run: FleetRun, number: int) -> FleetWaveRun:
    wave = next((item for item in run.waves if item.number == number), None)
    if wave is None:
        raise ValueError(f"fleet run has no wave {number}")
    return wave


def _ready(wave: FleetWaveRun, waves: tuple[FleetWaveRun, ...]) -> bool:
    if wave.status != "waiting":
        return False
    by_number = {item.number: item for item in waves}
    return all(by_number[number].status == "completed" for number in wave.waits_for)


def _blocked_reason(waves: tuple[FleetWaveRun, ...]) -> str:
    failed = [str(wave.number) for wave in waves if wave.status == "failed"]
    if failed:
        return "fleet wave failure blocks dependent work: " + ", ".join(failed)
    return "fleet run has no dependency-ready waves"


def _safe_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _FLEET_RUN_ID.fullmatch(safe):
        raise ValueError("invalid fleet run id")
    return safe
