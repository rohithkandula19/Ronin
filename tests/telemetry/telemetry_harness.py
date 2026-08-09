"""Shared fixtures for ``tests/telemetry``. Offline; nothing here opens a socket.

Named ``telemetry_harness`` rather than ``harness`` because the test trees are
deliberately not packages, so every module under ``tests/*/`` is importable by its bare
basename and two directories cannot share one — ``tests/tools/harness.py`` already owns
that name, and shadowing it breaks seven unrelated tool tests.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from ronin.telemetry import (
    Environment,
    OsName,
    Outcome,
    PayloadValue,
    TaskCategory,
    TaskOutcome,
    TaxonomyClass,
    duration_bucket,
    turn_bucket,
)

#: The repository root, found by walking up from this file rather than from ``cwd`` —
#: pytest may be invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: A fixed environment, so a payload built in a test is byte-identical on every machine.
#: Nothing in ``tests/telemetry`` may call ``local_environment``: it reads the real
#: interpreter version and would make every assertion depend on the runner.
FIXED_ENVIRONMENT = Environment(
    ronin_version="1.0.0", python_version="3.11", os_name=OsName.LINUX
)


def payload(
    *,
    task_category: TaskCategory = TaskCategory.FIX,
    outcome: Outcome = Outcome.DONE,
    taxonomy_class: TaxonomyClass = TaxonomyClass.NONE,
    turns: int = 3,
    seconds: float = 7.5,
    gate_denied: bool = False,
) -> TaskOutcome:
    """A payload with every field pinned, for tests that care about only one of them."""
    return TaskOutcome(
        ronin_version=FIXED_ENVIRONMENT.ronin_version,
        python_version=FIXED_ENVIRONMENT.python_version,
        os_name=FIXED_ENVIRONMENT.os_name,
        task_category=task_category,
        outcome=outcome,
        taxonomy_class=taxonomy_class,
        turn_bucket=turn_bucket(turns),
        duration_bucket=duration_bucket(seconds),
        gate_denied=gate_denied,
    )


@dataclass(slots=True)
class RecordingSender:
    """A sender that records what it was handed. Mutable because recording is the point.

    ``fail`` and ``hang`` exist so the two failure modes that must not break a turn — a
    sender that raises and a sender that never returns — are exercised through the same
    object as the happy path.
    """

    calls: list[Mapping[str, PayloadValue]] = field(default_factory=list)
    fail: bool = False
    hang: bool = False

    async def __call__(self, fields: Mapping[str, PayloadValue]) -> None:
        self.calls.append(fields)
        if self.fail:
            raise RuntimeError("the endpoint is on fire")
        if self.hang:
            import asyncio

            await asyncio.sleep(3600)

    @property
    def never_called(self) -> bool:
        return not self.calls


def unreachable_sender() -> RecordingSender:
    """A sender whose being called at all is the failure under test."""
    return RecordingSender()


def load_docsite_generator() -> ModuleType:
    """Import ``docs/site/generate.py`` by path.

    It lives in ``docs/`` rather than in a package because it is a documentation build
    step, not runtime code, and ``docs/`` is not importable. Loading it by path is the
    cost of that placement, and it is cheaper than adding a package for one script.
    """
    path = REPO_ROOT / "docs" / "site" / "generate.py"
    spec = importlib.util.spec_from_file_location("ronin_docsite_generate", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered_pages() -> dict[str, str]:
    """What the generator says every generated page should contain, right now."""
    module = load_docsite_generator()
    render_all = cast("Any", module).render_all
    return cast("dict[str, str]", render_all())


__all__ = [
    "FIXED_ENVIRONMENT",
    "REPO_ROOT",
    "RecordingSender",
    "load_docsite_generator",
    "payload",
    "rendered_pages",
    "unreachable_sender",
]
