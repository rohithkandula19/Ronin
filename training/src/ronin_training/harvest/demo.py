"""The harvest pipeline end to end, offline, on trajectories built in this file.

``python -m ronin_training.harvest.demo``

The trajectories here are hand-written *fixtures*, not recorded runs — the demo has to
work with no eval sweep on disk. Every number it prints is therefore a real count over
those fixtures and nothing else; none of it is a claim about the corpus a real sweep
would produce. What it demonstrates is the shape: which runs the gates keep, that a
perturbed copy renames prompt and target together, that all four negative classes are
mined from the failing run, and that every emitted DPO row validates against the schema.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from .augment import augment
from .dpo import build_pairs, validate_row
from .dpo import write_jsonl as write_pairs
from .filters import budget_table
from .negatives import DEFAULT_TOOL_ROLES, class_counts, mine_all
from .prompt import assistant_target_text, parse_target_text
from .sft import SftConfig, extract
from .sft import write_jsonl as write_examples
from .trajectory import RecordedCall, RecordedResult, Step, ToolSchema, Trajectory

_READ = ToolSchema(
    name="read_file",
    description="Read a file from the repository.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"],
    },
)
_WRITE = ToolSchema(
    name="write_file",
    description="Write a file, replacing its contents. Gated by approval.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
)
_TOOLS = (_READ, _WRITE)
_SYSTEM = "You are Ronin. Read before you claim; call tools with valid JSON."


def _good_run(run_id: str, *, extra_turns: int = 0) -> Trajectory:
    steps = [
        Step(
            index=0,
            calls=(
                RecordedCall(
                    id="c0", name="read_file", arguments={"path": "src/pipeline/loader.py"}
                ),
            ),
            results=(
                RecordedResult(
                    call_id="c0", name="read_file", ok=True, content="RETRY_LIMIT = 3\n"
                ),
            ),
        ),
        Step(
            index=1,
            text="Raising the retry limit.",
            calls=(
                RecordedCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "src/pipeline/loader.py", "content": "RETRY_LIMIT = 5\n"},
                ),
            ),
            results=(
                RecordedResult(
                    call_id="c1", name="write_file", ok=True, content="wrote 1 file"
                ),
            ),
        ),
    ]
    for i in range(extra_turns):
        index = len(steps)
        steps.append(
            Step(
                index=index,
                calls=(
                    RecordedCall(
                        id=f"x{i}", name="read_file", arguments={"path": f"src/pipeline/note{i}.py"}
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id=f"x{i}", name="read_file", ok=True, content="# note\n"
                    ),
                ),
            )
        )
    return Trajectory(
        task_id="raise-retry-limit",
        run_id=run_id,
        prompt="Raise RETRY_LIMIT in src/pipeline/loader.py to 5.",
        model="demo-model",
        system=_SYSTEM,
        tools=_TOOLS,
        steps=tuple(steps),
        verified=True,
    )


def _failing_run() -> Trajectory:
    """One run containing all four negative classes, so the demo shows each firing."""
    denial = "the user declined this action: do not touch the vendored tree"
    return Trajectory(
        task_id="raise-retry-limit",
        run_id="run-bad",
        prompt="Raise RETRY_LIMIT in src/pipeline/loader.py to 5.",
        model="demo-model",
        system=_SYSTEM,
        tools=_TOOLS,
        verified=False,
        steps=(
            # edit_without_read: writes a path it never read.
            Step(
                index=0,
                calls=(
                    RecordedCall(
                        id="b0",
                        name="write_file",
                        arguments={"path": "vendor/loader.py", "content": "RETRY_LIMIT = 5\n"},
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="b0", name="write_file", ok=False, error=f"DENIED: {denial}"
                    ),
                ),
            ),
            # ignored_denial: the identical call again.
            Step(
                index=1,
                calls=(
                    RecordedCall(
                        id="b1",
                        name="write_file",
                        arguments={"path": "vendor/loader.py", "content": "RETRY_LIMIT = 5\n"},
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="b1", name="write_file", ok=False, error=f"DENIED: {denial}"
                    ),
                ),
            ),
            # looping: a third identical call inside the stall window.
            Step(
                index=2,
                calls=(
                    RecordedCall(
                        id="b2",
                        name="write_file",
                        arguments={"path": "vendor/loader.py", "content": "RETRY_LIMIT = 5\n"},
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="b2", name="write_file", ok=False, error=f"DENIED: {denial}"
                    ),
                ),
            ),
            # malformed_tool_json, twice: an unparseable block, and a typed-wrong arg.
            Step(
                index=3,
                text=(
                    "Let me look again.\n<tool_call>\n"
                    '{"name": "read_file", "arguments": {path: }}\n'
                    "</tool_call>"
                ),
                calls=(
                    RecordedCall(
                        id="b3",
                        name="read_file",
                        arguments={"path": "src/pipeline/loader.py", "limit": "40"},
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="b3",
                        name="read_file",
                        ok=False,
                        error="limit must be an integer. Pass limit=40, not \"40\".",
                    ),
                ),
            ),
        ),
    )


async def main() -> int:
    good = [_good_run("run-a"), _good_run("run-b"), _good_run("run-flail", extra_turns=6)]
    bad = _failing_run()
    corpus = [*good, bad]

    print("== gates ==")
    examples, counts, budgets = extract(corpus, config=SftConfig(augment_copies=1, seed=7))
    for row in budget_table(budgets):
        print(f"  {row['task_id']}: median {row['median_turns']} turns, "
              f"budget {row['budget']}, from {row['verified_runs']} verified run(s)")
    print(counts.summary())

    print("\n== a rendered example (prompt tail + target) ==")
    first = examples[0]
    print(f"  tools presented: {[t['function']['name'] for t in first.payload.tools]}")
    print(f"  last prompt message: {json.dumps(first.payload.messages[-1], sort_keys=True)}")
    print(f"  target: {json.dumps(first.completion, sort_keys=True)}")
    wire = assistant_target_text(_good_run('run-a').steps[0])
    print(f"  same target as runtime wire text: {wire!r}")
    print(f"  parses back to: {parse_target_text(wire)}")

    print("\n== augmentation is consistent across prompt and target ==")
    perturbed = augment(_good_run("run-a"), seed=7)
    print(f"  prompt: {perturbed.prompt}")
    print(f"  target arguments: {perturbed.steps[1].calls[0].arguments['path']}")
    renamed = perturbed.steps[1].calls[0].arguments["path"]
    print(f"  the renamed path appears in the prompt: {renamed in perturbed.prompt}")

    print("\n== negatives ==")
    negatives = mine_all([bad], roles=DEFAULT_TOOL_ROLES)
    for name, total in class_counts(negatives).items():
        print(f"  {name}: {total}")

    print("\n== preference pairs ==")
    pairs, pair_counts = build_pairs(negatives, donors=good, roles=DEFAULT_TOOL_ROLES)
    print(pair_counts.summary())
    invalid = sum(1 for p in pairs if validate_row(p.as_row()))
    print(f"  rows failing the schema: {invalid}")
    for pair in pairs:
        origin = "synthesized" if pair.chosen.synthesized else f"donor {pair.chosen.donor_run_id}"
        print(f"  {pair.kind}: chosen from {origin}")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        sft_rows = write_examples(out / "sft.jsonl", examples)
        dpo_rows = write_pairs(out / "dpo.jsonl", pairs)
        print(f"\nwrote {sft_rows} SFT row(s) and {dpo_rows} validated DPO row(s) to {out}")

    print("\nEvery number above is a count over the fixtures in this file. It is not a "
          "claim about what a real eval sweep yields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
