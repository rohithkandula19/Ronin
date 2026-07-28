"""Scalable, local-first specialist profile catalog for orchestration.

The catalog deliberately separates *available profiles* from *active agents*.
It can index thousands of narrowly named specialists, while an orchestrated run
selects only a small, task-relevant team. That keeps context, cost, parallelism,
and approval surface bounded.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping


_PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
_TIERS = {"explore", "test", "write"}
DEFAULT_MANIFEST = ".ronin/agents.json"
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_PROFILE_INSTRUCTIONS = 6_000
MAX_PROFILE_DESCRIPTION = 500
MAX_PROFILE_TAGS = 32

# Repository traits that map cleanly onto the generated domain catalog.  These
# are deliberately coarse: routing should give the planner useful context, not
# pretend it has fully understood the codebase before any agent has read it.
_EXTENSION_TAGS = {
    ".py": ("python",),
    ".js": ("javascript", "web"),
    ".jsx": ("javascript", "frontend", "web"),
    ".ts": ("typescript", "web"),
    ".tsx": ("typescript", "frontend", "web"),
    ".go": ("go", "backend"),
    ".rs": ("rust", "backend"),
    ".java": ("java", "backend"),
    ".cs": ("dotnet", "backend"),
    ".rb": ("ruby", "backend"),
    ".php": ("php", "backend"),
    ".swift": ("swift", "mobile"),
    ".kt": ("kotlin", "mobile"),
    ".sql": ("database", "data"),
    ".dockerfile": ("containers", "devops"),
    ".yaml": ("devops",),
    ".yml": ("devops",),
}
_PATH_TAGS = {
    "docker": ("containers", "devops"),
    "k8s": ("kubernetes", "devops"),
    "kube": ("kubernetes", "devops"),
    "terraform": ("cloud", "devops"),
    "infra": ("cloud", "devops"),
    "migration": ("migration", "database"),
    "test": ("testing",),
    "docs": ("documentation",),
    "release": ("release", "build"),
    "ci": ("build", "release"),
    "security": ("security",),
    "auth": ("identity", "security"),
    "payment": ("payments",),
    "observability": ("observability",),
}
_ROOT_MARKER_TAGS = {
    "pyproject.toml": ("python", "build"),
    "requirements.txt": ("python", "dependencies"),
    "package.json": ("javascript", "typescript", "build"),
    "cargo.toml": ("rust", "build"),
    "go.mod": ("go", "build"),
    "pom.xml": ("java", "build"),
    "build.gradle": ("java", "kotlin", "build"),
    "dockerfile": ("containers", "devops"),
    "makefile": ("build",),
}

# A generated domain x discipline matrix gives Ronin a large, inspectable pool
# without hiding thousands of hand-maintained prompts in source. 45 x 26 = 1170.
DOMAINS = (
    "api", "backend", "frontend", "mobile", "database", "data", "analytics",
    "machine-learning", "llm", "python", "typescript", "javascript", "java",
    "go", "rust", "cpp", "dotnet", "ruby", "php", "swift", "kotlin", "devops",
    "cloud", "containers", "kubernetes", "networking", "security", "privacy",
    "identity", "payments", "observability", "performance", "reliability",
    "testing", "accessibility", "documentation", "release", "build", "dependencies",
    "migration", "concurrency", "distributed-systems", "cli", "developer-experience",
    "web",
)
DISCIPLINES = (
    "architect", "investigator", "implementer", "reviewer", "tester", "debugger",
    "security-auditor", "performance-engineer", "reliability-engineer", "api-designer",
    "database-engineer", "frontend-engineer", "backend-engineer", "migration-specialist",
    "refactoring-specialist", "compatibility-engineer", "dependency-auditor",
    "observability-engineer", "incident-responder", "accessibility-auditor",
    "documentation-writer", "release-engineer", "build-engineer", "verifier",
    "data-engineer", "quality-engineer",
)
_WRITE_DISCIPLINES = {
    "implementer", "debugger", "migration-specialist", "refactoring-specialist",
    "frontend-engineer", "backend-engineer", "database-engineer", "data-engineer",
}
_TEST_DISCIPLINES = {
    "tester", "debugger", "performance-engineer", "reliability-engineer",
    "incident-responder", "build-engineer", "quality-engineer",
}


@dataclass(frozen=True)
class AgentProfile:
    """A specialist prompt and its least-privilege tool tier."""

    key: str
    description: str
    system: str
    tags: tuple[str, ...]
    tier: str = "explore"
    source: str = "builtin"

    def __post_init__(self) -> None:
        if not _PROFILE_ID.fullmatch(self.key):
            raise ValueError(f"invalid agent profile id: {self.key!r}")
        if not self.description.strip() or not self.system.strip():
            raise ValueError(f"agent profile {self.key!r} needs description and instructions")
        if self.tier not in _TIERS:
            raise ValueError(f"agent profile {self.key!r} has invalid tier: {self.tier!r}")

    @property
    def read_only(self) -> bool:
        return self.tier == "explore"


@dataclass(frozen=True)
class RepositorySignals:
    """Compact, explainable evidence used for specialist routing.

    ``relevant_files`` and ``symbols`` come from the local BM25 repository
    index.  ``tags`` additionally includes language and project-marker hints.
    All fields are plain immutable data so callers can persist and evaluate a
    routing decision without rebuilding an index.
    """

    tags: tuple[str, ...] = ()
    relevant_files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileSelection:
    """A bounded specialist roster together with its routing evidence."""

    profiles: tuple[AgentProfile, ...]
    reasons: Mapping[str, tuple[str, ...]]
    repository: RepositorySignals = RepositorySignals()


def _tier_for(discipline: str) -> str:
    if discipline in _WRITE_DISCIPLINES:
        return "write"
    if discipline in _TEST_DISCIPLINES:
        return "test"
    return "explore"


@lru_cache(maxsize=1)
def generated_profiles() -> tuple[AgentProfile, ...]:
    """Return the generated 1,170-profile specialist matrix."""
    profiles: list[AgentProfile] = []
    for domain in DOMAINS:
        for discipline in DISCIPLINES:
            key = f"{domain}-{discipline}"
            tier = _tier_for(discipline)
            profiles.append(AgentProfile(
                key=key,
                description=f"{discipline.replace('-', ' ')} focused on {domain.replace('-', ' ')}",
                system=(
                    f"You are Ronin's {discipline.replace('-', ' ')} specialist for "
                    f"{domain.replace('-', ' ')}. Work only on the assigned subtask, "
                    "ground claims in the repository and command output, and report "
                    "concrete evidence. Respect all tool approvals and do not claim "
                    "verification that did not run."
                ),
                tags=tuple(dict.fromkeys((domain, discipline, *domain.split("-"), *discipline.split("-")))),
                tier=tier,
            ))
    return tuple(profiles)


def load_project_profiles(root: Path | str, manifest: Path | str | None = None) -> tuple[AgentProfile, ...]:
    """Load project-owned profiles from ``.ronin/agents.json`` without code execution."""
    repo = Path(root).resolve()
    path = Path(manifest) if manifest is not None else repo / DEFAULT_MANIFEST
    if not path.is_absolute():
        path = repo / path
    if not path.is_file():
        return ()
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError(f"agent manifest {path} exceeds the 5 MiB safety limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read agent manifest {path}: {exc}") from exc
    entries = raw.get("agents") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"agent manifest {path} must be a JSON object with an agents list")
    if len(entries) > 10_000:
        raise ValueError(f"agent manifest {path} exceeds the 10,000-profile safety limit")

    profiles: list[AgentProfile] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"agent manifest entry {index} must be an object")
        key = entry.get("id")
        description = entry.get("description")
        system = entry.get("instructions")
        tags = entry.get("tags", [])
        tier = entry.get("tier", "explore")
        if not isinstance(key, str) or not isinstance(description, str) or not isinstance(system, str):
            raise ValueError(f"agent manifest entry {index} needs string id, description, and instructions")
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise ValueError(f"agent manifest entry {index} tags must be a string list")
        if not isinstance(tier, str):
            raise ValueError(f"agent manifest entry {index} tier must be a string")
        if len(description) > MAX_PROFILE_DESCRIPTION or len(system) > MAX_PROFILE_INSTRUCTIONS:
            raise ValueError(f"agent manifest entry {index} exceeds the profile text safety limit")
        if len(tags) > MAX_PROFILE_TAGS:
            raise ValueError(f"agent manifest entry {index} exceeds the {MAX_PROFILE_TAGS}-tag limit")
        profiles.append(AgentProfile(
            key=key.strip().lower(),
            description=description.strip(),
            system=system.strip(),
            tags=tuple(tag.strip().lower() for tag in tags),
            tier=tier.strip().lower(),
            source="project",
        ))
    return tuple(profiles)


def build_catalog(
    core_profiles: Iterable[AgentProfile],
    root: Path | str,
    *,
    manifest: Path | str | None = None,
) -> tuple[AgentProfile, ...]:
    """Merge core, generated, and project profiles while rejecting collisions."""
    profiles = tuple(core_profiles) + generated_profiles() + load_project_profiles(root, manifest)
    seen: set[str] = set()
    duplicates: set[str] = set()
    for profile in profiles:
        if profile.key in seen:
            duplicates.add(profile.key)
        seen.add(profile.key)
    if duplicates:
        raise ValueError(f"duplicate agent profile ids: {', '.join(sorted(duplicates)[:8])}")
    return profiles


def _task_terms(task: str) -> set[str]:
    return {_normalize_term(term) for term in re.findall(r"[a-z0-9]+", task.lower())}


def _normalize_term(term: str) -> str:
    """Apply a tiny, deterministic singularization for catalog-tag matching."""
    if len(term) > 3 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def repository_signals(task: str, root: Path | str, *, max_files: int = 12) -> RepositorySignals:
    """Collect local repository evidence for a routing decision.

    The existing BM25 index identifies files relevant to ``task``.  This helper
    turns those paths and symbols into catalog-domain tags, and augments them
    with a few root configuration markers.  It is offline, has no model call,
    and tolerates unreadable or empty repositories by returning no signals.
    """
    try:
        from .repo_map import get_index

        index = get_index(root)
        ranked = index.search(task, max(1, min(max_files, 25)))
    except Exception:  # noqa: BLE001 - routing must degrade to task terms
        ranked = []

    files: list[str] = []
    symbols: list[str] = []
    tags: set[str] = set()
    for _score, doc in ranked:
        files.append(doc.path)
        suffix = Path(doc.path).suffix.lower()
        tags.update(_EXTENSION_TAGS.get(suffix, ()))
        for path_term in _task_terms(doc.path.replace("/", " ").replace(".", " ")):
            tags.update(_PATH_TAGS.get(path_term, ()))
        symbols.extend(name for name, _line in doc.symbols[:12])

    repo = Path(root)
    try:
        names = {path.name.lower() for path in repo.iterdir()}
    except OSError:
        names = set()
    for marker, marker_tags in _ROOT_MARKER_TAGS.items():
        if marker in names:
            tags.update(marker_tags)

    return RepositorySignals(
        tags=tuple(sorted(tags)),
        relevant_files=tuple(files),
        symbols=tuple(symbols[:48]),
    )


def _selection_details(
    task: str,
    profiles: Iterable[AgentProfile],
    *,
    core_keys: Iterable[str],
    max_agents: int = 8,
    repository: RepositorySignals | None = None,
) -> ProfileSelection:
    """Select a roster and retain the task/repository evidence behind it."""
    all_profiles = tuple(profiles)
    core_set = set(core_keys)
    core = tuple(profile for profile in all_profiles if profile.key in core_set)
    if len(core) != len(core_set):
        missing = sorted(core_set - {profile.key for profile in core})
        raise ValueError(f"agent catalog is missing core roles: {', '.join(missing)}")
    if max_agents < len(core):
        raise ValueError(f"max_agents must be at least {len(core)} to retain the core team")
    if max_agents > 32:
        raise ValueError("max_agents cannot exceed 32; split larger work into orchestration waves")

    repository = repository or RepositorySignals()
    task_terms = _task_terms(task)
    repo_terms = {_normalize_term(term) for term in repository.tags}
    reasons: dict[str, tuple[str, ...]] = {
        profile.key: ("core workflow role",) for profile in core
    }
    ranked: list[tuple[int, str, AgentProfile, tuple[str, ...]]] = []
    for profile in all_profiles:
        if profile.key in core_set:
            continue
        tag_terms = {
            _normalize_term(term)
            for term in (*profile.tags, *profile.key.split("-"))
        }
        task_hits = tuple(sorted(task_terms & tag_terms))
        repo_hits = tuple(sorted(repo_terms & tag_terms))
        score = len(task_hits) * 3 + len(repo_hits)
        key_hit = profile.key in task.lower()
        if key_hit:
            score += 3
        if not score:
            continue
        why = tuple(
            [*(f"task:{term}" for term in task_hits),
             *(f"repo:{term}" for term in repo_hits),
             *(("exact profile mention",) if key_hit else ())]
        )
        ranked.append((score, profile.key, profile, why))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    extras = ranked[:max_agents - len(core)]
    for _score, key, _profile, why in extras:
        reasons[key] = why
    return ProfileSelection(
        profiles=core + tuple(item[2] for item in extras),
        reasons=reasons,
        repository=repository,
    )


def select_profiles(
    task: str,
    profiles: Iterable[AgentProfile],
    *,
    core_keys: Iterable[str],
    max_agents: int = 8,
) -> tuple[AgentProfile, ...]:
    """Select a bounded, diverse specialist team for a task. Pure.

    Core roles are always retained; everything else is scored from explicit tags
    and only matching profiles are added. This prevents a large catalog from
    becoming a large prompt or an unbounded bill.
    """
    return _selection_details(
        task, profiles, core_keys=core_keys, max_agents=max_agents,
    ).profiles


def select_profiles_for_repository(
    task: str,
    profiles: Iterable[AgentProfile],
    *,
    core_keys: Iterable[str],
    repository: RepositorySignals,
    max_agents: int = 8,
) -> ProfileSelection:
    """Select a bounded team using task terms *and* supplied repo evidence.

    Keeping this pure is intentional: tests and the evaluation harness can feed
    fixture signals without touching the filesystem or the BM25 index.
    """
    return _selection_details(
        task, profiles, core_keys=core_keys, max_agents=max_agents,
        repository=repository,
    )
