"""``ronin duel`` — the same suite task, two models, the same seed, side by side.

A pass rate per model tells you which model scored higher. It does not tell you
*why*, and "why" is the only thing worth knowing before a fine-tune: if A and B both
fail a task for the **same** taxonomy reason, suspect the harness; if they fail it for
**different** reasons, suspect the models. That column —
:attr:`DuelScoreboard.both_failed_differently` — is what this module exists to
produce. Everything else here serves making it trustworthy.

**This is not the v1 duel, and deliberately not built on it.**
``packages/cli/src/ronin_cli/duel.py`` is an *adversarial diff review*: one model is
handed the other's diff and asked to hunt for blockers, and its ``DuelVerdict``
(``duelist``, ``blockers``, ``passed``, ``raw``) is a *model's opinion* parsed out of
prose with a regex. This module is a *paired A/B run over the eval suite*: both
models attempt the same task from the same start, and the judge is the suite's own
gate — ``verify.sh`` and the taxonomy, not an opinion. Nothing in ``DuelVerdict``
survives the translation: there is no diff under review, no reviewer, no blocker list,
and the outcome is per task rather than per change. Reusing it would mean either
pretending a task failure is a "blocker" or importing ``ronin_cli`` — with its
provider and ``ronin_agent_patterns`` dependencies — into a package that has none.
They are two different features that happen to share a name.

**The comparison table is not rewritten here.** :func:`ronin.evals.report.scoreboard`
is already a pure N-run comparison over :class:`~ronin.evals.report.RunReport` values,
and it already names regressions, fixes and the tasks a run is missing. A duel is one
of its callers. This module adds only what a two-model comparison needs and that
function cannot express: the seed, the transcript diff, and the both-failed-differently
split. :func:`render_duel_scoreboard` delegates the shared table to
:func:`~ronin.evals.report.scoreboard_markdown`.

Three design points carry the rest:

**The seed is explicit, and it reaches the model factory.** :data:`DuelistFactory` is
``(duelist, seed) -> AgentFactory``, and the factory is where a model client is built,
which is the only place a seed can actually influence sampling. Both sides get the
same per-task seed from :func:`task_seed` — derived with blake2b rather than
:func:`hash`, because ``hash(str)`` is salted per process and a seed that changes when
you restart Python is not a seed.

**Transcripts are compared through a canonical line form, not raw events.**
:func:`transcript_lines` renumbers tool-call ids to ``call#1``, ``call#2`` … Raw
``tool_use_id`` values are provider-generated and unique per call, so a raw comparison
diverges on the first tool call of every run — a diff that is 100% noise. It also
coalesces ``TextDelta`` runs into one line and honours ``StreamReset`` the way
``ronin.persistence.export`` does, because the unit a human compares is "the model
said X", not "the model emitted the token 'th'".

**The scoreboard excludes wall-clock time.** Turns, tokens and cost are properties of
the run and reproduce with the seed; wall time is a property of the machine and does
not. Putting it in the scoreboard would make "same seed → byte-identical scoreboard"
false on real hardware, quietly turning the determinism check into a flake. Wall time
lives in :func:`render_duel_markdown`, which is for reading rather than comparing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from ..core.types import (
    ApprovalRequest,
    Compaction,
    Event,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolOutput,
    ToolStart,
    TurnEnd,
    TurnStart,
    VerifyResult,
)
from ..verify.runner import CommandRunner, run_command
from .adapters import AgentFactory, OpenedAgent, RunAgent, usage_from_budget
from .adapters import outcome_from_events as _outcome_from_events
from .report import RunReport, Scoreboard, TaskRow, scoreboard, scoreboard_markdown
from .runner import RunnerConfig, run_suite
from .task import EvalTask
from .taxonomy import AgentOutcome, FailureClass, UsageRecord

#: How many aligned diff lines a transcript diff keeps. Beyond this the window is
#: centred on the first divergence and both ends are marked — see
#: :func:`diff_transcripts`.
DEFAULT_MAX_DIFF_LINES: Final[int] = 200

#: Lines of shared context kept before the first divergence when truncating.
DIVERGENCE_CONTEXT: Final[int] = 3

#: Per-line clamp for a canonical transcript line. A tool result can be 30k
#: characters; a diff of two of them is unreadable and says nothing the first 160
#: characters did not.
MAX_LINE_CHARS: Final[int] = 160

#: The one status string that counts as a win. Mirrors ``ronin.evals.report._status``:
#: a pass is a green ``verify.sh`` and nothing else.
PASS_STATUS: Final[str] = "pass"

#: A skipped task is neither side's failure, so it is a tie and never appears in the
#: both-failed columns. Counting a skip as a shared failure would put the harness in
#: the dock for a task that never ran.
SKIP_STATUS: Final[str] = "skip"

_CLIP_MARKER: Final[str] = "…(+{cut} chars)"
_HEAD_MARKER: Final[str] = "... {count} earlier diff line(s) omitted (limit={limit})"
_TAIL_MARKER: Final[str] = "... {count} later diff line(s) omitted (limit={limit})"

#: Marks a canonical line as assistant prose, so a :class:`StreamReset` can retract it
#: while leaving tool lines — which already had their effect — in place.
_TEXT_KIND: Final[str] = "text"
_EVENT_KIND: Final[str] = "event"


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

    Tool-call ids are renumbered ``call#1``, ``call#2`` … in first-seen order: the real
    ids are provider-generated and unique to a run, so comparing them finds a
    difference on the first tool call every time and the diff becomes noise.

    ``TextDelta`` runs are coalesced into a single line, and a :class:`StreamReset`
    retracts the prose of the current scope while leaving the tool lines alone —
    matching ``StreamReset``'s documented rule, and for its stated reason: a tool that
    already ran cannot be un-run by an event.
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
            lines[:] = [
                *lines[:anchor],
                *(pair for pair in lines[anchor:] if pair[0] != _TEXT_KIND),
            ]
            reason = _clip(event.reason or "unstated", max_line_chars)
            lines.append((_EVENT_KIND, f"reset ({reason})"))
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
        if isinstance(event, Compaction):
            # Both token estimates, because the ratio is the only honest measure of
            # whether compaction bought anything — and a duel comparing two models is
            # exactly where "it compacted" without "by how much" misleads.
            failed = ", summarizer failed" if event.summarizer_failed else ""
            emit(
                f"compaction folded {event.folded_messages} msg "
                f"({event.token_estimate_before}→{event.token_estimate_after} tok{failed})"
            )
            continue
        if isinstance(event, VerifyResult):
            if not event.ran:
                emit(f"verify skipped ({event.summary or 'nothing to verify'})")
            else:
                outcome = "passed" if event.passed else "failed"
                repaired = ", repaired" if event.repaired else ""
                emit(
                    f"verify {outcome} ({event.checks_passed} ok, "
                    f"{event.checks_failed} failed{repaired})"
                )
            continue
        if isinstance(event, ToolOutput):
            # A duel transcript compares *decisions*, and a tool's partial output is
            # the same text its ToolEnd already carries. Including it would make two
            # runs differ on chunk boundaries — a pipe detail, not a behaviour.
            continue
        kind = "recoverable" if event.recoverable else "fatal"
        emit(f"error ({event.kind}, {kind}) {_clip(event.message, max_line_chars)}")

    flush()
    return tuple(rendered for _kind, rendered in lines)


@dataclass(frozen=True, slots=True)
class Divergence:
    """The first place two transcripts stop agreeing.

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
        return f"at line {self.position}, A: {self.a_line} — but B: {self.b_line}"


@dataclass(frozen=True, slots=True)
class TranscriptDiff:
    """Two canonical transcripts, aligned, with the first divergence called out.

    ``lines`` are prefixed ``"  "`` (both), ``"A "`` (A only) or ``"B "`` (B only). The
    truncation markers are inside ``lines`` so the block explains itself when pasted
    somewhere without this class; ``omitted_head`` / ``omitted_tail`` are the same
    facts as numbers, for tests.
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
    marked with what they cut, and :attr:`TranscriptDiff.first_divergence` is a field
    rather than a line, so it survives truncation regardless.
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
# Capturing the transcripts the eval runner does not keep
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class TranscriptRecorder:
    """Per-task event streams, collected during a side's run.

    Mutable because accumulating is the point: the adapter fills it in as tasks
    complete and the duel reads it afterwards. It exists because
    ``ronin.evals.adapters.v2_adapter`` deliberately *folds* the event stream into an
    :class:`~ronin.evals.taxonomy.AgentOutcome` and discards it — which is right for
    the taxonomy and wrong for a duel, whose whole added value is showing a human
    where the two runs parted company.

    Scoped to one side of one duel and dropped with it, so this is not a transcript
    store and nothing persists it.
    """

    streams: dict[str, tuple[Event, ...]] = field(default_factory=dict)

    def record(self, task_id: str, events: Sequence[Event]) -> None:
        self.streams[task_id] = tuple(events)

    def stream_for(self, task_id: str) -> tuple[Event, ...]:
        """The stream for ``task_id``, or empty — a task that never ran has no events."""
        return self.streams.get(task_id, ())


def recording_v2_adapter(
    open_agent: AgentFactory,
    recorder: TranscriptRecorder,
    *,
    usage_for: Callable[[EvalTask, OpenedAgent], UsageRecord] | None = None,
) -> RunAgent:
    """``ronin.evals.adapters.v2_adapter``, but the event stream is kept as well as folded.

    Deliberately a near-copy rather than a wrapper: the events exist only inside
    ``v2_adapter``'s body, so there is nothing for a wrapper to intercept. The two
    must not drift, which is why the folding itself is delegated to
    ``adapters.outcome_from_events`` and ``adapters.usage_from_budget`` — the only
    behaviour added here is the :meth:`TranscriptRecorder.record` call.

    A task whose agent raised records an empty stream rather than no entry, so the
    diff says "this side did nothing" instead of silently comparing against absence.
    """

    async def run(task: EvalTask, workspace: Path) -> AgentOutcome:
        opened = await open_agent(task, workspace)
        try:
            result = await opened.agent.run(task.prompt, max_iterations=task.max_turns)
        except Exception as exc:
            # An adapter failure is a result, not an exception: one task that cannot
            # open a model must not end the duel over the others.
            recorder.record(task.id, ())
            return AgentOutcome(error=f"{type(exc).__name__}: {exc}")
        finally:
            await opened.agent.aclose()

        recorder.record(task.id, result.events)
        usage = (
            usage_for(task, opened)
            if usage_for is not None
            else usage_from_budget(
                opened.agent.state.budget,
                requests=sum(1 for event in result.events if isinstance(event, TurnStart)),
            )
        )
        return _outcome_from_events(
            result.events,
            workspace=workspace,
            final_text=result.text,
            registered_tools=opened.registered_tools,
            usage=usage,
        )

    return run


#: ``(duelist, seed) -> AgentFactory``. The factory is where a model client is built,
#: which is the only place a seed can influence sampling — so that is where it goes.
#: A duelist that has no event stream (the v1 shell-out adapter) cannot be duelled:
#: there would be no transcript to diff, and a duel without that is just two runs.
DuelistFactory = Callable[[str, int], AgentFactory]

#: Runs one side: tasks, the agent to drive them with, and the label the report is
#: headed with. Injected so a test drives a whole duel without a runner config.
SuiteRunner = Callable[[Sequence[EvalTask], RunAgent, str], Awaitable[RunReport]]


def suite_runner(
    *,
    config: RunnerConfig | None = None,
    command_runner: CommandRunner = run_command,
    clock: Callable[[], float] = time.monotonic,
) -> SuiteRunner:
    """A :data:`SuiteRunner` over :func:`ronin.evals.runner.run_suite`.

    The duelist's name is written into both ``label`` and ``model`` on the config, so
    the scoreboard column headings and the report's model field agree — a duelist *is*
    a model spec, and two names for it would eventually disagree.
    """
    base = RunnerConfig() if config is None else config

    async def run(tasks: Sequence[EvalTask], agent: RunAgent, label: str) -> RunReport:
        return await run_suite(
            tasks,
            agent,
            config=replace(base, label=label, model=label),
            command_runner=command_runner,
            clock=clock,
        )

    return run


# --------------------------------------------------------------------------- #
# Pairing two reports
# --------------------------------------------------------------------------- #


class Verdict(StrEnum):
    """Who won one task.

    A tie is a tie whether both passed or both failed; the interesting split *within*
    a tie is :attr:`TaskDuel.both_failed_differently`, not the win column.
    """

    A_WINS = "a_wins"
    B_WINS = "b_wins"
    TIE = "tie"


@dataclass(frozen=True, slots=True)
class TaskDuel:
    """One task, both sides' report rows, and the diff between how they got there."""

    task_id: str
    a: TaskRow
    b: TaskRow
    diff: TranscriptDiff = TranscriptDiff()

    @property
    def verdict(self) -> Verdict:
        a_passed = self.a.status == PASS_STATUS
        b_passed = self.b.status == PASS_STATUS
        if a_passed and not b_passed:
            return Verdict.A_WINS
        if b_passed and not a_passed:
            return Verdict.B_WINS
        return Verdict.TIE

    @property
    def both_failed(self) -> bool:
        """Neither side passed, and neither was skipped — a skip is nobody's failure."""
        return all(row.status not in (PASS_STATUS, SKIP_STATUS) for row in (self.a, self.b))

    @property
    def both_failed_differently(self) -> bool:
        """Both sides failed, and the taxonomy disagrees about why.

        The column that answers "harness or model?". Two models failing one task the
        same way is evidence about the *task*; failing it differently is evidence
        about the models.
        """
        return self.both_failed and _classes(self.a) != _classes(self.b)

    @property
    def both_failed_alike(self) -> bool:
        return self.both_failed and _classes(self.a) == _classes(self.b)


def _classes(row: TaskRow) -> frozenset[FailureClass]:
    """A row's taxonomy as a set. Order is the classifier's ranking, not a finding, so
    comparing tuples here would report noise in the one column that must not have any."""
    return frozenset(row.classes)


@dataclass(frozen=True, slots=True)
class Duel:
    """Two finished runs of the same tasks, paired.

    ``tasks`` holds only the tasks present in **both** reports. A task one side never
    attempted is named in ``incomparable`` instead of being scored against nothing —
    the same rule ``ronin.evals.report.scoreboard`` applies, and for the same reason: a
    comparison over a shifting task set is not a comparison.
    """

    seed: int
    duelists: tuple[str, str]
    reports: tuple[RunReport, RunReport]
    tasks: tuple[TaskDuel, ...] = ()
    incomparable: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError(
                "a duel cannot hold two results for one task id — the scoreboard is "
                "keyed by task, and duplicates would double-count"
            )


def pair_reports(
    a: RunReport,
    b: RunReport,
    *,
    seed: int,
    duelists: tuple[str, str] | None = None,
    a_transcripts: Mapping[str, Sequence[Event]] | None = None,
    b_transcripts: Mapping[str, Sequence[Event]] | None = None,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> Duel:
    """Pair two reports into a :class:`Duel`. Pure over the report objects.

    Separated from :func:`run_duel` so a duel can be assembled from two reports that
    were produced hours apart, by a CLI, or by replaying recorded runs — none of which
    should have to go through a runner.
    """
    names = duelists if duelists is not None else (a.name, b.name)
    a_rows = {row.task_id: row for row in a.rows}
    b_rows = {row.task_id: row for row in b.rows}
    shared = sorted(a_rows.keys() & b_rows.keys())
    left = {} if a_transcripts is None else a_transcripts
    right = {} if b_transcripts is None else b_transcripts
    return Duel(
        seed=seed,
        duelists=names,
        reports=(a, b),
        tasks=tuple(
            TaskDuel(
                task_id=task_id,
                a=a_rows[task_id],
                b=b_rows[task_id],
                diff=diff_transcripts(
                    left.get(task_id, ()), right.get(task_id, ()), max_lines=max_diff_lines
                ),
            )
            for task_id in shared
        ),
        incomparable=tuple(sorted((a_rows.keys() | b_rows.keys()) - set(shared))),
    )


async def run_duel(
    tasks: Sequence[EvalTask],
    *,
    factory: DuelistFactory,
    suite: SuiteRunner,
    duelists: tuple[str, str],
    seed: int,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> Duel:
    """Run the suite twice — once per duelist, at the same per-task seeds — and pair it.

    A side is one whole suite run, and the two run one after the other rather than
    concurrently. Interleaving them would make the wall-clock column measure
    contention between the sides rather than the work, and wall time is the column a
    reader instinctively trusts. Sequencing is cheap; an untrustworthy column is not.
    """
    a_name, b_name = duelists
    a_recorder = TranscriptRecorder()
    b_recorder = TranscriptRecorder()
    a_report = await suite(
        tasks, recording_v2_adapter(_seeded(factory, a_name, seed), a_recorder), a_name
    )
    b_report = await suite(
        tasks, recording_v2_adapter(_seeded(factory, b_name, seed), b_recorder), b_name
    )
    return pair_reports(
        a_report,
        b_report,
        seed=seed,
        duelists=duelists,
        a_transcripts=a_recorder.streams,
        b_transcripts=b_recorder.streams,
        max_diff_lines=max_diff_lines,
    )


def _seeded(factory: DuelistFactory, duelist: str, seed: int) -> AgentFactory:
    """An :data:`~ronin.evals.adapters.AgentFactory` seeded per task.

    The factory is consulted once per task rather than once per side, so each task's
    sampling stream is independent while both sides see the identical seed for it.
    """

    async def open_agent(task: EvalTask, workspace: Path) -> OpenedAgent:
        return await factory(duelist, task_seed(seed, task.id))(task, workspace)

    return open_agent


# --------------------------------------------------------------------------- #
# The scoreboard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DuelRow:
    """One scoreboard row. Every field here reproduces from the seed."""

    task_id: str
    category: str
    verdict: Verdict
    a_status: str
    b_status: str
    a_turns: int
    b_turns: int
    a_tokens: str
    b_tokens: str
    a_cost: str
    b_cost: str
    a_classes: tuple[FailureClass, ...] = ()
    b_classes: tuple[FailureClass, ...] = ()
    diverged_at: int = -1
    both_failed_differently: bool = False


@dataclass(frozen=True, slots=True)
class DuelScoreboard:
    """The duel comparison, as a value.

    ``shared`` is whatever :func:`ronin.evals.report.scoreboard` made of the two
    reports — the pass rates, the per-task status table, the regressions and the fixes
    — held rather than re-derived, so there is exactly one implementation of "compare
    two runs" in the package.

    ``wins`` and ``losses`` are stated from **A's** point of view.
    ``wins + losses + ties == tasks`` holds structurally rather than by assertion:
    each column counts rows by a single :class:`Verdict` and a row has exactly one.
    """

    seed: int
    duelists: tuple[str, str]
    shared: Scoreboard
    rows: tuple[DuelRow, ...] = ()
    incomparable: tuple[str, ...] = ()

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
        """Task ids both sides failed, for different taxonomy reasons."""
        return tuple(row.task_id for row in self.rows if row.both_failed_differently)

    @property
    def both_failed_alike(self) -> tuple[str, ...]:
        """Task ids both sides failed the same way — suspect the task or the harness."""
        return tuple(
            row.task_id
            for row in self.rows
            if not row.both_failed_differently
            and row.a_status not in (PASS_STATUS, SKIP_STATUS)
            and row.b_status not in (PASS_STATUS, SKIP_STATUS)
        )


def duel_scoreboard(duel: Duel) -> DuelScoreboard:
    """Fold a duel into its scoreboard. Pure, total, and the only summary function."""
    return DuelScoreboard(
        seed=duel.seed,
        duelists=duel.duelists,
        shared=scoreboard(list(duel.reports)),
        rows=tuple(
            DuelRow(
                task_id=task.task_id,
                category=task.a.category,
                verdict=task.verdict,
                a_status=task.a.status,
                b_status=task.b.status,
                a_turns=task.a.turns,
                b_turns=task.b.turns,
                a_tokens=task.a.tokens,
                b_tokens=task.b.tokens,
                a_cost=task.a.cost,
                b_cost=task.b.cost,
                a_classes=task.a.classes,
                b_classes=task.b.classes,
                diverged_at=(
                    -1
                    if task.diff.first_divergence is None
                    else task.diff.first_divergence.position
                ),
                both_failed_differently=task.both_failed_differently,
            )
            for task in duel.tasks
        ),
        incomparable=duel.incomparable,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _classes_cell(classes: Sequence[FailureClass]) -> str:
    return _cell(", ".join(sorted(str(name) for name in classes)) or "—")


def render_duel_scoreboard(board: DuelScoreboard) -> str:
    """The duel scoreboard as Markdown. Pure, and byte-identical for identical inputs.

    The shared comparison table comes from
    :func:`ronin.evals.report.scoreboard_markdown`; everything below it is what a
    two-model duel needs and that renderer cannot express. No wall-clock time and no
    timestamp appear anywhere here — that omission is what makes "two duels at the
    same seed produce the same scoreboard" a checkable claim rather than a hope.
    """
    a_name, b_name = board.duelists
    lines = [
        f"# duel — {a_name} vs {b_name} (seed {board.seed})",
        "",
        f"tasks {board.tasks} · {a_name} wins {board.wins} · {b_name} wins "
        f"{board.losses} · ties {board.ties}",
        "",
        scoreboard_markdown(board.shared).rstrip("\n"),
        "",
        "## per task",
        "",
        f"| id | {a_name} | {b_name} | verdict | turns (a/b) | tokens (a/b) "
        "| cost (a/b) | diverged | taxonomy a | taxonomy b |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in board.rows:
        diverged = "same" if row.diverged_at < 0 else f"line {row.diverged_at}"
        lines.append(
            f"| `{_cell(row.task_id)}` | {row.a_status} | {row.b_status} "
            f"| {row.verdict.value} | {row.a_turns}/{row.b_turns} "
            f"| {_cell(row.a_tokens)}/{_cell(row.b_tokens)} "
            f"| {_cell(row.a_cost)}/{_cell(row.b_cost)} | {diverged} "
            f"| {_classes_cell(row.a_classes)} | {_classes_cell(row.b_classes)} |"
        )

    lines += ["", "## both failed, for different reasons", ""]
    if board.both_failed_differently:
        lines += [
            "The tasks that tell you about the *models*: both sides failed, and the "
            "taxonomy disagrees about why.",
            "",
        ]
        rows = {row.task_id: row for row in board.rows}
        for task_id in board.both_failed_differently:
            row = rows[task_id]
            lines.append(
                f"- `{task_id}`: {a_name} {_classes_cell(row.a_classes)} · "
                f"{b_name} {_classes_cell(row.b_classes)}"
            )
    else:
        lines.append("None — no task was failed by both sides for different reasons.")

    if board.both_failed_alike:
        lines += [
            "",
            "## both failed, the same way",
            "",
            "Both sides hit the same taxonomy class here, which points at the task or "
            "the harness rather than at either model:",
            "",
        ]
        lines.extend(f"- `{task_id}`" for task_id in board.both_failed_alike)

    if board.incomparable:
        lines += [
            "",
            "## not run by both sides",
            "",
            "Excluded from every count above, because a comparison over a shifting "
            "task set is not a comparison:",
            "",
        ]
        lines.extend(f"- `{task_id}`" for task_id in board.incomparable)

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

    Wall-clock time lives here rather than in :func:`render_duel_scoreboard` because
    this document is for reading and that one is for comparing.
    """
    parts = [render_duel_scoreboard(duel_scoreboard(duel)), ""]
    a_name, b_name = duel.duelists
    for task in duel.tasks:
        parts += [
            f"## `{task.task_id}` — {task.verdict.value}",
            "",
            f"{a_name}: {task.a.status}, {task.a.wall_seconds:.2f}s · "
            f"{b_name}: {task.b.status}, {task.b.wall_seconds:.2f}s",
            "",
            render_transcript_diff(task.diff, duelists=duel.duelists),
        ]
    return "\n".join(parts).rstrip("\n") + "\n"


__all__ = [
    "DEFAULT_MAX_DIFF_LINES",
    "DIVERGENCE_CONTEXT",
    "MAX_LINE_CHARS",
    "PASS_STATUS",
    "SKIP_STATUS",
    "Divergence",
    "Duel",
    "DuelRow",
    "DuelScoreboard",
    "DuelistFactory",
    "SuiteRunner",
    "TaskDuel",
    "TranscriptDiff",
    "TranscriptRecorder",
    "Verdict",
    "diff_transcripts",
    "duel_scoreboard",
    "pair_reports",
    "recording_v2_adapter",
    "render_duel_markdown",
    "render_duel_scoreboard",
    "render_transcript_diff",
    "run_duel",
    "suite_runner",
    "task_seed",
    "transcript_lines",
]
