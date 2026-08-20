"""Trajectory harvest: recorded task runs turned into training-ready datasets.

RE.4. The eval suite already answers "is the agent good enough" (:mod:`ronin.evals`)
and "why did it fail" (:mod:`ronin.evals.taxonomy`). This module answers a third
question — "which of these runs is worth *learning from*" — and emits the three record
shapes a trainer consumes: supervised (SFT), preference (DPO), and reward-labeled
(RLVR).

**Harvest consumes recorded runs; it never runs a task.** Running a task needs a model,
a workspace and a container — none of which belong in an offline, deterministic module,
and all of which are the runner's job (:mod:`ronin.evals.runner`). The only input here
is what a finished run left behind: a recorded transcript
(``.ronin/sessions/<id>.jsonl``, read by :func:`ronin.persistence.transcript.read_events`)
and its verify signal (did ``verify.sh`` pass). That separation is deliberate — it is
what lets this whole file be tested from literals with no network and no fake model, and
it is why :func:`ronin.evals.adapters.sdk_agent_factory` grew a ``record=True`` flag: a
harvest run asks the runner to write transcripts, then points this module at them.

**Only assistant turns are training targets.** A supervised record contains the whole
conversation — system prompt, user task, assistant replies, tool results — because the
model needs the context to predict the next assistant turn. But the *loss* is computed
on the assistant tokens alone. Training on tool results teaches the model to hallucinate
tool output (it learns to *predict* what a tool would say instead of *calling* it), and
training on the user turn teaches it to write the user's half of the conversation. The
mask that enforces this is applied downstream by the trainer; this module's contract is
to make it *derivable* — every :func:`to_sharegpt` record carries ``assistant_spans``,
the indices of the turns that are targets, so the trainer never has to re-derive the
mask from role strings and get it subtly wrong.

**Errors are values.** A transcript that will not parse, or an outcome with no task id,
is a *skipped record with a reason* (:class:`Skip`) — never an exception. A harvest over
a thousand runs must not die on the one that crashed mid-write, and
:class:`HarvestResult` carries the skip and rejection tallies so nothing is ever
silently dropped: ``accepted + rejected + skipped`` accounts for every input.

**Everything is deterministic.** Records are sorted by task id, JSON keys are sorted,
category medians come from :func:`statistics.median`, and there is no clock and no
randomness anywhere. Two harvests of the same inputs produce byte-identical files, which
is the property that makes a dataset diffable and a regression visible.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ..core.types import (
    Event,
    Message,
    Role,
    Text,
    TextDelta,
    Thinking,
    ToolEnd,
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
)
from ..persistence.transcript import ReadResult
from .adapters import outcome_from_events
from .taxonomy import (
    AgentOutcome,
    FailureClass,
    RunRecord,
    ToolCallRecord,
    VerifyProbe,
    classify,
)

# --------------------------------------------------------------------------- #
# Role mapping — the one place a Ronin Role becomes a sharegpt tag
# --------------------------------------------------------------------------- #

#: ``core.types.Role`` -> the sharegpt ``from`` tag. sharegpt calls the model "gpt" and
#: the user "human"; the mapping is data rather than a chain of ``if`` so the masking
#: test can assert every role lands on a distinct tag and none is forgotten.
FROM_TAG: Final[dict[Role, str]] = {
    Role.SYSTEM: "system",
    Role.USER: "human",
    Role.ASSISTANT: "gpt",
    Role.TOOL: "tool",
}

#: The sharegpt tag whose turns are training targets. Everything else is masked.
ASSISTANT_TAG: Final = FROM_TAG[Role.ASSISTANT]


# --------------------------------------------------------------------------- #
# The input: one recorded run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """One recorded run, as much of it as harvest needs and no more.

    Deliberately not :class:`ronin.evals.taxonomy.RunRecord`: that type is the taxonomy's
    input and carries baselines, wall clock and usage the taxonomy needs, but it does not
    carry the *conversation* — and SFT is nothing but the conversation. So this is a
    smaller, harvest-shaped value that a test can build from three literals, with
    :func:`outcome_from_transcript` as the bridge from a real recorded run.

    ``messages`` is the full role-tagged conversation (the SFT source). ``tool_calls``
    and ``turns`` are the behavioural signals the quality bar reads. ``reward`` is an
    optional explicit override; left ``None``, :func:`verifiable_reward` derives it from
    ``verify_passed`` — the RLVR trainer's actual label.
    """

    task_id: str
    category: str = ""
    prompt: str = ""
    verify_passed: bool = False
    turns: int = 0
    messages: tuple[Message, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    target_paths: tuple[str, ...] = ()
    edited_paths: tuple[str, ...] = ()
    registered_tools: tuple[str, ...] = ()
    reward: float | None = None

    @property
    def final_assistant_text(self) -> str:
        """The last assistant turn's prose — the contrastive unit a DPO pair differs on."""
        for message in reversed(self.messages):
            if message.role is Role.ASSISTANT:
                return render_message(message)
        return ""


# --------------------------------------------------------------------------- #
# Signals derived from an outcome
# --------------------------------------------------------------------------- #


def repeated_tool_errors(outcome: TaskOutcome) -> int:
    """How many times the *same* failing call was made — the loop's own idea of "same".

    Grouped by ``fingerprint`` (name + normalized args, per
    :func:`ronin.core.loop.fingerprint`) so "wrote the same broken edit twice" counts and
    "two different edits that both failed" does not. The return is the largest such group;
    ``0`` means no tool errored at all.
    """
    counts: Counter[str] = Counter()
    for call in outcome.tool_calls:
        if not call.ok:
            counts[call.fingerprint or call.name] += 1
    return max(counts.values(), default=0)


def tool_validity(outcome: TaskOutcome) -> float:
    """Fraction of tool calls that succeeded, in ``[0, 1]``; ``1.0`` when none were made.

    A behavioural quality signal the RL trainer can shape a reward with: a run that
    reached a green ``verify.sh`` by way of ten failed tool calls is a worse trajectory
    than one that reached it cleanly, and pass/fail alone cannot see the difference.
    """
    if not outcome.tool_calls:
        return 1.0
    ok = sum(1 for call in outcome.tool_calls if call.ok)
    return round(ok / len(outcome.tool_calls), 6)


def verifiable_reward(outcome: TaskOutcome) -> float:
    """The RLVR label: the explicit ``reward`` if set, else ``1.0`` iff verify passed.

    "Verifiable" is the whole point — the reward is a green check, not a judge's opinion,
    so it is binary by default and cannot drift. An explicit ``reward`` is honoured for
    callers that shape it (partial credit, tool-validity blends), but the default keeps
    the signal honest.
    """
    if outcome.reward is not None:
        return outcome.reward
    return 1.0 if outcome.verify_passed else 0.0


# --------------------------------------------------------------------------- #
# The quality bar — the three-way filter on what becomes SFT
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TrajectoryQualityBar:
    """What a trajectory must clear to be supervised-training data.

    Three gates, each defending against a different way a run can be technically-correct
    and still poison a dataset:

    * ``require_verify_pass`` — a run that did not pass ``verify.sh`` did not solve the
      task, and imitating it teaches the wrong answer. The default is ``True``; DPO is
      where failed runs get used, as the *rejected* half.
    * ``max_turns_ratio`` — a run that took far longer than its peers reached the answer
      by a bad path, and imitating it teaches the bad path. Judged against the category
      median rather than an absolute, because "long" for a multi-file refactor is short
      for a one-line fix.
    * ``max_repeated_tool_errors`` — a run that made the same failing call more than once
      floundered; ``1`` means "one mistake is human, twice is a pattern", so a call that
      errored *twice* is rejected.
    """

    require_verify_pass: bool = True
    max_turns_ratio: float = 1.5
    max_repeated_tool_errors: int = 1

    def reject_reason(self, outcome: TaskOutcome, *, category_median_turns: float) -> str:
        """Why this trajectory is not training data, or ``""`` if it is.

        A reason string rather than a bool so :class:`HarvestResult` can say *which* gate
        dropped each run — a filter that cannot explain itself is one nobody trusts.
        """
        if self.require_verify_pass and not outcome.verify_passed:
            return "verify did not pass"
        if category_median_turns > 0:
            ceiling = self.max_turns_ratio * category_median_turns
            if outcome.turns > ceiling:
                return (
                    f"turns {outcome.turns} exceed {self.max_turns_ratio}x the category "
                    f"median ({category_median_turns:g} -> ceiling {ceiling:g})"
                )
        repeats = repeated_tool_errors(outcome)
        if repeats > self.max_repeated_tool_errors:
            return f"a tool errored {repeats} times (> {self.max_repeated_tool_errors} allowed)"
        return ""

    def accepts(self, outcome: TaskOutcome, *, category_median_turns: float) -> bool:
        """Whether ``outcome`` clears every gate. ``not reject_reason``, named for callers."""
        return not self.reject_reason(outcome, category_median_turns=category_median_turns)


# --------------------------------------------------------------------------- #
# SFT — sharegpt, with the assistant mask made derivable
# --------------------------------------------------------------------------- #


def render_tool_call(block: ToolUse) -> str:
    """One tool call as deterministic text the model can be trained to emit.

    Arguments are key-sorted so the same call renders the same bytes every time;
    unserializable values fall back to ``repr`` rather than crashing the harvest.
    """
    payload = {"name": block.name, "arguments": dict(block.arguments)}
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    return f"<tool_call>{body}</tool_call>"


def render_message(message: Message) -> str:
    """A message's content as one training string.

    Thinking blocks are dropped on purpose: a thought signature is provider-opaque bytes
    that must be replayed verbatim or the provider rejects the turn (see
    :class:`ronin.core.types.Thinking`), so it can neither be a training target nor be
    reconstructed from a dataset — and reasoning is not the answer regardless. Tool
    results render with an ``[error]`` marker preserved, because "the tool failed" is
    part of the context the next assistant turn is predicted from.
    """
    parts: list[str] = []
    for block in message.content_blocks:
        if isinstance(block, Text):
            if block.text:
                parts.append(block.text)
        elif isinstance(block, ToolUse):
            parts.append(render_tool_call(block))
        elif isinstance(block, ToolResultBlock):
            parts.append(f"[error] {block.content}" if block.is_error else block.content)
        elif isinstance(block, Thinking):  # pragma: no branch - explicit exclusion
            continue
    return "\n".join(parts)


def to_sharegpt(outcome: TaskOutcome) -> dict[str, Any]:
    """One SFT record in sharegpt shape, with the assistant turns marked.

    ``conversations`` is the standard sharegpt list of ``{"from", "value"}`` turns.
    ``assistant_spans`` is the addition that makes this file's contract real: the indices
    into ``conversations`` that are assistant turns, i.e. exactly the turns a trainer
    computes loss on. Everything else — system, human, tool — is context to be masked.
    """
    conversations: list[dict[str, str]] = []
    assistant_spans: list[int] = []
    for message in outcome.messages:
        tag = FROM_TAG[message.role]
        conversations.append({"from": tag, "value": render_message(message)})
        if message.role is Role.ASSISTANT:
            assistant_spans.append(len(conversations) - 1)
    return {
        "task_id": outcome.task_id,
        "conversations": conversations,
        "assistant_spans": assistant_spans,
    }


# --------------------------------------------------------------------------- #
# DPO — preference pairs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DPOPair:
    """A preference pair: one prompt, a preferred continuation, a rejected one.

    The invariant the format rests on is that ``chosen`` and ``rejected`` are two answers
    to the *same* ``prompt`` — the trainer's loss pushes probability toward the first and
    away from the second, and that only means something when the prompt is held fixed.
    """

    prompt: str
    chosen: str
    rejected: str
    task_id: str = ""


def make_pair(prompt: str, chosen: str, rejected: str, *, task_id: str = "") -> DPOPair:
    """Build a :class:`DPOPair`. The explicit builder, because hand-authored and
    failed-run rejects — not the happy path of a harvest — are the real source of the
    ``rejected`` half, and they arrive as three strings, not as a pair of runs."""
    return DPOPair(prompt=prompt, chosen=chosen, rejected=rejected, task_id=task_id)


def to_dpo(pair: DPOPair) -> dict[str, Any]:
    """A :class:`DPOPair` as the ``{prompt, chosen, rejected}`` record the trainer reads."""
    return {
        "task_id": pair.task_id,
        "prompt": pair.prompt,
        "chosen": pair.chosen,
        "rejected": pair.rejected,
    }


# --------------------------------------------------------------------------- #
# RLVR — reward-labeled records
# --------------------------------------------------------------------------- #


def to_rlvr(outcome: TaskOutcome) -> dict[str, Any]:
    """One reward-labeled record for the RL trainer.

    Unlike SFT and DPO, RLVR wants the *whole* labeled set, negatives included — the
    reward is the signal, and a run that failed carries as much information as one that
    passed. So :func:`harvest` emits an RLVR record for every non-skipped outcome, not
    only the accepted ones.
    """
    return {
        "task_id": outcome.task_id,
        "prompt": outcome.prompt,
        "reward": verifiable_reward(outcome),
        "verify_passed": outcome.verify_passed,
        "turns": outcome.turns,
        "tool_validity": tool_validity(outcome),
    }


# --------------------------------------------------------------------------- #
# The bridge from a recorded transcript
# --------------------------------------------------------------------------- #


def messages_from_transcript(read_result: ReadResult) -> tuple[Message, ...]:
    """The conversation a transcript recorded, from the richest source it offers.

    The reliable source is the ``AgentState`` a :class:`~ronin.core.types.TurnEnd` carries
    — it holds the whole message list including the system and user turns, which the event
    stream alone does not. The last such state is the complete conversation, so it wins.
    Only when no state was recorded does this fall back to folding the events, which can
    reconstruct the assistant and tool turns but not the prompt (see
    :func:`outcome_from_transcript`, which supplies it).
    """
    latest: tuple[Message, ...] = ()
    for event in read_result.events:
        if (
            isinstance(event, TurnEnd)
            and event.agent_state is not None
            and event.agent_state.messages
        ):
            latest = event.agent_state.messages
    if latest:
        return latest
    return _fold_events_to_messages(read_result.events)


def _fold_events_to_messages(events: Sequence[Event]) -> tuple[Message, ...]:
    """Reconstruct assistant and tool turns from a bare event stream.

    A fallback for transcripts recorded without agent state. It cannot invent the user
    prompt — that is not in the stream — so it emits only what the events prove happened:
    each turn's assistant prose and tool calls, followed by the tool results as a tool
    message. Deliberately tolerant: an unmatched tool call still becomes a ``ToolUse``,
    because a dropped result is exactly the kind of torn tail this must not crash on.
    """
    messages: list[Message] = []
    text_parts: list[str] = []
    tool_uses: list[ToolUse] = []
    results: list[ToolResultBlock] = []

    def flush() -> None:
        blocks: list[Text | ToolUse] = []
        joined = "".join(text_parts)
        if joined:
            blocks.append(Text(joined))
        blocks.extend(tool_uses)
        if blocks:
            messages.append(Message(role=Role.ASSISTANT, content_blocks=tuple(blocks)))
        if results:
            messages.append(Message(role=Role.TOOL, content_blocks=tuple(results)))
        text_parts.clear()
        tool_uses.clear()
        results.clear()

    for event in events:
        if isinstance(event, TurnStart):
            flush()
        elif isinstance(event, TextDelta):
            if not event.thinking:
                text_parts.append(event.text)
        elif isinstance(event, ToolStart):
            tool_uses.append(
                ToolUse(id=event.tool_use_id, name=event.name, arguments=event.arguments)
            )
        elif isinstance(event, ToolEnd):
            results.append(event.result.as_block(event.tool_use_id))
    flush()
    return tuple(messages)


def outcome_from_transcript(
    read_result: ReadResult,
    *,
    task_id: str,
    verify_passed: bool,
    category: str = "",
    prompt: str = "",
    target_paths: Sequence[str] = (),
    registered_tools: Sequence[str] = (),
    reward: float | None = None,
) -> TaskOutcome:
    """Fold one recorded run into a :class:`TaskOutcome`.

    The behavioural signals (tool calls, turn count, edited paths) come from
    :func:`ronin.evals.adapters.outcome_from_events` — the same fold the runner uses, so
    "the same call" means the same thing here and there rather than being reinvented. The
    conversation comes from :func:`messages_from_transcript`; if the transcript had no
    recorded state and ``prompt`` was supplied, a leading human turn is prepended so the
    SFT record is not missing the task it was solving.

    Never raises — a caller reads the transcript with
    :func:`ronin.persistence.transcript.read_events` (which is itself tolerant of a torn
    tail) and hands the result here; a version-mismatch or mid-file corruption is caught
    at the read and becomes a :class:`Skip`, not an exception in the harvest.
    """
    folded: AgentOutcome = outcome_from_events(read_result.events, workspace=Path("."))
    messages = messages_from_transcript(read_result)
    if prompt and not any(message.role is Role.USER for message in messages):
        messages = (Message(role=Role.USER, content_blocks=(Text(prompt),)), *messages)
    return TaskOutcome(
        task_id=task_id,
        category=category,
        prompt=prompt,
        verify_passed=verify_passed,
        turns=folded.turns_used,
        messages=messages,
        tool_calls=folded.tool_calls,
        target_paths=tuple(target_paths),
        edited_paths=folded.edited_paths,
        registered_tools=tuple(registered_tools),
        reward=reward,
    )


# --------------------------------------------------------------------------- #
# Taxonomy integration — why a rejected run was not worth learning from
# --------------------------------------------------------------------------- #


def _run_record(outcome: TaskOutcome) -> RunRecord:
    """Adapt a :class:`TaskOutcome` to the taxonomy's :class:`RunRecord`, best-effort.

    Harvest and the taxonomy share the same evidence — tool calls, edits, the final
    message — so a rejected trajectory can be *labelled* with why it failed rather than
    left as a bare "dropped". The adaptation is lossy in one honest direction: harvest has
    no verify *baseline* and no fixture symbol table, so the taxonomy classes that need
    them (``BROKE_TESTS`` via a regressed check, ``HALLUCINATED_API`` via an unknown
    symbol) can only fire through their other signal. That is a smaller claim than the
    full runner makes, and it is the right one to make from what a harvest has.
    """
    return RunRecord(
        task_id=outcome.task_id,
        category=outcome.category,
        files_expected=outcome.target_paths,
        outcome=AgentOutcome(
            turns_used=outcome.turns,
            tool_calls=outcome.tool_calls,
            edited_paths=outcome.edited_paths,
            final_text=outcome.final_assistant_text,
            registered_tools=outcome.registered_tools,
        ),
        verify_after=VerifyProbe(ran=True, exit_code=0 if outcome.verify_passed else 1),
    )


def failure_class_of(outcome: TaskOutcome) -> FailureClass | None:
    """The taxonomy's primary class for a run, or ``None`` when it passed and has none."""
    findings = classify(_run_record(outcome))
    return findings[0].failure_class if findings else None


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Rejection:
    """One trajectory the quality bar dropped, and why — plus its failure class if any."""

    task_id: str
    reason: str
    failure_class: str = ""


@dataclass(frozen=True, slots=True)
class Skip:
    """One input that could not be harvested at all — malformed, or missing its id."""

    task_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """The three datasets, plus a full account of what happened to every input.

    ``accepted + len(rejections) + len(skips)`` equals the number of inputs: every run is
    either training data, dropped for a stated reason, or unreadable for a stated reason.
    Nothing is silently lost — that accounting is the difference between a dataset and a
    pile of whatever survived.
    """

    sft: tuple[dict[str, Any], ...] = ()
    dpo: tuple[dict[str, Any], ...] = ()
    rlvr: tuple[dict[str, Any], ...] = ()
    accepted: tuple[str, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    skips: tuple[Skip, ...] = ()
    category_medians: Mapping[str, float] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejections)

    @property
    def skipped_count(self) -> int:
        return len(self.skips)

    def rejection_reasons(self) -> dict[str, int]:
        """How many trajectories each reason dropped — the shape a summary line wants."""
        counts: Counter[str] = Counter(rejection.reason for rejection in self.rejections)
        return dict(counts)


# --------------------------------------------------------------------------- #
# The harvest
# --------------------------------------------------------------------------- #


def category_medians(outcomes: Sequence[TaskOutcome]) -> dict[str, float]:
    """Median turn count per category. The denominator the turns gate divides against."""
    by_category: dict[str, list[int]] = {}
    for outcome in outcomes:
        by_category.setdefault(outcome.category, []).append(outcome.turns)
    return {
        category: float(statistics.median(turns)) for category, turns in sorted(by_category.items())
    }


def harvest(
    outcomes: Sequence[TaskOutcome],
    *,
    bar: TrajectoryQualityBar | None = None,
) -> HarvestResult:
    """Turn recorded runs into SFT, DPO and RLVR datasets.

    The pipeline, in order: drop the unusable (a :class:`Skip` for each), compute the
    category medians the turns gate needs, then walk every usable run once — accepting it
    into SFT if it clears the bar, recording a :class:`Rejection` with its taxonomy label
    if it does not, and emitting an RLVR record either way. DPO pairs are formed last,
    across the whole set, because a pair needs two runs of one task and no single run can
    produce it.

    Ordering is fixed (task id) at every output so the result is byte-identical across
    runs. ``bar`` defaults to a standard :class:`TrajectoryQualityBar`.
    """
    quality = bar if bar is not None else TrajectoryQualityBar()

    usable: list[TaskOutcome] = []
    skips: list[Skip] = []
    for outcome in outcomes:
        if not outcome.task_id:
            skips.append(Skip(task_id="", reason="missing task id"))
            continue
        usable.append(outcome)

    medians = category_medians(usable)

    accepted: list[TaskOutcome] = []
    rejections: list[Rejection] = []
    sft: list[dict[str, Any]] = []
    rlvr: list[dict[str, Any]] = []

    for outcome in usable:
        median = medians.get(outcome.category, 0.0)
        reason = quality.reject_reason(outcome, category_median_turns=median)
        try:
            rlvr.append(to_rlvr(outcome))
            if not reason:
                sft.append(to_sharegpt(outcome))
        except (TypeError, ValueError) as exc:
            # A record that will not serialize is a skip, not a crash: one poisoned
            # transcript must not cost the other 999 their dataset.
            skips.append(Skip(task_id=outcome.task_id, reason=f"unserializable: {exc}"))
            continue
        if reason:
            failure = failure_class_of(outcome)
            rejections.append(
                Rejection(
                    task_id=outcome.task_id,
                    reason=reason,
                    failure_class=failure.value if failure is not None else "",
                )
            )
        else:
            accepted.append(outcome)

    dpo = _dpo_pairs(usable, accepted)

    return HarvestResult(
        sft=tuple(sorted(sft, key=_task_id_key)),
        dpo=tuple(sorted(dpo, key=_task_id_key)),
        rlvr=tuple(sorted(rlvr, key=_task_id_key)),
        accepted=tuple(sorted(outcome.task_id for outcome in accepted)),
        rejections=tuple(sorted(rejections, key=lambda item: item.task_id)),
        skips=tuple(skips),
        category_medians=medians,
    )


def _task_id_key(record: Mapping[str, Any]) -> str:
    value = record.get("task_id", "")
    return value if isinstance(value, str) else ""


def _dpo_pairs(
    usable: Sequence[TaskOutcome], accepted: Sequence[TaskOutcome]
) -> list[dict[str, Any]]:
    """Pair each task's best accepted run against a failed run of the same task.

    The natural preference signal a harvest can produce for free: for a task the agent
    solved *and* fumbled, the solved run's final answer is preferred over the fumbled
    one's. It only fires when both exist for one task id — which, in practice, is rare,
    and is why :func:`make_pair` exists for the hand-authored and failed-run rejects that
    are the real source. The pick is deterministic: the accepted run with the fewest
    turns is the chosen, the first verify-failed run (in input order) is the rejected.
    """
    chosen_by_task: dict[str, TaskOutcome] = {}
    for outcome in sorted(accepted, key=lambda item: (item.turns, item.task_id)):
        chosen_by_task.setdefault(outcome.task_id, outcome)

    rejected_by_task: dict[str, TaskOutcome] = {}
    for outcome in usable:
        if not outcome.verify_passed:
            rejected_by_task.setdefault(outcome.task_id, outcome)

    pairs: list[dict[str, Any]] = []
    for task_id in sorted(chosen_by_task.keys() & rejected_by_task.keys()):
        chosen = chosen_by_task[task_id]
        rejected = rejected_by_task[task_id]
        pairs.append(
            to_dpo(
                make_pair(
                    prompt=chosen.prompt or rejected.prompt,
                    chosen=chosen.final_assistant_text,
                    rejected=rejected.final_assistant_text,
                    task_id=task_id,
                )
            )
        )
    return pairs


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

#: The three files a harvest writes, paired with the result attribute each drains.
DATASET_FILES: Final[tuple[str, ...]] = ("sft", "dpo", "rlvr")


def write_dataset(result: HarvestResult, out_dir: Path) -> dict[str, Path]:
    """Write ``sft.jsonl``, ``dpo.jsonl`` and ``rlvr.jsonl`` under ``out_dir``.

    One JSON object per line (``json.dumps`` with sorted keys, so the bytes are stable),
    the records already in task-id order from :func:`harvest`. Returns the path of each
    file written, so a caller need not reconstruct the names it just asked for.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name in DATASET_FILES:
        records: tuple[dict[str, Any], ...] = getattr(result, name)
        path = out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        written[name] = path
    return written


__all__ = [
    "ASSISTANT_TAG",
    "DATASET_FILES",
    "FROM_TAG",
    "DPOPair",
    "HarvestResult",
    "Rejection",
    "Skip",
    "TaskOutcome",
    "TrajectoryQualityBar",
    "category_medians",
    "failure_class_of",
    "harvest",
    "make_pair",
    "messages_from_transcript",
    "outcome_from_transcript",
    "render_message",
    "render_tool_call",
    "repeated_tool_errors",
    "to_dpo",
    "to_rlvr",
    "to_sharegpt",
    "tool_validity",
    "verifiable_reward",
    "write_dataset",
]
