"""Folding the middle of a long session without breaking it.

At 80% of the context window something has to go, and *what* goes decides whether
the agent stays useful or gets amnesia. The rules here are the ones a long session
actually needs:

* **Pinned, never summarized:** the system prompt, the repo map, RONIN.md, and the
  last three turns. The first three are the agent's identity and its map of the
  world; the last three are what it is doing right now.
* **The middle becomes a fixed five-part summary** — goal / files touched / what
  was learned / what failed and why / what is left. Fixed because a free-form
  summary drifts toward narrative ("we explored the codebase") and drops the two
  parts that matter most: the failures, and the unfinished work.
* **The most recent tool result per file path survives in full.** This is the
  requirement that makes "what did we edit in turn 3" answerable after two hundred
  turns. A summary of a diff is not a diff.

The critical invariant is pairing. A compaction that keeps a ``ToolUse`` whose
``ToolResultBlock`` was folded away produces a transcript the provider rejects with
a 400, and it rejects it on the *next* turn, so the traceback points at the wrong
place. Retained tool results are therefore re-emitted as freshly synthesized
one-call/one-result message couples rather than by keeping the original messages —
an original assistant message often carries three calls of which one is being kept,
and keeping it whole is exactly how the pair gets split. :func:`repair_pairing` is
the final belt-and-braces pass, and it reports what it had to remove.

The summarizer is injected (``Summarizer = Callable[[str], Awaitable[str]]``) so
this module never imports a provider; the orchestrator wires it to the fast model.
If it fails, compaction does **not** raise: a session at 80% of its window that
cannot compact is a session that dies on its next request. It falls back to a
deterministic, locally-computed digest, says so in the transcript marker, and
records the error on the result.

Token counts here are estimates — ``len(text) / 4``, a rule of thumb, not a
measurement. No tokenizer is available at this layer and taking a dependency on one
per provider would be worse than being explicit about the approximation.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.types import (
    Message,
    Role,
    Text,
    ToolResultBlock,
    ToolUse,
    unpaired_tool_uses,
)
from .budget import CHARS_PER_TOKEN, OutputBudget, clamp, estimate_tokens

#: What the orchestrator wires to the fast model. Takes a prompt, returns a summary.
Summarizer = Callable[[str], Awaitable[str]]

#: Fraction of the context window at which compaction fires.
DEFAULT_TRIGGER_FRACTION = 0.8

#: Turns kept verbatim at the end of the transcript.
DEFAULT_PINNED_TAIL_TURNS = 3

#: A message with truthy ``metadata[PINNED_KEY]`` is never folded. This is how the
#: repo map and RONIN.md survive when they are carried as messages rather than in
#: the provider layer's stable prefix.
PINNED_KEY = "pinned"

#: Set on the summary message the fold produces.
COMPACTION_KEY = "compaction"

#: Set on each re-emitted tool couple, naming the path whose result it preserves.
RETAINED_PATH_KEY = "retained_path"

#: The five sections, in order. Fixed shape: see the module docstring.
SUMMARY_SECTIONS: tuple[str, ...] = (
    "## Goal",
    "## Files touched",
    "## Learned",
    "## Failed",
    "## Remaining",
)

SUMMARY_INSTRUCTIONS = """\
You are compacting the middle of a coding session's transcript so the agent can \
keep working with less context. Write ONLY these five sections, with these headings \
verbatim, in this order:

## Goal
What the user is trying to achieve, in one or two sentences.

## Files touched
One line per file: the path, and what was done to it. Be specific about paths.

## Learned
Facts discovered about the codebase that would cost tool calls to rediscover.

## Failed
What was tried and did not work, and why. Do not omit this: repeating a failed \
approach is the most expensive thing a compacted agent does.

## Remaining
What is still to do, as concrete next steps.

Do not add commentary, apologies, or sections of your own. Do not invent facts that \
are not in the transcript.

TRANSCRIPT
----------
"""

#: Argument keys, in priority order, that carry the path a tool call is about.
#: Explicit rather than pattern-matched: a heuristic like "any key containing
#: 'path'" also matches ``search_path`` on a grep over the whole tree, and then the
#: retention map is keyed on ``"."`` and keeps nothing useful.
PATH_ARGUMENT_KEYS: tuple[str, ...] = (
    "file_path",
    "path",
    "notebook_path",
    "filename",
    "file",
    "target",
    "target_path",
    "new_path",
    "old_path",
)


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    """When to compact and what is untouchable when it happens."""

    context_window: int
    trigger_fraction: float = DEFAULT_TRIGGER_FRACTION
    pinned_tail_turns: int = DEFAULT_PINNED_TAIL_TURNS
    #: Keep the most recent tool result per unique file path, in full.
    retain_tool_results_per_path: bool = True
    #: Ceiling on one retained result, in characters; ``None`` for no ceiling.
    #: Defaults to no ceiling because the point of retention is *full* text; a
    #: caller whose files are enormous can bound it and get a marked truncation.
    max_retained_chars: int | None = None
    #: Ceiling on how many paths are retained at all; ``None`` for no ceiling.
    #: Unbounded by default, and that default is load-bearing: it is what makes a
    #: file edited in turn 3 still answerable in turn 200. Setting it keeps the
    #: most recently touched paths and **gives that guarantee up** — a session
    #: that touches more unique files than the window can hold their results is
    #: the case where compaction alone cannot get under the trigger, and the
    #: caller has to choose which property to keep.
    max_retained_paths: int | None = None

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("CompactionPolicy.context_window must be positive")
        if not 0.0 < self.trigger_fraction <= 1.0:
            raise ValueError(
                "CompactionPolicy.trigger_fraction must be in (0, 1] — a fraction "
                "of 0 would compact on every turn and one above 1 never would"
            )
        if self.pinned_tail_turns < 1:
            raise ValueError(
                "CompactionPolicy.pinned_tail_turns must be >= 1 — folding the turn "
                "in progress leaves the model answering a question it cannot see"
            )
        if self.max_retained_chars is not None and self.max_retained_chars <= 0:
            raise ValueError("CompactionPolicy.max_retained_chars must be positive or None")
        if self.max_retained_paths is not None and self.max_retained_paths <= 0:
            raise ValueError("CompactionPolicy.max_retained_paths must be positive or None")

    @property
    def trigger_tokens(self) -> int:
        return int(self.context_window * self.trigger_fraction)


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """Where the knife goes: what is pinned, what is folded, what is verbatim.

    Separated from the fold itself so the boundary arithmetic — which is where the
    pairing bugs live — is testable without a summarizer.
    """

    head_end: int
    tail_start: int
    #: Indices inside the middle that carry an explicit pin and are hoisted, in
    #: order, to just after the head.
    hoisted: tuple[int, ...] = ()

    @property
    def has_middle(self) -> bool:
        return self.tail_start > self.head_end and bool(
            set(range(self.head_end, self.tail_start)) - set(self.hoisted)
        )


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """The compacted transcript and an honest account of what compaction did."""

    messages: tuple[Message, ...]
    compacted: bool
    folded_messages: int = 0
    retained_paths: tuple[str, ...] = ()
    token_estimate_before: int = 0
    token_estimate_after: int = 0
    marker: str = ""
    summary: str = ""
    #: Non-empty when the injected summarizer failed and the fallback digest ran.
    summarizer_error: str = ""
    #: Sections the summarizer omitted. Reported, not repaired — rewriting a
    #: model's summary to fit a template is how a wrong summary becomes confident.
    missing_sections: tuple[str, ...] = ()
    #: Orphan tool blocks :func:`repair_pairing` had to remove. Should be zero.
    dropped_blocks: int = 0
    #: The policy's trigger, carried so a caller can see the one case compaction
    #: cannot fix on its own: retained tool results alone exceeding the trigger.
    trigger_tokens: int = 0

    @property
    def still_over_trigger(self) -> bool:
        """Whether the compacted transcript is *still* at or above the trigger.

        True means retention (or the pinned tail) is bigger than the budget allows,
        and the orchestrator must escalate — bound ``max_retained_paths``, bound
        ``max_retained_chars``, or start a fresh session. Compacting again would
        change nothing, and a caller that loops on ``should_compact`` without
        checking this spins."""
        return bool(self.trigger_tokens) and self.token_estimate_after >= self.trigger_tokens


# --------------------------------------------------------------------------- #
# estimation
# --------------------------------------------------------------------------- #


def render_message(message: Message) -> str:
    """A deterministic textual form of a message, for estimation and prompting.

    Includes role, tool names and arguments because those are real tokens in the
    request — an estimator that counted only prose would under-count a transcript
    made mostly of tool calls, which is the shape of every long coding session.
    """
    parts = [f"[{message.role.value}]"]
    for block in message.content_blocks:
        if isinstance(block, Text):
            parts.append(block.text)
        elif isinstance(block, ToolUse):
            parts.append(f"call {block.name}({_render_arguments(block.arguments)})")
        elif isinstance(block, ToolResultBlock):
            prefix = "error" if block.is_error else "result"
            parts.append(f"{prefix} {block.tool_use_id}: {block.content}")
        else:
            parts.append(block.text)
    return "\n".join(parts)


def _render_arguments(arguments: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={arguments[key]!r}" for key in sorted(arguments))


def message_tokens(message: Message) -> int:
    return estimate_tokens(render_message(message))


def transcript_tokens(messages: Sequence[Message]) -> int:
    return sum(message_tokens(message) for message in messages)


def should_compact(
    messages: Sequence[Message],
    *,
    policy: CompactionPolicy,
    pinned_prefix_tokens: int = 0,
) -> bool:
    """Whether the transcript has reached the trigger.

    ``pinned_prefix_tokens`` accounts for context that is *not* in ``messages`` —
    the system prompt, repo map and RONIN.md when they live in the provider layer's
    stable prefix. Ignoring it is how a session with a 30k-token repo map thinks it
    is at 40% of the window while the provider sees 95%.
    """
    return transcript_tokens(messages) + pinned_prefix_tokens >= policy.trigger_tokens


# --------------------------------------------------------------------------- #
# path extraction
# --------------------------------------------------------------------------- #


def file_path_from_arguments(arguments: Mapping[str, Any]) -> str | None:
    """The file path a tool call is about, or ``None``.

    First key from :data:`PATH_ARGUMENT_KEYS` with a non-empty string value wins.
    Normalized with ``PurePosix``-style separators so ``./src/a.py`` and
    ``src/a.py`` are the same key — otherwise "most recent result per path" keeps
    two copies and the retention budget goes on duplicates.
    """
    for key in PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_path(value.strip())
    return None


def _normalize_path(value: str) -> str:
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or value


def latest_tool_result_per_path(
    messages: Sequence[Message],
) -> dict[str, tuple[ToolUse, ToolResultBlock]]:
    """Most recent ``(use, result)`` pair for each unique file path in ``messages``.

    Unanswered calls are skipped: a pair is only useful if it can be re-emitted
    with its result, and re-emitting a call without one breaks pairing.
    """
    results: dict[str, ToolResultBlock] = {
        block.tool_use_id: block
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    latest: dict[str, tuple[ToolUse, ToolResultBlock]] = {}
    for message in messages:
        for block in message.content_blocks:
            if not isinstance(block, ToolUse):
                continue
            path = file_path_from_arguments(block.arguments)
            answer = results.get(block.id)
            if path is None or answer is None:
                continue
            latest[path] = (block, answer)
    return latest


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def plan_compaction(
    messages: Sequence[Message], *, policy: CompactionPolicy
) -> CompactionPlan:
    """Decide the head/middle/tail split without touching anything."""
    head_end = _pinned_head_end(messages)
    tail_start = _tail_start(messages, head_end, policy.pinned_tail_turns)
    tail_start = _avoid_splitting_pairs(messages, head_end, tail_start)
    hoisted = tuple(
        index
        for index in range(head_end, tail_start)
        if messages[index].metadata.get(PINNED_KEY)
    )
    return CompactionPlan(head_end=head_end, tail_start=tail_start, hoisted=hoisted)


def _pinned_head_end(messages: Sequence[Message]) -> int:
    """Length of the leading run of pinned context.

    Only the *leading* run of system messages counts. ``core.loop`` inserts
    system-role stall nudges mid-transcript, and pinning those would keep the
    scaffolding while folding the work.
    """
    end = 0
    for message in messages:
        if message.role is Role.SYSTEM or message.metadata.get(PINNED_KEY):
            end += 1
            continue
        break
    return end


def _is_user_turn(message: Message) -> bool:
    """A genuine user turn, not a message that merely carries tool results.

    Some wirings put ``ToolResultBlock``s on a user-role message. Counting those as
    turns would make "the last three turns" mean "the last three tool calls", and
    the tail would no longer contain the user's actual request.
    """
    if message.role is not Role.USER:
        return False
    return not message.tool_results or bool(message.text.strip())


def _tail_start(messages: Sequence[Message], head_end: int, turns: int) -> int:
    boundaries = [
        index
        for index in range(head_end, len(messages))
        if _is_user_turn(messages[index])
    ]
    if len(boundaries) <= turns:
        return head_end
    return boundaries[-turns]


def _avoid_splitting_pairs(
    messages: Sequence[Message], head_end: int, tail_start: int
) -> int:
    """Move the boundary earlier until no tool pair straddles it.

    A result kept in the tail whose call was folded is an orphan the provider
    rejects just as hard as the reverse, and ``unpaired_tool_uses`` cannot see it —
    it only looks for calls without results. This is the fix, not a repair.

    A transcript that reuses a tool-use id (which ``ronin.core.types`` documents as
    a real provider 400 in its own right) can drive the boundary all the way back to
    the head, and compaction then folds nothing rather than mangling the pairs. The
    caller sees ``compacted=False`` with ``still_over_trigger=True``, which is the
    honest report: the input was already broken.
    """
    for _ in range(len(messages) + 1):
        middle_uses = {
            block.id
            for message in messages[head_end:tail_start]
            for block in message.content_blocks
            if isinstance(block, ToolUse)
        }
        if not middle_uses:
            return tail_start
        earliest: int | None = None
        for index in range(tail_start, len(messages)):
            for block in messages[index].content_blocks:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in middle_uses:
                    owner = _owner_index(messages, block.tool_use_id, head_end, tail_start)
                    if owner is not None and (earliest is None or owner < earliest):
                        earliest = owner
        if earliest is None:
            return tail_start
        tail_start = earliest
        if tail_start <= head_end:
            return head_end
    return tail_start


def _owner_index(
    messages: Sequence[Message], tool_use_id: str, start: int, stop: int
) -> int | None:
    for index in range(start, stop):
        for block in messages[index].content_blocks:
            if isinstance(block, ToolUse) and block.id == tool_use_id:
                return index
    return None


# --------------------------------------------------------------------------- #
# the fold
# --------------------------------------------------------------------------- #


async def maybe_compact(
    messages: Sequence[Message],
    *,
    policy: CompactionPolicy,
    summarizer: Summarizer,
    pinned_prefix_tokens: int = 0,
) -> CompactionResult:
    """Compact only if the trigger has been reached. The orchestrator's entry point."""
    if not should_compact(
        messages, policy=policy, pinned_prefix_tokens=pinned_prefix_tokens
    ):
        total = transcript_tokens(messages)
        return CompactionResult(
            messages=tuple(messages),
            compacted=False,
            token_estimate_before=total,
            token_estimate_after=total,
            trigger_tokens=policy.trigger_tokens,
        )
    return await compact(messages, policy=policy, summarizer=summarizer)


async def compact(
    messages: Sequence[Message],
    *,
    policy: CompactionPolicy,
    summarizer: Summarizer,
) -> CompactionResult:
    """Fold the middle. Unconditional — call :func:`maybe_compact` for the trigger.

    Never raises on a summarizer failure; see the module docstring.
    """
    before = transcript_tokens(messages)
    plan = plan_compaction(messages, policy=policy)
    if not plan.has_middle:
        return CompactionResult(
            messages=tuple(messages),
            compacted=False,
            token_estimate_before=before,
            token_estimate_after=before,
            trigger_tokens=policy.trigger_tokens,
        )

    head = list(messages[: plan.head_end])
    middle = list(messages[plan.head_end : plan.tail_start])
    tail = list(messages[plan.tail_start :])
    hoisted = [messages[index] for index in plan.hoisted]
    foldable = [
        message
        for index, message in enumerate(middle, start=plan.head_end)
        if index not in set(plan.hoisted)
    ]

    summary, error = await _summarize(foldable, summarizer)
    retained = _retained_couples(foldable, policy) if policy.retain_tool_results_per_path else []
    retained_paths = tuple(path for path, _ in retained)

    marker = ""
    body: list[Message] = []
    repaired = 0
    # The marker names the resulting token estimate, and the marker is itself part
    # of the transcript it is measuring. Two passes settle it; the third exists
    # only so a digit-count change cannot leave a stale number in the text. The
    # loop always leaves ``marker`` equal to the marker actually inside ``body``.
    for _ in range(3):
        candidate = _marker(
            folded=len(foldable),
            retained=len(retained),
            tokens=_provisional_tokens(head, hoisted, summary, marker, retained, tail),
            summarizer_error=error,
            dropped=repaired,
        )
        body, repaired = repair_pairing(
            [
                *head,
                *hoisted,
                _summary_message(candidate, summary),
                *(message for _, couple in retained for message in couple),
                *tail,
            ]
        )
        settled = candidate == marker
        marker = candidate
        if settled:
            break

    return CompactionResult(
        messages=tuple(body),
        compacted=True,
        folded_messages=len(foldable),
        retained_paths=retained_paths,
        token_estimate_before=before,
        token_estimate_after=transcript_tokens(body),
        marker=marker,
        summary=summary,
        summarizer_error=error,
        missing_sections=missing_sections(summary),
        dropped_blocks=repaired,
        trigger_tokens=policy.trigger_tokens,
    )


def build_summary_prompt(messages: Sequence[Message]) -> str:
    """The prompt handed to the injected summarizer."""
    rendered = "\n\n".join(render_message(message) for message in messages)
    return SUMMARY_INSTRUCTIONS + rendered


def missing_sections(summary: str) -> tuple[str, ...]:
    """Which of the five required headings the summary does not contain."""
    return tuple(section for section in SUMMARY_SECTIONS if section not in summary)


async def _summarize(
    messages: Sequence[Message], summarizer: Summarizer
) -> tuple[str, str]:
    """``(summary, error)``. A failing summarizer yields the local digest, not a raise."""
    try:
        summary = await summarizer(build_summary_prompt(messages))
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return _fallback_summary(messages), f"{type(exc).__name__}: {exc}"
    if not summary.strip():
        return _fallback_summary(messages), "summarizer returned empty text"
    return summary.strip(), ""


def _fallback_summary(messages: Sequence[Message]) -> str:
    """A five-part digest computed locally, with no model involved.

    Every line is derived from the transcript, so it states less than a model would
    but cannot state anything false. The ``Learned``/``Remaining`` sections say
    outright that they are unavailable rather than guessing.
    """
    goal = next(
        (m.text.strip() for m in messages if m.role is Role.USER and m.text.strip()),
        "(no user message in the folded range)",
    )
    touched: list[str] = []
    failures: list[str] = []
    results = {
        block.tool_use_id: block
        for m in messages
        for block in m.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    for message in messages:
        for block in message.content_blocks:
            if not isinstance(block, ToolUse):
                continue
            path = file_path_from_arguments(block.arguments)
            if path is not None:
                entry = f"- {path} — {block.name}"
                if entry not in touched:
                    touched.append(entry)
            answer = results.get(block.id)
            if answer is not None and answer.is_error:
                first_line = answer.content.splitlines()[0] if answer.content else "(no message)"
                failures.append(f"- {block.name} failed: {first_line}")
    return "\n".join(
        [
            "## Goal",
            goal.splitlines()[0][:500] if goal else "(unknown)",
            "",
            "## Files touched",
            *(touched or ["- (no file-scoped tool calls in the folded range)"]),
            "",
            "## Learned",
            "- unavailable: the summarizer failed, so only mechanical facts are kept",
            "",
            "## Failed",
            *(failures or ["- (no failing tool results in the folded range)"]),
            "",
            "## Remaining",
            "- unavailable: re-derive from the retained tool results and the last turns",
        ]
    )


def _summary_message(marker: str, summary: str) -> Message:
    """The fold, as one system message: the visible marker plus the summary.

    System role rather than user role because it is not something the user said,
    and ``core.loop`` already relies on mid-transcript system messages being
    renderable by every adapter (it inserts stall nudges the same way).
    """
    return Message(
        role=Role.SYSTEM,
        content_blocks=(Text(f"{marker}\n\n{summary}"),),
        metadata={COMPACTION_KEY: True},
    )


def _marker(
    *, folded: int, retained: int, tokens: int, summarizer_error: str, dropped: int
) -> str:
    parts = [
        f"[context compacted: {folded} message(s) folded into a five-part summary",
        f"{retained} tool result(s) kept in full (most recent per file path)",
        f"transcript now ~{tokens:,} tokens (estimated at {CHARS_PER_TOKEN} chars/token)",
    ]
    if summarizer_error:
        parts.append(f"summarizer FAILED ({summarizer_error}) — local digest used instead")
    if dropped:
        parts.append(f"{dropped} orphan tool block(s) dropped to keep the transcript pairable")
    return "; ".join(parts) + "]"


def _provisional_tokens(
    head: Sequence[Message],
    hoisted: Sequence[Message],
    summary: str,
    marker: str,
    retained: Sequence[tuple[str, tuple[Message, Message]]],
    tail: Sequence[Message],
) -> int:
    return (
        transcript_tokens(head)
        + transcript_tokens(hoisted)
        + estimate_tokens(f"[system]\n{marker}\n\n{summary}")
        + sum(transcript_tokens(couple) for _, couple in retained)
        + transcript_tokens(tail)
    )


def _retained_couples(
    messages: Sequence[Message], policy: CompactionPolicy
) -> list[tuple[str, tuple[Message, Message]]]:
    """One synthesized call/result couple per unique path, in first-call order.

    Synthesized rather than reused: the original assistant message usually carries
    several calls, and keeping it whole to save one result orphans the others.
    """
    latest = latest_tool_result_per_path(messages)
    order: dict[str, int] = {}
    for index, message in enumerate(messages):
        for block in message.content_blocks:
            if not isinstance(block, ToolUse):
                continue
            path = file_path_from_arguments(block.arguments)
            if path is not None and path in latest:
                order[path] = index
    selected = sorted(latest, key=lambda p: order.get(p, 0))
    if policy.max_retained_paths is not None and len(selected) > policy.max_retained_paths:
        # Keep the most recently touched. See the field's docstring for what this
        # costs: an early-turn file can fall out of the retained set entirely.
        selected = selected[-policy.max_retained_paths :]
    couples: list[tuple[str, tuple[Message, Message]]] = []
    for path in selected:
        use, result = latest[path]
        content = result.content
        if policy.max_retained_chars is not None:
            content = clamp(
                content, budget=OutputBudget(remaining_chars=policy.max_retained_chars)
            ).text
        couples.append(
            (
                path,
                (
                    Message(
                        role=Role.ASSISTANT,
                        content_blocks=(use,),
                        metadata={RETAINED_PATH_KEY: path},
                    ),
                    Message(
                        role=Role.TOOL,
                        content_blocks=(
                            ToolResultBlock(
                                tool_use_id=result.tool_use_id,
                                content=content,
                                is_error=result.is_error,
                            ),
                        ),
                        metadata={RETAINED_PATH_KEY: path},
                    ),
                ),
            )
        )
    return couples


def repair_pairing(messages: Sequence[Message]) -> tuple[list[Message], int]:
    """Drop orphan tool blocks, returning the transcript and how many were dropped.

    The planner is supposed to make this a no-op, and the tests assert that it is.
    It exists because the cost of being wrong is a provider 400 on the next turn
    rather than a visible failure here, and a dropped block is at least reported.
    """
    answered = {
        block.tool_use_id
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    called = {
        block.id
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    }
    dropped = 0
    out: list[Message] = []
    for message in messages:
        kept = []
        for block in message.content_blocks:
            if isinstance(block, ToolUse) and block.id not in answered:
                dropped += 1
                continue
            if isinstance(block, ToolResultBlock) and block.tool_use_id not in called:
                dropped += 1
                continue
            kept.append(block)
        if not kept and message.content_blocks:
            continue
        out.append(
            message
            if len(kept) == len(message.content_blocks)
            else Message(
                role=message.role,
                content_blocks=tuple(kept),
                metadata=message.metadata,
            )
        )
    if unpaired_tool_uses(out):  # pragma: no cover - the loop above cannot leave any
        raise AssertionError("repair_pairing left unpaired tool uses")
    return out, dropped
