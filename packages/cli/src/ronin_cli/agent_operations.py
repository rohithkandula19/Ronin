"""One local snapshot for the agent platform's operational control plane."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .agent_observability import dashboard_counts
from .agent_queue import AgentQueue
from .agent_recovery import list_recoverable_runs
from .autonomy_ledger import verify_ledger
from .model_scorecards import ModelScorecardStore
from .provider_health_store import ProviderHealthStore


def operations_snapshot(root: Path | str) -> dict[str, Any]:
    """Aggregate existing local records without provider calls or mutation."""
    queue = AgentQueue(root).list()
    ledger = verify_ledger(root)
    return {
        "runs": dashboard_counts(root),
        "queue": dict(Counter(job.status for job in queue)),
        "recoverable": list_recoverable_runs(root, limit=50),
        "providers": ProviderHealthStore(root).view(),
        "scorecards": ModelScorecardStore(root).list(),
        "ledger": ledger,
    }
