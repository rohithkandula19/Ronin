"""One governed durable-runtime factory for CLI execution surfaces.

This module intentionally owns no approval policy.  It only creates the shared
kernel journal and budget; each caller continues to supply its existing
read-only/write/approval restrictions to ``run_code_agent`` or ``run_ask``.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from ronin_agent_patterns import BudgetLimits, RunBudget, RunJournal


@dataclass(frozen=True)
class SurfaceRuntime:
    journal: RunJournal
    budget: RunBudget
    run_id: str


def surface_runtime(
    root: Path | str,
    surface: str,
    identity: str,
    *,
    max_tokens: int = 50_000,
    max_tool_calls: int = 80,
    max_wall_time_seconds: float = 900,
    limits: BudgetLimits | None = None,
) -> SurfaceRuntime:
    """Create a project-local journal and bounded budget for one agent turn.

    ``identity`` is hashed before it becomes part of the run id, so chat ids,
    session ids, and task names do not leak into filenames or journal metadata.
    """
    safe_surface = "".join(char for char in surface.lower() if char.isalnum() or char in "-_") or "agent"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    base = Path(root).resolve() / ".ronin" / "durable-runs"
    journal = RunJournal(base / f"{safe_surface}.sqlite")
    return SurfaceRuntime(
        journal=journal,
        budget=RunBudget(limits or BudgetLimits(
            max_tokens=max_tokens,
            max_tool_calls=max_tool_calls,
            max_wall_time_seconds=max_wall_time_seconds,
        )),
        # Each invocation gets a new durable run. The opaque identity digest is
        # correlation metadata only; reusing it as the primary run id would make
        # a recurring task collide with its previous completed execution.
        run_id=f"{safe_surface}-{digest}-{uuid.uuid4().hex[:12]}",
    )
