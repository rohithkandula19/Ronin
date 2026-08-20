"""End-to-end smoke test for the stage ablation over a tiny, real suite.

Everything here is the real machinery except the checkpoints: no GPU weights exist yet, so each
stage is a *scripted agent* standing in for one. But the suite is loaded from real task dirs,
run through the real ``ronin.evals`` runner, and passed/failed by the **real ``verify.sh``
subprocess** against the file the agent actually wrote — then folded into a real ``RunReport``
and reduced by the shipped wiring (`stage_run_from_report`) plus a real decode probe
(`syntax_probe`). The test pins the whole path: all five metrics populate, and the
base→+SFT→+DPO→+GRPO progression comes out monotone on the metrics the scripts make monotone.

This is the counterpart to the placeholder smoke (`test_placeholder_table_*`): that one proves
the table *renders* with no model; this one proves the table *is produced by the real suite*.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The eval-suite test harness lives beside the main suite, not in the training package; add it
# by a path resolved from this file so the test runs under `uv run --project training pytest`
# regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tests" / "evalsuite"))

# ronin (the runtime) and the harness are optional in a bare training checkout; skip cleanly
# rather than erroring if the suite is not importable here.
pytest.importorskip("ronin.evals")
evals_harness = pytest.importorskip("evals_harness")

from ronin_training.adapter.stage_ablation import (  # noqa: E402
    BASE,
    DPO,
    GRPO,
    KIMI,
    SFT,
    STAGE_ORDER,
    render_markdown,
    run_ablation,
    stage_runner,
    syntax_probe,
)
from ronin_training.eval_runner import ronin_tools_for_template  # noqa: E402

from ronin.core.types import Budget  # noqa: E402
from ronin.evals import RunnerConfig, load_suite, run_suite, v2_adapter  # noqa: E402
from ronin.evals.adapters import OpenedAgent  # noqa: E402

CONDITIONAL_VERIFY = evals_harness.CONDITIONAL_VERIFY
ScriptedAgent = evals_harness.ScriptedAgent
Step = evals_harness.Step
frozen_clock = evals_harness.frozen_clock
write_manifest = evals_harness.write_manifest
write_task = evals_harness.write_task

BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in ronin_tools_for_template()}
TASK_IDS = ["add-0", "add-1", "add-2", "add-3"]


def _build_suite(root: Path) -> None:
    for task_id in TASK_IDS:
        write_task(
            root,
            task_id,
            category="edit",
            prompt="make add return the sum",
            fixture={"app.py": BROKEN},
            solution={"app.py": FIXED},
            verify=CONDITIONAL_VERIFY,
            files_expected=["app.py"],
        )
    write_manifest(root, TASK_IDS)


def _tc(name: str, arguments: dict) -> str:
    return (
        f"<ronin:tool_call>{json.dumps({'name': name, 'arguments': arguments})}</ronin:tool_call>"
    )


# Raw decode-probe completions; validity climbs across stages.
_GOOD = _tc("read_file", {"path": "app.py"})
_BADTAG = '<tool_call>{"name": "read_file"}</tool_call>'  # v1 dialect → invalid
_UNKNOWN = _tc("frobnicate", {})  # not in the registry → invalid
_PROSE = "Let me think about this first."  # no attempt → excluded from the denominator

# fixes = tasks repaired (drives pass@1 via the real verifier); recover = fail then a *different*
# action that works (a genuine recovery); usd = per-task spend; turns_pad = extra read turns.
_STAGES = {
    BASE: dict(
        fixes=0,
        recover=False,
        usd=0.002,
        turns_pad=2,
        completions=[_GOOD, _BADTAG, _UNKNOWN, _PROSE],
    ),
    SFT: dict(
        fixes=2, recover=False, usd=0.002, turns_pad=1, completions=[_GOOD, _GOOD, _BADTAG, _PROSE]
    ),
    DPO: dict(
        fixes=3, recover=True, usd=0.002, turns_pad=1, completions=[_GOOD, _GOOD, _GOOD, _UNKNOWN]
    ),
    GRPO: dict(
        fixes=4, recover=True, usd=0.003, turns_pad=0, completions=[_GOOD, _GOOD, _GOOD, _GOOD]
    ),
    KIMI: dict(
        fixes=4, recover=False, usd=0.040, turns_pad=0, completions=[_GOOD, _GOOD, _GOOD, _GOOD]
    ),
}
_MODELS = {
    BASE: "qwen2.5-coder-1.5b",
    SFT: "qwen+sft",
    DPO: "qwen+sft+dpo",
    GRPO: "qwen+sft+dpo+grpo",
    KIMI: "kimi (frontier)",
}


def _make_factory(*, fixes: int, recover: bool, usd: float, turns_pad: int):
    async def factory(eval_task: object, workspace: Path) -> object:
        index = TASK_IDS.index(eval_task.id)  # type: ignore[attr-defined]
        steps: list[object] = [Step(tool="read_file", path="app.py")]
        steps += [Step(tool="read_file", path="app.py") for _ in range(turns_pad)]
        if index < fixes:
            if recover:  # a failed edit, then a DIFFERENT action that works — a real recovery
                steps.append(
                    Step(
                        tool="edit_file",
                        ok=False,
                        error="patch did not apply",
                        arguments={"path": "app.py", "mode": "patch"},
                    )
                )
                steps.append(Step(tool="write_file", path="app.py", content=FIXED))
            else:
                steps.append(Step(tool="edit_file", path="app.py", content=FIXED))
        else:  # a miss: two identical failed edits — the verifier fails it, and the repeat is
            steps.append(
                Step(tool="edit_file", path="app.py", content=BROKEN, ok=False, error="nope")
            )
            steps.append(
                Step(tool="edit_file", path="app.py", content=BROKEN, ok=False, error="nope")
            )
        agent = ScriptedAgent(
            steps=steps, workspace=workspace, budget=Budget(spent_tokens=800, spent_usd=usd)
        )
        return OpenedAgent(agent=agent, registered_tools=tuple(SCHEMAS))

    return factory


def _make_probe(completions: list[str]):
    cases = [{"out": text} for text in completions]

    async def probe() -> object:
        return syntax_probe(cases, lambda case: SimpleNamespace(text=case["out"]), schemas=SCHEMAS)

    return probe


def _run_ablation(root: Path):
    tasks = load_suite(root)

    def make_run_suite(stage: str):
        cfg = _STAGES[stage]
        adapter = v2_adapter(
            _make_factory(
                fixes=cfg["fixes"],
                recover=cfg["recover"],
                usd=cfg["usd"],
                turns_pad=cfg["turns_pad"],
            )
        )

        async def _run() -> object:
            return await run_suite(
                tasks,
                adapter,
                config=RunnerConfig(
                    parallel=2, label=stage, model=_MODELS[stage], suite_root=str(root)
                ),
                clock=frozen_clock(),
            )

        return _run

    runners = {
        stage: stage_runner(
            stage=stage,
            run_suite=make_run_suite(stage),
            validity_probe=_make_probe(_STAGES[stage]["completions"]),
        )
        for stage in STAGE_ORDER
    }
    return asyncio.run(
        run_ablation(
            suite_id="tiny-add-suite",
            suite_cases=len(tasks),
            runners=runners,
            provenance=(
                "REAL end-to-end run: ronin.evals.run_suite over scripted agents + real verify.sh",
            ),
        )
    )


def test_stage_ablation_runs_end_to_end_over_the_real_suite(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    _build_suite(root)
    table = _run_ablation(root)

    rows = {row.stage: row for row in table.rows}
    assert [row.stage for row in table.rows] == list(STAGE_ORDER)  # all five stages ran, in order

    # pass@1 is decided by the REAL verify.sh over the file each scripted agent wrote.
    assert rows[BASE].pass_at_1 == 0.0
    assert rows[SFT].pass_at_1 == 0.5
    assert rows[DPO].pass_at_1 == 0.75
    assert rows[GRPO].pass_at_1 == 1.0
    assert rows[KIMI].pass_at_1 == 1.0

    # tool-syntax validity comes from the decode probe over raw completions, not the suite.
    assert rows[BASE].tool_validity == pytest.approx(1 / 3)
    assert rows[SFT].tool_validity == pytest.approx(2 / 3)
    assert rows[DPO].tool_validity == pytest.approx(0.75)
    assert rows[GRPO].tool_validity == 1.0

    # recovery is reconstructed from the parsed tool calls: a fix that retries with a *different*
    # action recovers; a miss that repeats the same failing call does not; Kimi never fails.
    assert rows[BASE].recovery == 0.0
    assert rows[DPO].recovery == pytest.approx(0.75)
    assert rows[GRPO].recovery == 1.0
    assert rows[KIMI].recovery is None  # no setback ever occurred → not measured, not 0

    # cost/task is real (priced budget); the frontier ceiling costs more than the local stages.
    assert rows[KIMI].cost_per_task is not None and rows[BASE].cost_per_task is not None
    assert rows[KIMI].cost_per_task > rows[BASE].cost_per_task

    # median turns is measured, and the trained stages solve in fewer turns than the base.
    assert rows[BASE].median_turns is not None and rows[GRPO].median_turns is not None
    assert rows[GRPO].median_turns < rows[BASE].median_turns


def test_end_to_end_table_renders_with_every_metric(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    _build_suite(root)
    md = render_markdown(_run_ablation(root))
    for header in (
        "pass@1",
        "tool-syntax validity",
        "median turns",
        "recovery rate",
        "cost / task",
    ):
        assert header in md
    assert "REAL end-to-end run" in md  # the provenance stamp
    assert "What each stage added" in md  # the delta block
    for label in ("base Qwen2.5-Coder-1.5B", "+SFT", "+DPO", "+GRPO", "Kimi (ceiling)"):
        assert label in md
