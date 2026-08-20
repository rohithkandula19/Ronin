"""Artifact/contract validation: did the work produce what it promised.

A task that says "writes ``report.json`` with a ``score`` field" should be checked against
that promise, not trusted. This validates a declared :class:`ArtifactContract` against a
directory: the named files exist, the ones declared as JSON parse, and the keys they must
carry are present. Every shortfall is a :class:`ContractViolation` with a plain reason — a
missing artifact is a finding, never a crash.

Reads the filesystem (it must, to check what was written) and nothing else — no network,
no subprocess — so it is tested against a ``tmp_path`` with the files written by hand.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArtifactRule:
    """One promised artifact and what makes it valid.

    ``json_keys`` implies the file must be JSON *and* an object carrying those top-level
    keys — the common "it wrote a report with these fields" contract.
    """

    path: str
    must_exist: bool = True
    json_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactContract:
    """The set of artifacts a task promised to produce."""

    rules: tuple[ArtifactRule, ...] = ()


@dataclass(frozen=True, slots=True)
class ContractViolation:
    """One promise the produced artifacts did not keep."""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ContractResult:
    """The outcome: satisfied when nothing was violated."""

    violations: tuple[ContractViolation, ...] = field(default_factory=tuple)

    @property
    def satisfied(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.satisfied:
            return "contract satisfied"
        return f"{len(self.violations)} violation(s): " + "; ".join(
            f"{v.path} ({v.reason})" for v in self.violations
        )


def _check(rule: ArtifactRule, root: Path) -> ContractViolation | None:
    target = root / rule.path
    if not target.is_file():
        # A declared-but-absent artifact is a violation only when it was required; an
        # optional artifact (must_exist=False) that is missing has nothing more to check.
        return ContractViolation(rule.path, "missing") if rule.must_exist else None
    if not rule.json_keys:
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ContractViolation(rule.path, f"not valid json: {type(exc).__name__}")
    if not isinstance(data, Mapping):
        return ContractViolation(rule.path, "json is not an object")
    missing = [key for key in rule.json_keys if key not in data]
    if missing:
        return ContractViolation(rule.path, f"missing key(s): {', '.join(missing)}")
    return None


def validate_contract(contract: ArtifactContract, root: Path) -> ContractResult:
    """Check every rule against ``root`` and collect the violations, in rule order."""
    violations = [v for rule in contract.rules if (v := _check(rule, root)) is not None]
    return ContractResult(violations=tuple(violations))


__all__ = [
    "ArtifactContract",
    "ArtifactRule",
    "ContractResult",
    "ContractViolation",
    "validate_contract",
]
