"""``python -m ronin_training.adapter.demo`` — the whole pipeline, offline, in one run.

Proves four things without a GPU, a model, or a network:

1. both shipped configs load and validate, and carry the specified hyperparameters;
2. the holdout split puts no task on both sides, and says what fraction of *examples*
   that actually held out (which is not the configured task fraction);
3. the two first-class metrics compute exactly the arithmetic they claim, against
   scripted turns with a known error rate;
4. the three-way report renders an **adapter-loses** outcome legibly — the outcome
   that has to look good on the page or nobody will publish one.

Every number printed comes from the scripted fakes in this file. No number here is a
measurement of any trained model, because no model was trained.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .config import DPO_CONFIG_PATH, SFT_CONFIG_PATH, load_config, validate
from .dataset import Example, split_by_task, write_split
from .metrics import (
    CallCheck,
    Setback,
    TurnRecord,
    recovery_rate,
    tool_syntax_validity,
)
from .threeway import ADAPTER, BASE, KIMI, TargetRun, assemble_report, render_markdown

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _rule(title: str) -> None:
    print()
    print(f"== {title} " + "=" * max(0, 70 - len(title)))


def _configs() -> int:
    _rule("1. the shipped configs")
    problems = 0
    for relative in (SFT_CONFIG_PATH, DPO_CONFIG_PATH):
        path = _REPO_ROOT / relative
        cfg = load_config(path)
        result = validate(cfg, train_rows=400)
        print(f"{relative}: {'ok' if result.ok else 'INVALID'}")
        print(
            f"  pass={cfg.pass_.value} base={cfg.base_model} ({cfg.base_model_licence})"
        )
        print(
            f"  lora r={cfg.lora.rank} alpha={cfg.lora.alpha} "
            f"(mlx scale={cfg.lora.mlx_scale}) targets={list(cfg.lora.target_modules)}"
        )
        print(
            f"  lr={cfg.train.learning_rate} schedule={cfg.train.schedule} "
            f"epochs={cfg.train.epochs} seq={cfg.train.max_seq_length} "
            f"grad_checkpoint={cfg.train.grad_checkpoint}"
        )
        if cfg.dpo is not None:
            print(f"  dpo beta={cfg.dpo.beta} resume={cfg.resume_adapter}")
        print(f"  holdout by={cfg.holdout.by} fraction={cfg.holdout.fraction}")
        print(f"  mlx iters for 400 rows = {cfg.iters_for(400)}")
        for line in result.render().splitlines():
            print(f"  {line}")
        problems += len(result.errors)
    return problems


def _split() -> int:
    _rule("2. the holdout split — by task, never by example")
    # Deliberately uneven: task sizes differ, so the example fraction cannot equal
    # the task fraction, and pretending otherwise is how a split gets misread.
    examples = [
        Example(
            task_id=f"task-{index % 20:02d}",
            example_id=f"ex-{index:03d}",
            row={"task_id": f"task-{index % 20:02d}", "messages": []},
        )
        for index in range(93)
    ]
    split = split_by_task(examples, fraction=0.10, seed=0)
    print(f"tasks: {len(split.train_tasks)} train / {len(split.holdout_tasks)} holdout")
    print(f"examples: {len(split.train)} train / {len(split.holdout)} holdout")
    print(f"holdout tasks: {list(split.holdout_tasks)}")
    print(f"leaked tasks: {list(split.leaked_tasks)} (must be empty)")
    print(f"example fraction held out: {split.example_fraction_held_out:.3f}")
    with tempfile.TemporaryDirectory() as raw:
        written = write_split(split, Path(raw) / "split")
        manifest = written["manifest"].read_bytes()
        print(f"wrote {sorted(written)}; manifest is {len(manifest)} bytes")
        crlf = b"\r\n" in manifest
        print(f"manifest has CRLF: {crlf}")
    return 1 if split.leaked_tasks else 0


def _metrics() -> int:
    _rule("3. the two first-class metrics, over scripted turns")
    bad = CallCheck(ok=False, reason="call to 'read_file' violates its schema: demo")
    good = CallCheck(ok=True)
    turns = [
        # 7 turns attempt a call and 5 of them are valid  ->  validity 5/7. The two
        # recovery turns below count here too: attempting a call after a setback is
        # still an attempt, and excluding them would make the denominators disagree.
        TurnRecord(task_id="t1", call=good, fingerprint="read_file:{}"),
        TurnRecord(task_id="t1", call=good, fingerprint="read_file:{\"a\":1}"),
        TurnRecord(task_id="t1", call=good, fingerprint="glob:{}"),
        TurnRecord(task_id="t2", call=bad, fingerprint="read_file:{\"b\":2}"),
        TurnRecord(task_id="t2", call=bad, fingerprint="read_file:{\"c\":3}"),
        # a turn with no call at all: excluded from the denominator, counted
        TurnRecord(task_id="t2"),
        # 3 setbacks, 2 adapted  ->  recovery rate 2/3
        TurnRecord(
            task_id="t3",
            call=good,
            fingerprint="glob:{\"pattern\":\"*.py\"}",
            setback=Setback.FAILED_RESULT,
            setback_fingerprint="read_file:{\"path\":\"missing\"}",
        ),
        TurnRecord(
            task_id="t3",
            call=good,
            fingerprint="read_file:{\"path\":\"missing\"}",
            setback=Setback.FAILED_RESULT,
            setback_fingerprint="read_file:{\"path\":\"missing\"}",
        ),
        TurnRecord(
            task_id="t4",
            setback=Setback.DENIAL,
            setback_fingerprint="run_command:{\"command\":\"rm -rf /\"}",
        ),
    ]
    syntax = tool_syntax_validity(turns)
    recovery = recovery_rate(turns)
    print(f"tool-syntax validity: {syntax.render()}  (expected 5/7 = 0.714)")
    print(f"  turns={syntax.turns} attempts={syntax.attempts} no_attempt={syntax.no_attempt}")
    print(f"recovery rate: {recovery.render()}  (expected 2/3 = 0.667)")
    for kind, recovered, total in recovery.breakdown:
        print(f"  after {kind}: {recovered}/{total}")
    empty_syntax = tool_syntax_validity([TurnRecord(task_id="t9")])
    empty_recovery = recovery_rate([TurnRecord(task_id="t9")])
    print(f"nothing measured renders as: {empty_syntax.render()!r}")
    print(f"nothing measured renders as: {empty_recovery.render()!r}")
    ok = syntax.rate == 5 / 7 and recovery.rate == 2 / 3
    print(f"arithmetic matches: {ok}")
    return 0 if ok else 1


def _bad(reason: str) -> CallCheck:
    return CallCheck(ok=False, reason=reason)


def _scripted(task: str, valid: int, invalid: int, adapted: int, repeated: int) -> list[
    TurnRecord
]:
    turns: list[TurnRecord] = []
    for index in range(valid):
        turns.append(
            TurnRecord(task_id=task, call=CallCheck(ok=True), fingerprint=f"ok:{index}")
        )
    for index in range(invalid):
        turns.append(
            TurnRecord(
                task_id=task,
                call=_bad("call payload is not valid JSON: demo"),
                fingerprint=f"bad:{index}",
            )
        )
    for index in range(adapted):
        turns.append(
            TurnRecord(
                task_id=task,
                call=CallCheck(ok=True),
                fingerprint=f"new:{index}",
                setback=Setback.FAILED_RESULT,
                setback_fingerprint=f"old:{index}",
            )
        )
    for index in range(repeated):
        turns.append(
            TurnRecord(
                task_id=task,
                call=CallCheck(ok=True),
                fingerprint=f"old:{index}",
                setback=Setback.DENIAL,
                setback_fingerprint=f"old:{index}",
            )
        )
    return turns


def _three_way() -> int:
    _rule("4. the three-way report, rendering an ADAPTER-LOSES outcome")
    report = assemble_report(
        suite_id="phase11-demo",
        suite_cases=10,
        runs={
            # Scripted so the adapter is WORSE than base on both metrics. This is the
            # outcome that must render well; a pipeline that only looks good when it
            # wins is a pipeline that never publishes a negative result.
            ADAPTER: TargetRun(
                model="ronin-adapter-qwen1.5b (SCRIPTED FAKE, not a trained model)",
                turns=tuple(_scripted("t", valid=6, invalid=4, adapted=1, repeated=3)),
            ),
            BASE: TargetRun(
                model="Qwen2.5-Coder-1.5B (SCRIPTED FAKE)",
                turns=tuple(_scripted("t", valid=8, invalid=2, adapted=3, repeated=1)),
            ),
            KIMI: TargetRun(
                model="kimi (SCRIPTED FAKE)",
                turns=tuple(_scripted("t", valid=10, invalid=0, adapted=4, repeated=0)),
            ),
        },
        provenance=(
            "SCRIPTED FAKE DATA — this demo trains nothing and measures nothing",
        ),
    )
    verdict = report.verdict()
    print(f"outcome: {verdict.outcome.value}")
    print(f"headline: {verdict.headline}")
    print()
    print(render_markdown(report))
    return 0 if verdict.outcome.value == "adapter_loses" else 1


def main() -> int:
    print("ronin-adapter-qwen1.5b — offline pipeline demo")
    print("NOTE: no training happens here and no number below measures a real model.")
    failures = _configs() + _split() + _metrics() + _three_way()
    _rule("result")
    print("all four checks proved what they claim" if not failures else f"{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
