"""Persisted, bounded multi-wave plans for Ronin's specialist catalog.

The catalog can contain thousands of profiles, but an execution must stay
small enough to review, fund, and recover. A fleet plan converts a larger
relevant set into local research, implementation, and acceptance waves. It is
only a plan: saving or inspecting one never starts a provider, queue worker,
shell command, or write operation.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .agent_catalog import (
    AgentProfile,
    ProfileSelection,
    RepositorySignals,
    rank_profiles_for_fleet,
)


FLEET_SCHEMA_VERSION = 1
MAX_WAVE_PARALLELISM = 32
_FLEET_ID = re.compile(r"^fleet-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
_PHASES = ("research", "implementation", "acceptance")


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    stamp = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"fleet-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class FleetMember:
    """A selected profile plus the local evidence that selected it."""

    key: str
    tier: str
    source: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data

    @classmethod
    def from_dict(cls, raw: object) -> "FleetMember | None":
        if not isinstance(raw, dict):
            return None
        key, tier, source, reasons = (
            raw.get("key"), raw.get("tier"), raw.get("source"), raw.get("reasons"),
        )
        if (
            not isinstance(key, str) or not _PROFILE_ID.fullmatch(key)
            or tier not in {"explore", "test", "write"}
            or not isinstance(source, str) or not source
            or not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons)
        ):
            return None
        return cls(key, tier, source, tuple(reasons))


@dataclass(frozen=True)
class FleetWave:
    """A bounded group of profiles whose phase prerequisites are complete."""

    number: int
    phase: str
    profile_keys: tuple[str, ...]
    waits_for: tuple[int, ...] = ()

    @property
    def parallelism(self) -> int:
        return len(self.profile_keys)

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "phase": self.phase,
            "profile_keys": list(self.profile_keys),
            "waits_for": list(self.waits_for),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "FleetWave | None":
        if not isinstance(raw, dict):
            return None
        number, phase = raw.get("number"), raw.get("phase")
        keys, waits_for = raw.get("profile_keys"), raw.get("waits_for", [])
        if (
            not isinstance(number, int) or number < 1 or phase not in _PHASES
            or not isinstance(keys, list) or not keys
            or not isinstance(waits_for, list)
            or not all(isinstance(key, str) and _PROFILE_ID.fullmatch(key) for key in keys)
            or not all(isinstance(item, int) and item >= 1 for item in waits_for)
        ):
            return None
        return cls(number, phase, tuple(keys), tuple(waits_for))


@dataclass(frozen=True)
class FleetPlan:
    """A local schedule over a selected subset of the catalog."""

    id: str
    created: str
    goal: str
    write: bool
    catalog_size: int
    max_parallel: int
    members: tuple[FleetMember, ...]
    waves: tuple[FleetWave, ...]
    repository: RepositorySignals

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": FLEET_SCHEMA_VERSION,
            "id": self.id,
            "created": self.created,
            "goal": self.goal,
            "write": self.write,
            "catalog_size": self.catalog_size,
            "max_parallel": self.max_parallel,
            "members": [member.as_dict() for member in self.members],
            "waves": [wave.as_dict() for wave in self.waves],
            "repository": {
                "tags": list(self.repository.tags),
                "relevant_files": list(self.repository.relevant_files),
                "symbols": list(self.repository.symbols),
            },
        }

    @classmethod
    def from_dict(cls, raw: object) -> "FleetPlan | None":
        if not isinstance(raw, dict) or raw.get("schema_version") != FLEET_SCHEMA_VERSION:
            return None
        ident, created, goal = raw.get("id"), raw.get("created"), raw.get("goal")
        write = raw.get("write")
        catalog_size, max_parallel = raw.get("catalog_size"), raw.get("max_parallel")
        raw_members, raw_waves = raw.get("members"), raw.get("waves")
        repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
        if (
            not isinstance(ident, str) or not _FLEET_ID.fullmatch(ident)
            or not isinstance(created, str) or not isinstance(goal, str) or not goal.strip()
            or not isinstance(write, bool) or not isinstance(catalog_size, int) or catalog_size < 1
            or not isinstance(max_parallel, int) or not 1 <= max_parallel <= MAX_WAVE_PARALLELISM
            or not isinstance(raw_members, list) or not isinstance(raw_waves, list)
        ):
            return None
        members = [member for item in raw_members if (member := FleetMember.from_dict(item)) is not None]
        waves = [wave for item in raw_waves if (wave := FleetWave.from_dict(item)) is not None]
        if len(members) != len(raw_members) or len(waves) != len(raw_waves) or not members or not waves:
            return None
        if len({member.key for member in members}) != len(members):
            return None
        if [wave.number for wave in waves] != list(range(1, len(waves) + 1)):
            return None
        member_keys = {member.key for member in members}
        flattened = [key for wave in waves for key in wave.profile_keys]
        if (
            len(flattened) != len(members)
            or len(set(flattened)) != len(flattened)
            or set(flattened) != member_keys
            or any(wave.parallelism > max_parallel for wave in waves)
            or any(wait >= wave.number for wave in waves for wait in wave.waits_for)
            or [wave.phase for wave in waves] != sorted(
                (wave.phase for wave in waves), key=_PHASES.index,
            )
        ):
            return None
        return cls(
            ident, created, goal, write, catalog_size, max_parallel,
            tuple(members), tuple(waves),
            RepositorySignals(
                tags=_strings(repository.get("tags")),
                relevant_files=_strings(repository.get("relevant_files")),
                symbols=_strings(repository.get("symbols")),
            ),
        )


class FleetPlanStore:
    """Atomic project-local store for inspectable fleet plans."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "fleet-plans"

    def save(self, plan: FleetPlan) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{plan.id}.json"
        _atomic_write(path, json.dumps(plan.as_dict(), indent=2, sort_keys=True) + "\n")
        return path

    def load(self, plan_id: str) -> FleetPlan:
        safe_id = _safe_id(plan_id)
        try:
            raw = json.loads((self.directory / f"{safe_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"fleet plan {safe_id!r} was not found") from exc
        plan = FleetPlan.from_dict(raw)
        if plan is None:
            raise ValueError(f"fleet plan {safe_id!r} is invalid")
        return plan

    def list(self, *, limit: int = 100) -> tuple[FleetPlan, ...]:
        try:
            paths = sorted(self.directory.glob("fleet-*.json"))
        except OSError:
            return ()
        plans: list[FleetPlan] = []
        for path in paths:
            try:
                plan = self.load(path.stem)
            except ValueError:
                continue
            plans.append(plan)
        plans.sort(key=lambda plan: (plan.created, plan.id), reverse=True)
        return tuple(plans[:max(1, limit)])


def plan_fleet(
    goal: str,
    profiles: Iterable[AgentProfile],
    *,
    core_keys: Iterable[str],
    repository: RepositorySignals,
    write: bool,
    max_profiles: int = 32,
    max_parallel: int = 8,
) -> FleetPlan:
    """Build an explainable schedule with at most 32 active profiles per wave."""
    text = goal.strip()
    if not text:
        raise ValueError("fleet goal must not be empty")
    if len(text) > 20_000:
        raise ValueError("fleet goal exceeds the 20,000-character safety limit")
    if not 1 <= max_parallel <= MAX_WAVE_PARALLELISM:
        raise ValueError(f"fleet max_parallel must be between 1 and {MAX_WAVE_PARALLELISM}")
    catalog = tuple(profiles)
    selection = rank_profiles_for_fleet(
        text, catalog, core_keys=core_keys, repository=repository,
        max_profiles=max_profiles,
    )
    return _build_plan(text, catalog, selection, write=write, max_parallel=max_parallel)


def _build_plan(
    goal: str,
    catalog: tuple[AgentProfile, ...],
    selection: ProfileSelection,
    *,
    write: bool,
    max_parallel: int,
) -> FleetPlan:
    members = tuple(
        FleetMember(
            profile.key, profile.tier, profile.source,
            tuple(selection.reasons.get(profile.key, ())),
        )
        for profile in selection.profiles
    )
    by_phase: dict[str, list[AgentProfile]] = {phase: [] for phase in _PHASES}
    for profile in selection.profiles:
        by_phase[_phase_for(profile, write=write)].append(profile)

    waves: list[FleetWave] = []
    previous_phase: tuple[int, ...] = ()
    for phase in _PHASES:
        current_phase: list[int] = []
        for group in _chunks(by_phase[phase], max_parallel):
            number = len(waves) + 1
            waves.append(FleetWave(
                number, phase, tuple(profile.key for profile in group), previous_phase,
            ))
            current_phase.append(number)
        if current_phase:
            previous_phase = tuple(current_phase)
    return FleetPlan(
        _new_id(), _now(), goal, write, len(catalog), max_parallel,
        members, tuple(waves), selection.repository,
    )


def _phase_for(profile: AgentProfile, *, write: bool) -> str:
    if profile.key == "researcher":
        return "research"
    if profile.key in {"reviewer", "tester"}:
        return "acceptance"
    if profile.key == "implementer":
        return "implementation" if write else "research"
    if profile.tier == "write":
        return "implementation" if write else "research"
    if profile.tier == "test":
        return "acceptance"
    return "research"


def _chunks(items: list[AgentProfile], size: int) -> Iterable[list[AgentProfile]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _safe_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _FLEET_ID.fullmatch(safe):
        raise ValueError("invalid fleet plan id")
    return safe


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
