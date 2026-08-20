"""The eval suite: a task tree, a bounded runner, and a failure taxonomy.

Without this you are guessing. A pass rate tells you the agent is worse than you
want; the taxonomy in :mod:`ronin.evals.taxonomy` tells you **whether to fix the
harness or the model**, which is the only output of this package anyone acts on.

Four seams keep the whole thing honest, and none of them is optional:

* the **model is injected** (:data:`ronin.evals.adapters.RunAgent`), so the suite runs
  offline against a scripted fake and never constructs a provider client;
* the **subprocess runner is injected** (``ronin.verify.runner.CommandRunner``), so
  ``verify.sh`` is fake-able;
* the **clock is injected**, which is what makes two runs byte-identical;
* the **answer key is never copied** — :func:`ronin.evals.task.materialize` refuses to
  put a task's ``solution/`` in the workspace, because a suite that leaks the answer
  reports a pass rate that means nothing and nothing would notice.

Typical use::

    tasks = load_suite("tests/evals")
    report = await run_suite(tasks, v2_adapter(my_factory), config=RunnerConfig(parallel=8))
    print(report_markdown(report))

``swebench`` and ``duel`` live in this package too and are exported from their own
modules; they are not re-exported here so that this file does not have to change
every time one of them gains a name.
"""

from __future__ import annotations

from .adapters import (
    AgentFactory,
    AgentLike,
    OpenedAgent,
    RunAgent,
    ledger_usage,
    outcome_from_events,
    sdk_agent_factory,
    usage_from_budget,
    v1_adapter,
    v2_adapter,
)
from .report import (
    Aggregate,
    Distribution,
    RunReport,
    Scoreboard,
    ScoreboardRow,
    TaskRow,
    TaxonomyBreakdown,
    build_report,
    report_json,
    report_markdown,
    report_payload,
    scoreboard,
    scoreboard_markdown,
)
from .runner import EvalRunner, RunnerConfig, SuiteSelection, run_suite
from .task import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TIMEOUT_SECONDS,
    SUITE_RELATIVE_ROOT,
    EvalTask,
    Manifest,
    ManifestMismatch,
    NeedsNetwork,
    TaskError,
    Workspace,
    check_manifest,
    load_manifest,
    load_suite,
    load_task,
    load_tasks,
    materialize,
)
from .taxonomy import (
    BLAME,
    CLASSIFIERS,
    AgentOutcome,
    FailureClass,
    Finding,
    RunRecord,
    TaskResult,
    ToolCallRecord,
    UsageRecord,
    VerifyProbe,
    classify,
    count_classes,
    fixture_symbols,
    judge,
)

__all__ = [
    "BLAME",
    "CLASSIFIERS",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "SUITE_RELATIVE_ROOT",
    "AgentFactory",
    "AgentLike",
    "AgentOutcome",
    "Aggregate",
    "Distribution",
    "EvalRunner",
    "EvalTask",
    "FailureClass",
    "Finding",
    "Manifest",
    "ManifestMismatch",
    "NeedsNetwork",
    "OpenedAgent",
    "RunAgent",
    "RunRecord",
    "RunReport",
    "RunnerConfig",
    "Scoreboard",
    "ScoreboardRow",
    "SuiteSelection",
    "TaskError",
    "TaskResult",
    "TaskRow",
    "TaxonomyBreakdown",
    "ToolCallRecord",
    "UsageRecord",
    "VerifyProbe",
    "Workspace",
    "build_report",
    "check_manifest",
    "classify",
    "count_classes",
    "fixture_symbols",
    "judge",
    "ledger_usage",
    "load_manifest",
    "load_suite",
    "load_task",
    "load_tasks",
    "materialize",
    "outcome_from_events",
    "report_json",
    "report_markdown",
    "report_payload",
    "run_suite",
    "scoreboard",
    "scoreboard_markdown",
    "sdk_agent_factory",
    "usage_from_budget",
    "v1_adapter",
    "v2_adapter",
]
