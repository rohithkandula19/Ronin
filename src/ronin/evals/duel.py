"""``ronin duel`` — the same suite task, two models, the same seed, side by side.

A single number per model tells you which model scored higher. It does not tell you
*why*, and "why" is the only thing worth knowing before a fine-tune: if model A and
model B both fail a task for the **same** taxonomy reason, the harness is wrong; if
they fail it for **different** reasons, the models are. That column —
:attr:`Scoreboard.both_failed_differently` — is what this module exists to produce.
Everything else here is in service of making it trustworthy.

**This is not the v1 duel, and deliberately not built on it.**
``packages/cli/src/ronin_cli/duel.py`` is an *adversarial diff review*: one model is
handed the other's diff and asked to hunt for blockers, and its ``DuelVerdict``
(``duelist``, ``blockers``, ``passed``, ``raw``) is a *model's opinion* parsed out of
prose. This module is a *paired A/B run over the eval suite*: both models attempt the
same task from the same start, and the judge is the suite's own gate — tests and
files, not an opinion. Nothing in ``DuelVerdict`` survives the translation: there is
no diff, no reviewer, no blocker list, and the outcome is per task rather than per
change. Reusing it would mean either pretending a task failure is a "blocker" or
importing ``ronin_cli`` (with its provider and ``ronin_agent_patterns`` dependencies)
into a package that has none. They are two different features that share a name; see
the report for the CLI naming this implies.

Three design points that carry the module:

**The seed is explicit, and it is threaded into the model factory, not the runner.**
:data:`DuelistFactory` takes ``(duelist, seed)`` and returns a
:data:`RunAgent` with that seed baked in, which is the only place a seed can
actually reach sampling. Per task, both sides get the *same* derived seed from
:func:`task_seed` — derived with blake2b rather than :func:`hash`, because
``hash(str)`` is salted per process and a seed that changes when you restart Python
is not a seed.

**Transcripts are compared through a canonical line form, not raw events.**
:func:`transcript_lines` renumbers tool-call ids to ``call#1``, ``call#2`` … Raw
``tool_use_id`` values are provider-generated and unique per call, so a raw
comparison finds a divergence on the first tool call of every run — a diff that is
100% noise. It also coalesces ``TextDelta`` runs into one line and honours
``StreamReset`` the way ``ronin.persistence.export`` does, because the unit a human
compares is "the model said X", not "the model emitted the token 'th'".

**The scoreboard excludes wall-clock time.** Turns, tokens and cost are properties of
the run and reproduce with the seed; wall time is a property of the machine and does
not. Putting it in the scoreboard would make "same seed → byte-identical scoreboard"
false on real hardware, which would quietly turn the determinism check into a flake.
Wall time is in :func:`render_duel_markdown` instead, where it belongs.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

from ..core.types import (
    ApprovalRequest,
    Event,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)

#: How many aligned diff lines a transcript diff keeps. Beyond this the window is
#: centred on the first divergence and both ends are marked — see
#: :func:`diff_transcripts`.
DEFAULT_MAX_DIFF_LINES: Final[int] = 200

#: Lines of shared context kept before the first divergence when truncating.
DIVERGENCE_CONTEXT: Final[int] = 3

#: Per-line clamp for a canonical transcript line. A tool result can be 30k
#: characters; a diff of two of them is unreadable and tells you nothing the first
#: 160 characters did not.
MAX_LINE_CHARS: Final[int] = 160

_CLIP_MARKER: Final[str] = "…(+{cut} chars)"
_HEAD_MARKER: Final[str] = "... {count} earlier diff line(s) omitted (limit={limit})"
_TAIL_MARKER: Final[str] = "... {count} later diff line(s) omitted (limit={limit})"

#: Marks a canonical line as assistant prose, so :class:`StreamReset` can retract it
#: while leaving tool calls — which already had their effect — in place.
_TEXT_KIND: Final[str] = "text"
_EVENT_KIND: Final[str] = "event"


class Side(StrEnum):
    """Which corner of the duel. Used to key working directories apart.

    A duel with the same model on both sides is legal and useful — it is how you
    check that "same seed" means anything at all — so the two sides cannot be
    distinguished by their model name and need an identity of their own.
    """

    A = "a"
    B = "b"


# --------------------------------------------------------------------------- #
# The seams onto the eval suite
# --------------------------------------------------------------------------- #


class TaskLike(Protocol):
    """The part of ``ronin.evals.task.EvalTask`` a duel needs.

    Read-only properties rather than attributes, so a frozen dataclass satisfies it
    (mypy treats a frozen field as read-only and rejects it against a settable
    protocol member). Deliberately two members and not eight: every name guessed
    here is a name that has to match agent B's ``EvalTask`` exactly, and the duel
    genuinely only needs an identity and something to run.
    """

    @property
    def id(self) -> str: ...

    @property
    def prompt(self) -> str: ...


class RecordLike(Protocol):
    """The part of ``ronin.evals.runner.RunRecord`` a duel reads.

    These six names are the whole contract between this module and the runner. If the
    runner spells one of them differently, rename it *here* — this protocol is the
    single place the two meet, and nothing downstream of :class:`SideOutcome` knows
    the runner exists.

    ``taxonomy`` is typed ``Sequence[str]`` so a ``StrEnum`` classification tuple
    satisfies it without this module importing the taxonomy.
    """

    @property
    def passed(self) -> bool: ...

    @property
    def turns(self) -> int: ...

    @property
    def tokens(self) -> int: ...

    @property
    def cost_usd(self) -> float: ...

    @property
    def wall_seconds(self) -> float: ...

    @property
    def taxonomy(self) -> Sequence[str]: ...

    @property
    def events(self) -> Sequence[Event]: ...


#: One side's runner: attempt a task in a directory and report what happened. Shaped
#: to match the eval runner's own ``RunAgent`` — two positional arguments, no seed —
#: because the seed belongs to the factory that built it, not to each call.
RunAgent = Callable[[TaskLike, Path], Awaitable[RecordLike]]

#: ``(duelist, seed) -> RunAgent``. The only place a seed can reach sampling is the
#: thing that constructs the model client, so that is where it is threaded.
DuelistFactory = Callable[[str, int], RunAgent]

#: Where one side runs one task. Two sides must never share a directory: A's edits
#: would be B's starting state, and the duel would measure order rather than models.
WorkdirFactory = Callable[[TaskLike, Side], Path]


def task_seed(seed: int, task_id: str) -> int:
    """A per-task seed derived from the duel's seed. Deterministic across processes.

    Both sides get the same value for the same task, so the comparison stays fair,
    while different tasks do not share a sampling stream. ``blake2b`` rather than
    :func:`hash`: ``hash(str)`` is salted from ``PYTHONHASHSEED``, so a duel seeded
    with it would not reproduce after a restart — which is the one property a seed
    exists to provide.
    """
    digest = hashlib.blake2b(task_id.encode("utf-8"), digest_size=8).digest()
    return (seed ^ int.from_bytes(digest, "big")) & 0xFFFF_FFFF_FFFF_FFFF


# --------------------------------------------------------------------------- #
# Canonical transcripts
# --------------------------------------------------------------------------- #


def _clip(text: str, limit: int) -> str:
    """One line, clamped, with the cut named. Deterministic."""
    flat = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + _CLIP_MARKER.format(cut=len(flat) - limit)


def _arguments(arguments: object) -> str:
    """Tool arguments as stable JSON — sorted, so key order cannot fake a divergence."""
    return json.dumps(arguments, sort_keys=True, default=repr)


def transcript_lines(
    events: Sequence[Event], *, max_line_chars: int = MAX_LINE_CHARS
) -> tuple[str, ...]:
    """One canonical line per meaningful thing that happened. Pure.

    Tool-call ids are renumbered ``call#1``, ``call#2`` … in first-seen order: the
    real ids are provider-generated and unique to a run, so comparing them finds a
    difference on the first tool call every time and the diff becomes noise.

    ``TextDelta`` runs are coalesced into a single line, and a :class:`StreamReset`
    retracts the prose of the current scope while leaving the tool lines alone —
    matching ``StreamReset``'s documented rule, and for its stated reason: a tool
    that already ran cannot be un-run by an event.
    """
    lines: list[tuple[str, str]] = []
    buffer: list[str] = []
    thinking = False
    anchor = 0
    ids: dict[str, str] = {}

    def label(tool_use_id: str) -> str:
        if tool_use_id not in ids:
            ids[tool_use_id] = f"call#{len(ids) + 1}"
        return ids[tool_use_id]

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        prefix = "think" if thinking else "text"
        lines.append((_TEXT_KIND, f"{prefix}: {_clip(''.join(buffer), max_line_chars)}"))
        buffer = []

    def emit(rendered: str) -> None:
        flush()
        lines.append((_EVENT_KIND, rendered))

    for event in events:
        if isinstance(event, TextDelta):
            if buffer and event.thinking != thinking:
                flush()
            thinking = event.thinking
            buffer.append(event.text)
            continue
        if isinstance(event, StreamReset):
            buffer = []
            kept = [*lines[:anchor], *(pair for pair in lines[anchor:] if pair[0] != _TEXT_KIND)]
            lines[:] = kept
            reason = event.reason or "unstated"
            lines.append((_EVENT_KIND, f"reset ({_clip(reason, max_line_chars)})"))
            anchor = len(lines)
            continue
        if isinstance(event, TurnStart):
            emit(f"turn {event.turn_index} start")
            anchor = len(lines)
            continue
        if isinstance(event, ToolStart):
            emit(
                f"call {label(event.tool_use_id)} {event.name} "
                f"{_clip(_arguments(dict(event.arguments)), max_line_chars)}"
            )
            continue
        if isinstance(event, ToolEnd):
            verdict = "ok" if event.result.ok else "error"
            body = event.result.content if event.result.ok else event.result.error
            emit(
                f"result {label(event.tool_use_id)} {event.name} -> {verdict} "
                f"{_clip(body, max_line_chars)}"
            )
            continue
        if isinstance(event, ApprovalRequest):
            emit(
                f"approval {label(event.tool_use_id)} {event.name} "
                f"{event.danger_level.name.lower()}"
            )
            continue
        if isinstance(event, TurnEnd):
            emit(
                f"turn {event.turn_index} end {event.state.value} "
                f"({event.stop_reason or 'unspecified'})"
            )
            anchor = len(lines)
            continue
        kind = "recoverable" if event.recoverable else "fatal"
        emit(f"error ({event.kind}, {kind}) {_clip(event.message, max_line_chars)}")

    flush()
    return tuple(rendered for _kind, rendered in lines)


@dataclass(frozen=True, slots=True)
class Divergence:
    """The first place the two transcripts stop agreeing.

    ``a_line`` / ``b_line`` are empty when that side had nothing at this point — an
    insertion or a deletion rather than a substitution.
    """

    position: int
    a_index: int
    b_index: int
    a_line: str = ""
    b_line: str = ""
    kind: str = "replace"

    @property
    def summary(self) -> str:
        """One sentence naming the divergence, for a report header."""
        if self.kind == "insert":
            return f"at line {self.position}, B did something A never did: {self.b_line}"
        if self.kind == "delete":
            return f"at line {self.position}, A did something B never did: {self.a_line}"
        return (
            f"at line {self.position}, A: {self.a_line} — but B: {self.b_line}"
        )


@dataclass(frozen=True, slots=True)
class TranscriptDiff:
    """Two canonical transcripts, aligned, with the first divergence called out.

    ``lines`` are prefixed ``"  "`` (both), ``"A "`` (A only) or ``"B "`` (B only).
    The truncation markers are inside ``lines`` so the block explains itself when
    pasted somewhere without this class; ``omitted_head`` / ``omitted_tail`` are the
    same facts as numbers, for tests.
    """

    lines: tuple[str, ...] = ()
    first_divergence: Divergence | None = None
    a_lines: int = 0
    b_lines: int = 0
    omitted_head: int = 0
    omitted_tail: int = 0

    @property
    def identical(self) -> bool:
        return self.first_divergence is None


def diff_transcripts(
    a_events: Sequence[Event],
    b_events: Sequence[Event],
    *,
    max_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> TranscriptDiff:
    """Align two event streams and name where they first part company. Pure.

    Truncation keeps a window *centred on the first divergence* rather than the head
    of the diff: the head of two runs of the same task is almost always identical, so
    head-truncation reliably throws away the only interesting part. Both ends are
    marked with what they cut. :attr:`TranscriptDiff.first_divergence` is a field, not
    a line, so it survives truncation regardless.
    """
    left = transcript_lines(a_events)
    right = transcript_lines(b_events)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)

    rendered: list[str] = []
    divergence: Divergence | None = None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rendered.extend(f"  {line}" for line in left[i1:i2])
            continue
        if divergence is None:
            divergence = Divergence(
                position=len(rendered),
                a_index=i1 if i1 < i2 else -1,
                b_index=j1 if j1 < j2 else -1,
                a_line=left[i1] if i1 < i2 else "",
                b_line=right[j1] if j1 < j2 else "",
                kind=tag,
            )
        rendered.extend(f"A {line}" for line in left[i1:i2])
        rendered.extend(f"B {line}" for line in right[j1:j2])

    focus = divergence.position if divergence is not None else 0
    window, head, tail = _window(rendered, focus, max_lines)
    if head:
        window = [_HEAD_MARKER.format(count=head, limit=max_lines), *window]
    if tail:
        window = [*window, _TAIL_MARKER.format(count=tail, limit=max_lines)]

    return TranscriptDiff(
        lines=tuple(window),
        first_divergence=divergence,
        a_lines=len(left),
        b_lines=len(right),
        omitted_head=head,
        omitted_tail=tail,
    )


def _window(rendered: list[str], focus: int, limit: int) -> tuple[list[str], int, int]:
    """``limit`` lines around ``focus``, plus how many were dropped either side."""
    if limit <= 0 or len(rendered) <= limit:
        return list(rendered), 0, 0
    start = min(max(0, focus - DIVERGENCE_CONTEXT), len(rendered) - limit)
    end = start + limit
    return rendered[start:end], start, len(rendered) - end


# --------------------------------------------------------------------------- #
# Outcomes, verdicts, scoreboard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SideOutcome:
    """One side's result for one task, normalized off the runner's record.

    The event stream is *not* here. It is consumed by :func:`diff_transcripts` and
    dropped, so a duel result is a comparison rather than two transcripts — which is
    what makes a scoreboard byte-comparable at all.
    """

    duelist: str
    passed: bool = False
    turns: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    taxonomy: tuple[str, ...] = ()

    @classmethod
    def from_record(cls, record: RecordLike, *, duelist: str) -> SideOutcome:
        """Normalize a runner record. Taxonomy classes are sorted and de-duplicated
        so two runs that classified the same failure in a different order compare
        equal — order there is an artefact of the classifier, not a finding."""
        return cls(
            duelist=duelist,
            passed=record.passed,
            turns=record.turns,
            tokens=record.tokens,
            cost_usd=record.cost_usd,
            wall_seconds=record.wall_seconds,
            taxonomy=tuple(sorted({str(name) for name in record.taxonomy})),
        )


class Verdict(StrEnum):
    """Who won one task. A tie is a tie whether both passed or both failed —
    the *interesting* split within a tie is :attr:`TaskDuel.both_failed_differently`,
    not the win column."""

    A_WINS = "a_wins"
    B_WINS = "b_wins"
    TIE = "tie"


@dataclass(frozen=True, slots=True)
class TaskDuel:
    """One task, both sides, and the diff between how they got there."""

    task_id: str
    a: SideOutcome
    b: SideOutcome
    diff: TranscriptDiff = TranscriptDiff()

    @property
    def verdict(self) -> Verdict:
        if self.a.passed and not self.b.passed:
            return Verdict.A_WINS
        if self.b.passed and not self.a.passed:
            return Verdict.B_WINS
        return Verdict.TIE

    @property
    def both_failed(self) -> bool:
        return not self.a.passed and not self.b.passed

    @property
    def both_failed_differently(self) -> bool:
        """Both sides failed, and the taxonomy disagrees about why.

        This is the column that answers "harness or model?". Two models failing the
        same task for the same reason is evidence about the *task*; two models failing
        it for different reasons is evidence about the models.
        """
        return self.both_failed and set(self.a.taxonomy) != set(self.b.taxonomy)

    @property
    def both_failed_alike(self) -> bool:
        return self.both_failed and set(self.a.taxonomy) == set(self.b.taxonomy)


@dataclass(frozen=True, slots=True)
class Duel:
    """A finished duel: the seed it ran under, the two duelists, and every task."""

    seed: int
    duelists: tuple[str, str]
    tasks: tuple[TaskDuel, ...] = ()

    def __post_init__(self) -> None:
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError(
                "a duel cannot contain two results for the same task id — the "
                "scoreboard is keyed by task, and duplicates would double-count"
            )


@dataclass(frozen=True, slots=True)
class ScoreRow:
    """One scoreboard row. Everything here reproduces from the seed."""

    task_id: str
    verdict: Verdict
    a: SideOutcome
    b: SideOutcome
    diverged_at: int = -1
    both_failed_differently: bool = False


@dataclass(frozen=True, slots=True)
class Scoreboard:
    """The comparison, as a value. Pure function of a :class:`Duel`.

    ``wins`` and ``losses`` are stated from **A's** point of view, and the invariant
    checked in :meth:`__post_init__` — ``wins + losses + ties == len(rows)`` — is why
    they are derived rather than passed in: a scoreboard whose columns do not add up
    to the task count has silently dropped a task.
    """

    seed: int
    duelists: tuple[str, str]
    rows: tuple[ScoreRow, ...] = ()

    def __post_init__(self) -> None:
        if self.wins + self.losses + self.ties != len(self.rows):
            raise ValueError(
                "scoreboard columns do not add up to the task count — every task is "
                "exactly one of a win, a loss or a tie"
            )

    @property
    def tasks(self) -> int:
        return len(self.rows)

    @property
    def wins(self) -> int:
        return sum(1 for row in self.rows if row.verdict is Verdict.A_WINS)

    @property
    def losses(self) -> int:
        return sum(1 for row in self.rows if row.verdict is Verdict.B_WINS)

    @property
    def ties(self) -> int:
        return sum(1 for row in self.rows if row.verdict is Verdict.TIE)

    @property
    def both_failed_differently(self) -> tuple[str, ...]:
        """Task ids where both sides failed for different taxonomy reasons."""
        return tuple(row.task_id for row in self.rows if row.both_failed_differently)

    @property
    def both_failed_alike(self) -> tuple[str, ...]:
        """Task ids where both sides failed the same way — suspect the harness."""
        return tuple(
            row.task_id
            for row in self.rows
            if row.verdict is Verdict.TIE
            and not row.a.passed
            and not row.b.passed
            and not row.both_failed_differently
        )


def scoreboard(duel: Duel) -> Scoreboard:
    """Fold a duel into its scoreboard. Pure, total, and the only summary function."""
    rows = tuple(
        ScoreRow(
            task_id=task.task_id,
            verdict=task.verdict,
            a=task.a,
            b=task.b,
            diverged_at=(
                -1 if task.diff.first_divergence is None else task.diff.first_divergence.position
            ),
            both_failed_differently=task.both_failed_differently,
        )
        for task in duel.tasks
    )
    return Scoreboard(seed=duel.seed, duelists=duel.duelists, rows=rows)


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


async def run_duel(
    tasks: Sequence[TaskLike],
    *,
    factory: DuelistFactory,
    duelists: tuple[str, str],
    seed: int,
    workdir: WorkdirFactory,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> Duel:
    """Run every task on both sides at the same per-task seed, and pair the results.

    Sequential, and A before B for every task. Running the two sides concurrently
    would be faster and would make the wall-clock column measure contention between
    the sides rather than the work — and wall time is the one column a reader
    instinctively trusts. Sequencing is cheap; an untrustworthy column is not.

    A fresh :data:`RunAgent` is built per (side, task) so the per-task seed from
    :func:`task_seed` actually reaches the model client, rather than one long-lived
    client drifting through the task list.
    """
    a_name, b_name = duelists
    results: list[TaskDuel] = []
    for task in tasks:
        derived = task_seed(seed, task.id)
        a_record = await factory(a_name, derived)(task, workdir(task, Side.A))
        b_record = await factory(b_name, derived)(task, workdir(task, Side.B))
        results.append(
            TaskDuel(
                task_id=task.id,
                a=SideOutcome.from_record(a_record, duelist=a_name),
                b=SideOutcome.from_record(b_record, duelist=b_name),
                diff=diff_transcripts(
                    a_record.events, b_record.events, max_lines=max_diff_lines
                ),
            )
        )
    return Duel(seed=seed, duelists=duelists, tasks=tuple(results))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _pass(passed: bool) -> str:
    return "pass" if passed else "fail"


def render_scoreboard(board: Scoreboard) -> str:
    """The scoreboard as Markdown. Pure, and byte-identical for identical inputs.

    Carries no wall-clock time and no timestamp — see the module docstring. That
    omission is what makes "two duels at the same seed produce the same scoreboard" a
    checkable claim rather than a hope.
    """
    a_name, b_name = board.duelists
    lines = [
        f"## duel — {a_name} vs {b_name} (seed {board.seed})",
        "",
        f"tasks {board.tasks} · {a_name} wins {board.wins} · {b_name} wins "
        f"{board.losses} · ties {board.ties}",
        "",
        f"| Task | {a_name} | {b_name} | verdict | turns (a/b) | tokens (a/b) "
        "| cost (a/b) | diverged | taxonomy a | taxonomy b |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in board.rows:
        diverged = "same" if row.diverged_at < 0 else f"line {row.diverged_at}"
        lines.append(
            f"| `{_cell(row.task_id)}` | {_pass(row.a.passed)} | {_pass(row.b.passed)} "
            f"| {row.verdict.value} "
            f"| {row.a.turns}/{row.b.turns} | {row.a.tokens}/{row.b.tokens} "
            f"| ${row.a.cost_usd:.4f}/${row.b.cost_usd:.4f} | {diverged} "
            f"| {_cell(', '.join(row.a.taxonomy) or '—')} "
            f"| {_cell(', '.join(row.b.taxonomy) or '—')} |"
        )

    lines += ["", "### both failed, for different reasons", ""]
    if board.both_failed_differently:
        lines.append(
            "These are the tasks that tell you about the *models*: both sides failed, "
            "and the taxonomy disagrees about why."
        )
        lines.append("")
        for task_id in board.both_failed_differently:
            row = next(r for r in board.rows if r.task_id == task_id)
            lines.append(
                f"- `{task_id}`: {a_name} {', '.join(row.a.taxonomy) or '(unclassified)'} "
                f"· {b_name} {', '.join(row.b.taxonomy) or '(unclassified)'}"
            )
    else:
        lines.append("None — no task was failed by both sides for different reasons.")

    if board.both_failed_alike:
        lines += [
            "",
            "### both failed, the same way",
            "",
            "Both sides hit the same taxonomy class here, which points at the task or "
            "the harness rather than at either model:",
            "",
        ]
        lines.extend(f"- `{task_id}`" for task_id in board.both_failed_alike)

    return "\n".join(lines) + "\n"


def render_transcript_diff(diff: TranscriptDiff, *, duelists: tuple[str, str]) -> str:
    """The aligned transcripts as a fenced block, first divergence named above it."""
    a_name, b_name = duelists
    lines = [f"`A` = {a_name} · `B` = {b_name} · {diff.a_lines} vs {diff.b_lines} lines", ""]
    if diff.first_divergence is None:
        lines.append("The two transcripts are identical under canonical comparison.")
        return "\n".join(lines) + "\n"
    lines += [f"**First divergence** {diff.first_divergence.summary}", "", "```text"]
    lines.extend(diff.lines)
    lines += ["```"]
    return "\n".join(lines) + "\n"


def render_duel_markdown(duel: Duel) -> str:
    """The full report: the scoreboard, then per task the wall clock and the diff.

    Wall-clock time lives here rather than in :func:`render_scoreboard` because this
    document is for reading and that one is for comparing.
    """
    board = scoreboard(duel)
    parts = [render_scoreboard(board), ""]
    a_name, b_name = duel.duelists
    for task in duel.tasks:
        parts += [
            f"### `{task.task_id}` — {task.verdict.value}",
            "",
            f"{a_name}: {_pass(task.a.passed)}, {task.a.wall_seconds:.2f}s · "
            f"{b_name}: {_pass(task.b.passed)}, {task.b.wall_seconds:.2f}s",
            "",
            render_transcript_diff(task.diff, duelists=duel.duelists),
        ]
    return "\n".join(parts).rstrip("\n") + "\n"


__all__ = [
    "DEFAULT_MAX_DIFF_LINES",
    "DIVERGENCE_CONTEXT",
    "MAX_LINE_CHARS",
    "Divergence",
    "Duel",
    "DuelistFactory",
    "RecordLike",
    "RunAgent",
    "ScoreRow",
    "Scoreboard",
    "Side",
    "SideOutcome",
    "TaskDuel",
    "TaskLike",
    "TranscriptDiff",
    "Verdict",
    "WorkdirFactory",
    "diff_transcripts",
    "render_duel_markdown",
    "render_scoreboard",
    "render_transcript_diff",
    "run_duel",
    "scoreboard",
    "task_seed",
    "transcript_lines",
]
