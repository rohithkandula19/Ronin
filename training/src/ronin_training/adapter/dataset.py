"""The holdout split, by task and never by example — plus the input contract.

The one rule this module exists to enforce: **no task appears on both sides of the
split.** Two examples harvested from the same task, one in train and one in the
holdout, make the reported score partly a memorisation score, and the inflation is
invisible — the numbers look better, the curves look normal, and the adapter is
worse than it reads. Splitting by example is the default behaviour of almost every
dataset utility, which is exactly why this is written out longhand and tested as a
property rather than as a single example.

Input contract (what the harvest pipeline hands over). Each line of a ``.jsonl``
file is one object:

* SFT rows carry ``messages`` (the mlx-lm / chat shape) and optionally ``tools``.
* DPO rows carry ``prompt``, ``chosen`` and ``rejected``.
* **Every** row must carry a task identity under one of :data:`TASK_ID_KEYS`. A row
  with none is rejected by index, naming the keys that were looked for, because a
  row with no task identity cannot be split safely and silently treating it as its
  own task would defeat the whole module.

Selection is deterministic without being random: task ids are ordered by
``sha256(seed:task_id)``, which shuffles them stably across processes and Python
versions (``hash()`` does not) and makes the same seed reproduce the same split
years later.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Keys a harvested row may use to name its task, in priority order. The harvest
#: pipeline picks one; accepting several means the two halves of this phase can be
#: written in parallel and reconciled without a rewrite.
TASK_ID_KEYS: tuple[str, ...] = ("task_id", "task", "family", "scenario")

#: Fields an SFT row must have to be trainable at all.
SFT_REQUIRED: tuple[str, ...] = ("messages",)
#: Fields a preference row must have.
DPO_REQUIRED: tuple[str, ...] = ("prompt", "chosen", "rejected")


class DatasetError(ValueError):
    """A dataset could not be read, or cannot be split without leaking."""


@dataclass(frozen=True, slots=True)
class Example:
    """One harvested row, with its task identity lifted out.

    ``row`` is carried through untouched: this module decides which side of the
    split a row lands on and nothing else, so a change to the row format upstream
    does not need a change here.
    """

    task_id: str
    example_id: str
    row: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.task_id:
            raise DatasetError("Example.task_id must be non-empty")


def task_id_of(row: Mapping[str, Any], *, keys: Sequence[str] = TASK_ID_KEYS) -> str:
    """The task identity of ``row``, or a :class:`DatasetError` naming what was missing."""
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise DatasetError(
        f"row has no task identity: looked for {list(keys)} and found "
        f"{sorted(row)} — a row with no task cannot be held out by task, and "
        "treating it as its own task would defeat the split"
    )


def examples_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: str = "sft",
    keys: Sequence[str] = TASK_ID_KEYS,
) -> tuple[Example, ...]:
    """Lift task ids off ``rows`` and check the per-kind required fields.

    ``kind`` is ``"sft"`` or ``"dpo"``; anything else is a programming error and
    raises rather than defaulting to the laxer check.
    """
    if kind == "sft":
        required = SFT_REQUIRED
    elif kind == "dpo":
        required = DPO_REQUIRED
    else:
        raise DatasetError(f"kind must be 'sft' or 'dpo', got {kind!r}")

    out: list[Example] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise DatasetError(f"row {index} is a {type(row).__name__}, not an object")
        missing = [name for name in required if name not in row]
        if missing:
            raise DatasetError(
                f"row {index} is not a usable {kind} row: missing {missing}"
            )
        try:
            task = task_id_of(row, keys=keys)
        except DatasetError as exc:
            raise DatasetError(f"row {index}: {exc}") from exc
        example_id = str(row.get("id") or row.get("example_id") or f"{task}#{index}")
        out.append(Example(task_id=task, example_id=example_id, row=row))
    return tuple(out)


def read_jsonl(path: str | Path) -> tuple[Mapping[str, Any], ...]:
    """Read a ``.jsonl`` file, naming the line number of a bad line.

    A malformed line is fatal rather than skipped: a silently dropped row is a
    row that is in neither split and in no count, and the totals then disagree
    with the file for reasons nobody can find later.
    """
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"cannot read {file}: {exc}") from exc
    rows: list[Mapping[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise DatasetError(f"{file}:{number} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, Mapping):
            raise DatasetError(f"{file}:{number} is not a JSON object")
        rows.append(parsed)
    return tuple(rows)


def read_examples(
    path: str | Path, *, kind: str = "sft", keys: Sequence[str] = TASK_ID_KEYS
) -> tuple[Example, ...]:
    """:func:`read_jsonl` then :func:`examples_from_rows`."""
    return examples_from_rows(read_jsonl(path), kind=kind, keys=keys)


# --------------------------------------------------------------------------- #
# The split
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TaskSplit:
    """A train/holdout split, carrying the task sets it was built from.

    The task sets are stored rather than recomputed so :func:`verify_no_task_leak`
    checks what the split *claims* against what it *contains* — a split whose
    bookkeeping disagrees with its rows is exactly the bug this guards against.
    """

    train: tuple[Example, ...]
    holdout: tuple[Example, ...]
    train_tasks: tuple[str, ...]
    holdout_tasks: tuple[str, ...]

    @property
    def leaked_tasks(self) -> tuple[str, ...]:
        """Task ids present on both sides. Empty is the only acceptable value."""
        left = {example.task_id for example in self.train}
        right = {example.task_id for example in self.holdout}
        return tuple(sorted(left & right))

    @property
    def example_fraction_held_out(self) -> float:
        """The fraction of *examples* in the holdout.

        Reported because holding out 10% of tasks does not hold out 10% of
        examples when tasks differ in size, and someone will otherwise read the
        configured 0.10 as a statement about rows.
        """
        total = len(self.train) + len(self.holdout)
        return len(self.holdout) / total if total else 0.0

    def manifest(self) -> dict[str, Any]:
        """What to write next to the split so a later reader can audit it."""
        return {
            "train_examples": len(self.train),
            "holdout_examples": len(self.holdout),
            "train_tasks": list(self.train_tasks),
            "holdout_tasks": list(self.holdout_tasks),
            "task_fraction_held_out": (
                len(self.holdout_tasks)
                / (len(self.train_tasks) + len(self.holdout_tasks))
                if self.train_tasks or self.holdout_tasks
                else 0.0
            ),
            "example_fraction_held_out": self.example_fraction_held_out,
            "leaked_tasks": list(self.leaked_tasks),
        }


def _order_key(task_id: str, seed: int) -> str:
    """A stable pseudo-random ordering key.

    sha256 rather than ``hash()``: ``hash()`` of a str is salted per process, so a
    split built with it is not reproducible even on the same machine.
    """
    return hashlib.sha256(f"{seed}:{task_id}".encode()).hexdigest()


def split_by_task(
    examples: Sequence[Example], *, fraction: float = 0.10, seed: int = 0
) -> TaskSplit:
    """Hold out ``fraction`` of the *tasks* (never of the examples).

    ``fraction`` is read as a fraction of the task count, which is what "hold out
    10% by task" means. At least one task is always held out, and at least one is
    always kept, so a split is never degenerate; a corpus with a single task
    raises, because there is no honest way to divide it.
    """
    if not 0.0 < fraction < 1.0:
        raise DatasetError(f"fraction must be in (0, 1), got {fraction}")
    if not examples:
        raise DatasetError("cannot split an empty dataset")

    by_task: dict[str, list[Example]] = {}
    for example in examples:
        by_task.setdefault(example.task_id, []).append(example)
    tasks = sorted(by_task)
    if len(tasks) < 2:
        raise DatasetError(
            f"cannot hold out by task with {len(tasks)} task(s) — every example "
            "would land on one side. Harvest more task variety before splitting"
        )

    shuffled = sorted(tasks, key=lambda task: (_order_key(task, seed), task))
    wanted = round(fraction * len(tasks))
    n_holdout = min(max(1, wanted), len(tasks) - 1)
    holdout_tasks = tuple(sorted(shuffled[:n_holdout]))
    train_tasks = tuple(sorted(shuffled[n_holdout:]))

    def rows_for(names: Iterable[str]) -> tuple[Example, ...]:
        out: list[Example] = []
        for name in sorted(names):
            out.extend(sorted(by_task[name], key=lambda example: example.example_id))
        return tuple(out)

    split = TaskSplit(
        train=rows_for(train_tasks),
        holdout=rows_for(holdout_tasks),
        train_tasks=train_tasks,
        holdout_tasks=holdout_tasks,
    )
    verify_no_task_leak(split)
    return split


def verify_no_task_leak(split: TaskSplit) -> None:
    """Raise unless the two sides are task-disjoint and account for every task.

    Called by :func:`split_by_task` on its own output — an assertion about the
    thing that was just built, because the failure it catches is silent, and a
    caller who forgets to check gets the check anyway.
    """
    leaked = split.leaked_tasks
    if leaked:
        raise DatasetError(
            f"task leakage: {list(leaked)} appear in both train and holdout. "
            "The holdout score would be partly a memorisation score"
        )
    declared_train = set(split.train_tasks)
    declared_holdout = set(split.holdout_tasks)
    overlap = sorted(declared_train & declared_holdout)
    if overlap:
        raise DatasetError(f"the split's own task lists overlap: {overlap}")
    actual_train = {example.task_id for example in split.train}
    actual_holdout = {example.task_id for example in split.holdout}
    if not actual_train <= declared_train:
        raise DatasetError(
            "train rows carry task ids the split does not declare: "
            f"{sorted(actual_train - declared_train)}"
        )
    if not actual_holdout <= declared_holdout:
        raise DatasetError(
            "holdout rows carry task ids the split does not declare: "
            f"{sorted(actual_holdout - declared_holdout)}"
        )


def write_split(split: TaskSplit, out_dir: str | Path) -> dict[str, Path]:
    """Write ``train.jsonl`` / ``holdout.jsonl`` / ``split_manifest.json``.

    ``\\n`` line endings are written explicitly (``newline=""``) so the files are
    byte-identical on every platform — the manifest is hashed downstream, and a
    CRLF translation would look like tampering.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    verify_no_task_leak(split)
    written: dict[str, Path] = {}
    for name, rows in (("train", split.train), ("holdout", split.holdout)):
        path = directory / f"{name}.jsonl"
        body = "".join(
            json.dumps(dict(example.row), sort_keys=True) + "\n" for example in rows
        )
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        written[name] = path
    manifest = directory / "split_manifest.json"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(split.manifest(), indent=2, sort_keys=True) + "\n")
    written["manifest"] = manifest
    return written
