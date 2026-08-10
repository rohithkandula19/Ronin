"""The gate every tool result passes through on its way to the model.

One job: given how much room is left, cut oversized output *deterministically* and
say exactly what was cut. Two properties matter more than any cleverness here.

1. **Head and tail, not head alone.** A build log's failure is at the end and its
   command line is at the start; a truncator that keeps only the head throws away
   the answer. The split is a fixed 60/40 so the same input always yields the same
   output — a truncator that varies with wall-clock or dict order also varies the
   prompt prefix, and a varying prefix is a cache miss on every turn.
2. **The marker names the true count.** ``[... 412 lines elided ...]`` with the
   real number, not "output truncated". The model uses it to decide whether to
   re-read with an offset, and a wrong or vague count makes that decision badly.

The marker itself is content, so it is charged against the budget: the fit is
solved to a fixpoint because the marker's own length depends on the number it
reports. A budget too small to hold even the marker returns the marker alone —
mode :attr:`ClampMode.MARKER_ONLY`, the one case where the returned text may
exceed the budget. Returning empty text instead would be a silent drop, which is
the failure this module exists to prevent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

#: Fraction of the surviving content taken from the head. The rest is the tail.
HEAD_FRACTION = 0.6

#: Characters per token. A rule of thumb, not a measurement: no tokenizer exists at
#: this layer, and taking a per-provider tokenizer dependency to sharpen a
#: truncation threshold would cost more than the sharpening is worth. Every token
#: number this package reports is an estimate at this ratio, and says so.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """``len(text) / 4``, rounded up. The one estimator the package shares."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


#: The marker's length depends on the count it reports, and the count depends on
#: how much room the marker leaves. Four passes is far more than the one or two a
#: digit-count change needs; the loop exits early once the count stops moving.
_MARKER_PASSES = 4


def line_marker(elided_lines: int) -> str:
    """The one and only elision marker for line-oriented clamping."""
    return f"[... {elided_lines} lines elided ...]"


def char_marker(elided_chars: int) -> str:
    """The fallback marker, which says *why* it is counting characters.

    A single enormous line — a minified bundle, a base64 blob, a one-line JSON
    response — has no line breaks to split on, and a model told "0 lines elided"
    would conclude nothing was cut.
    """
    return f"[... {elided_chars} chars elided (single line, no line breaks to split on) ...]"


class ClampMode(StrEnum):
    """How a piece of text was cut, or that it was not."""

    NONE = "none"
    LINES = "lines"
    CHARS = "chars"
    MARKER_ONLY = "marker_only"


@dataclass(frozen=True, slots=True)
class OutputBudget:
    """What is left to spend on tool output, in characters and optionally lines.

    Characters rather than tokens because this layer never sees a tokenizer; the
    orchestrator converts a token budget into characters at whatever ratio its
    provider warrants. ``remaining_lines`` is a second, independent ceiling for
    the case where a model drowns in ten thousand one-character lines that cost
    almost nothing in characters.
    """

    remaining_chars: int
    remaining_lines: int | None = None
    head_fraction: float = HEAD_FRACTION

    def __post_init__(self) -> None:
        if self.remaining_chars < 0:
            raise ValueError("OutputBudget.remaining_chars must be >= 0")
        if self.remaining_lines is not None and self.remaining_lines < 0:
            raise ValueError("OutputBudget.remaining_lines must be >= 0 or None")
        if not 0.0 < self.head_fraction < 1.0:
            raise ValueError(
                "OutputBudget.head_fraction must be strictly between 0 and 1 — "
                "an all-head or all-tail split loses the other end of the output"
            )

    def fits(self, text: str) -> bool:
        """Whether ``text`` can be handed over untouched."""
        if len(text) > self.remaining_chars:
            return False
        if self.remaining_lines is None:
            return True
        return _count_lines(text) <= self.remaining_lines

    def spend(self, text: str) -> OutputBudget:
        """The budget that remains after ``text`` is charged against it.

        Floors at zero rather than going negative: a budget is a ceiling, and a
        negative remainder would make :func:`clamp` produce marker-only output for
        every subsequent call without saying why.
        """
        lines = None
        if self.remaining_lines is not None:
            lines = max(0, self.remaining_lines - _count_lines(text))
        return OutputBudget(
            remaining_chars=max(0, self.remaining_chars - len(text)),
            remaining_lines=lines,
            head_fraction=self.head_fraction,
        )


@dataclass(frozen=True, slots=True)
class Clamped:
    """Text after clamping, plus exactly what clamping did to it.

    The counts are carried separately from the marker so a caller can log or
    aggregate them without re-parsing the string it just built.
    """

    text: str
    mode: ClampMode
    elided_lines: int = 0
    elided_chars: int = 0
    marker: str = ""

    @property
    def truncated(self) -> bool:
        return self.mode is not ClampMode.NONE


def clamp(text: str, *, budget: OutputBudget) -> Clamped:
    """Fit ``text`` inside ``budget``, head 60% / tail 40%, marking what was cut.

    Byte-identical passthrough when the text already fits — the fast path has to
    be *exactly* the input, because a truncator that reformats short output is a
    truncator that changes a diff nobody asked it to change.
    """
    if budget.fits(text):
        return Clamped(text=text, mode=ClampMode.NONE)
    lines = text.splitlines(keepends=True)
    if len(lines) <= 1:
        return _clamp_chars(text, budget)
    return _clamp_lines(text, lines, budget)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _count_lines(text: str) -> int:
    """Lines as a human counts them: a trailing newline does not add one."""
    if not text:
        return 0
    return len(text.splitlines())


def _clamp_lines(text: str, lines: list[str], budget: OutputBudget) -> Clamped:
    head_n = 0
    tail_n = 0
    elided = len(lines)
    for _ in range(_MARKER_PASSES):
        marker = line_marker(elided)
        head_n, tail_n = _fit_lines(lines, budget, len(marker) + 1)
        settled = len(lines) - head_n - tail_n
        if settled == elided:
            break
        elided = settled
    if head_n == 0 and tail_n == 0:
        return _marker_only(
            line_marker(len(lines)), elided_lines=len(lines), elided_chars=len(text)
        )

    head = "".join(lines[:head_n])
    tail = "".join(lines[len(lines) - tail_n :]) if tail_n else ""
    marker = line_marker(len(lines) - head_n - tail_n)
    body = [head if head.endswith("\n") or not head else head + "\n"]
    body.append(marker + "\n" if tail else marker)
    body.append(tail)
    return Clamped(
        text="".join(body),
        mode=ClampMode.LINES,
        elided_lines=len(lines) - head_n - tail_n,
        elided_chars=len(text) - len(head) - len(tail),
        marker=marker,
    )


def _fit_lines(lines: list[str], budget: OutputBudget, marker_cost: int) -> tuple[int, int]:
    """How many whole lines fit at the head and at the tail. Never all of them.

    Greedy from each end, which is what makes it reproducible: no search, no
    balancing, no dependence on anything but the line lengths.
    """
    allowance = budget.remaining_chars - marker_cost
    if allowance <= 0:
        return 0, 0
    head_chars = int(allowance * budget.head_fraction)
    tail_chars = allowance - head_chars

    head_cap: int | None = None
    tail_cap: int | None = None
    if budget.remaining_lines is not None:
        line_allowance = budget.remaining_lines - 1  # the marker occupies a line
        if line_allowance <= 0:
            return 0, 0
        head_cap = int(line_allowance * budget.head_fraction)
        tail_cap = line_allowance - head_cap

    head_n = 0
    used = 0
    for line in lines:
        if head_cap is not None and head_n >= head_cap:
            break
        if used + len(line) > head_chars:
            break
        used += len(line)
        head_n += 1

    tail_n = 0
    used = 0
    for line in reversed(lines[head_n:]):
        if tail_cap is not None and tail_n >= tail_cap:
            break
        if used + len(line) > tail_chars:
            break
        used += len(line)
        tail_n += 1

    if head_n + tail_n >= len(lines):
        # Claiming truncation while eliding nothing would make the marker a lie.
        tail_n = max(0, len(lines) - head_n - 1)
    return head_n, tail_n


def _clamp_chars(text: str, budget: OutputBudget) -> Clamped:
    head_n = 0
    tail_n = 0
    elided = len(text)
    for _ in range(_MARKER_PASSES):
        marker = char_marker(elided)
        allowance = budget.remaining_chars - len(marker)
        if allowance <= 0:
            return _marker_only(char_marker(len(text)), elided_lines=0, elided_chars=len(text))
        head_n = int(allowance * budget.head_fraction)
        tail_n = allowance - head_n
        settled = len(text) - head_n - tail_n
        if settled == elided:
            break
        elided = settled
    marker = char_marker(len(text) - head_n - tail_n)
    return Clamped(
        text=text[:head_n] + marker + (text[len(text) - tail_n :] if tail_n else ""),
        mode=ClampMode.CHARS,
        elided_lines=0,
        elided_chars=len(text) - head_n - tail_n,
        marker=marker,
    )


def _marker_only(marker: str, *, elided_lines: int, elided_chars: int) -> Clamped:
    """The budget cannot hold the marker. Return it anyway; see the module docstring."""
    return Clamped(
        text=marker,
        mode=ClampMode.MARKER_ONLY,
        elided_lines=elided_lines,
        elided_chars=elided_chars,
        marker=marker,
    )
