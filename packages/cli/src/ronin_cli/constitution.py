"""Repository-owned operating rules for autonomous work.

The constitution is intentionally small, JSON-only, and local to a checkout.
It lets a repository state non-negotiable boundaries that are enforced below an
agent prompt: protected paths cannot be modified and tagged work can require a
specialist role.  It is a guardrail, not a policy language or remote service.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PATH = ".ronin/constitution.json"
MAX_BYTES = 128 * 1024


@dataclass(frozen=True)
class RepositoryConstitution:
    protected_paths: tuple[str, ...] = ()
    required_roles: dict[str, tuple[str, ...]] | None = None
    max_agents: int | None = None
    require_sandbox_for_write: bool = False

    def __post_init__(self) -> None:
        if self.max_agents is not None and not 1 <= self.max_agents <= 32:
            raise ValueError("constitution max_agents must be between 1 and 32")

    def protects(self, path: Path | str, *, root: Path | str) -> bool:
        """Return whether ``path`` matches a protected project-relative glob."""
        try:
            relative = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            return False
        candidate = PurePosixPath(relative)
        return any(candidate.match(pattern) for pattern in self.protected_paths)

    def validate_team(self, profiles: Iterable[str], *, tags: Iterable[str], write: bool) -> None:
        selected = {profile.strip().lower() for profile in profiles}
        if self.max_agents is not None and len(selected) > self.max_agents:
            raise ValueError(
                f"constitution limits this repository to {self.max_agents} agents (selected {len(selected)})"
            )
        if write and self.require_sandbox_for_write and not _sandbox_requested():
            raise ValueError("constitution requires RONIN_BACKEND for write orchestration")
        for tag in {str(tag).lower() for tag in tags}:
            for required in (self.required_roles or {}).get(tag, ()):
                if not any(profile == required or profile.endswith("-" + required) for profile in selected):
                    raise ValueError(
                        f"constitution requires role {required!r} for {tag!r} work"
                    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protected_paths": list(self.protected_paths),
            "required_roles": {key: list(value) for key, value in (self.required_roles or {}).items()},
            "max_agents": self.max_agents,
            "require_sandbox_for_write": self.require_sandbox_for_write,
        }


def load_constitution(root: Path | str) -> RepositoryConstitution:
    """Load a bounded JSON constitution, or a permissive empty one when absent."""
    path = Path(root).resolve() / PATH
    if not path.exists():
        return RepositoryConstitution()
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("constitution exceeds 128 KiB")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read constitution: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("constitution must be a JSON object")
    unknown = set(raw) - {"protected_paths", "required_roles", "max_agents", "require_sandbox_for_write"}
    if unknown:
        raise ValueError(f"constitution has unknown fields: {', '.join(sorted(unknown))}")
    protected = raw.get("protected_paths", [])
    if not isinstance(protected, list) or not all(_valid_pattern(item) for item in protected):
        raise ValueError("constitution protected_paths must be safe non-empty relative glob strings")
    roles_raw = raw.get("required_roles", {})
    if not isinstance(roles_raw, dict):
        raise ValueError("constitution required_roles must be an object")
    roles: dict[str, tuple[str, ...]] = {}
    for tag, values in roles_raw.items():
        if not isinstance(tag, str) or not tag.strip() or not isinstance(values, list):
            raise ValueError("constitution required_roles must map tag strings to role string lists")
        cleaned = tuple(value.strip().lower() for value in values if isinstance(value, str) and value.strip())
        if len(cleaned) != len(values):
            raise ValueError("constitution role names must be non-empty strings")
        roles[tag.strip().lower()] = cleaned
    max_agents = raw.get("max_agents")
    if max_agents is not None and (isinstance(max_agents, bool) or not isinstance(max_agents, int)):
        raise ValueError("constitution max_agents must be an integer or null")
    require_sandbox = raw.get("require_sandbox_for_write", False)
    if not isinstance(require_sandbox, bool):
        raise ValueError("constitution require_sandbox_for_write must be boolean")
    return RepositoryConstitution(tuple(protected), roles, max_agents, require_sandbox)


def starter_constitution() -> dict[str, Any]:
    """Return an intentionally conservative starter users may edit explicitly."""
    return {
        "protected_paths": [".github/workflows/**", "migrations/**"],
        "required_roles": {"payments": ["security-auditor"], "security": ["security-auditor"]},
        "max_agents": 8,
        "require_sandbox_for_write": False,
    }


def write_starter(root: Path | str) -> Path:
    path = Path(root).resolve() / PATH
    if path.exists():
        raise ValueError(f"constitution already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(starter_constitution(), indent=2) + "\n", encoding="utf-8")
    return path


def _valid_pattern(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.startswith("/")
        and ".." not in Path(value).parts
    )


def _sandbox_requested() -> bool:
    import os

    return bool(os.environ.get("RONIN_BACKEND", "").strip())
