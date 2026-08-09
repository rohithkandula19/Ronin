"""One cheap second opinion before saying "done".

Tests prove the code does what the tests say. Nothing in a green suite proves the
code does what the *user* asked, and the most common way an agent wastes a
person's afternoon is finishing confidently on three of the four things they
listed. So: one pass, one fast model, one question — does this diff do what was
asked, and if not, what is missing.

The design is deliberately the cheap version. One pass, not a debate. A fast
model, not the main one. A yes/no answer, not a rubric. If the answer is no, the
loop gets **exactly one** more iteration and then stops regardless, because a
critique loop with no cap is a way to spend a user's money disagreeing with
yourself.

Parsing is tolerant on purpose, and biased. A fast model asked for "yes" or "no"
will sometimes answer ``NO — the CLI flag is missing``, sometimes ``❌``,
sometimes with two sentences of preamble first. All of those parse. An answer that
parses to nothing at all is treated as **no**, with the raw text as the missing
piece: one extra iteration costs a turn, whereas a mis-read "no" that becomes a
"yes" ships an unfinished task.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

#: The whole prompt, as a module constant so a reviewer can read exactly what is
#: being asked without running anything. Short on purpose: a long rubric turns a
#: cheap check into a second opinion about style, and style is not what this
#: catches.
CRITIQUE_PROMPT: Final[str] = """\
You are checking one thing and nothing else: does this change do what was asked?

What was asked:
{objective}

What changed:
{diff}

Answer on the first line with exactly `yes` or `no`.
- `yes` means every part of the request is addressed by this change.
- `no` means something asked for is missing or wrong.
If `no`, use the following lines to say only what is missing, concretely.
Do not review style, naming, or test coverage. Do not suggest improvements.\
"""

#: The diff is clamped before it goes in the prompt: this is the *fast* model, and
#: a 200k-token diff makes it neither fast nor cheap. Head and tail are kept for
#: the same reason as everywhere else in the codebase.
MAX_DIFF_CHARS: Final[int] = 24_000

#: How many extra iterations a "no" may buy. One. Stated as a constant so the cap
#: appears in the report rather than living in a caller's head.
MAX_EXTRA_ITERATIONS: Final[int] = 1

_YES = re.compile(r"(?:^|[^a-z])(yes|pass(?:es|ed)?|correct|complete|✅|👍)(?:$|[^a-z])", re.I)
_NO = re.compile(r"(?:^|[^a-z])(no|nope|fail(?:s|ed)?|incomplete|missing|❌|👎)(?:$|[^a-z])", re.I)

_UNPARSEABLE = (
    "the critique did not answer yes or no, so this counts as no; the raw answer "
    "follows"
)


@dataclass(frozen=True, slots=True)
class Critique:
    """The verdict. ``passes=False`` always carries something to act on."""

    passes: bool
    missing: str = ""
    raw: str = ""
    parsed: bool = True

    def __post_init__(self) -> None:
        if not self.passes and not self.missing:
            raise ValueError(
                "a failing critique must say what is missing — 'no' with no reason "
                "gives the next iteration nothing to do"
            )


def clamp_diff(diff: str, *, limit: int = MAX_DIFF_CHARS) -> str:
    """Cut the diff to fit the fast model, marking what was removed."""
    if limit <= 0 or len(diff) <= limit:
        return diff
    head_chars = int(limit * 0.5)
    tail_chars = limit - head_chars
    cut = len(diff) - limit
    return (
        f"{diff[:head_chars]}\n"
        f"…[{cut:,} characters of diff cut from the middle]…\n"
        f"{diff[-tail_chars:]}"
    )


def parse_critique(text: str) -> Critique:
    """Read a verdict out of whatever the fast model actually said.

    The first line is trusted when it contains a marker, because that is what the
    prompt asks for; otherwise the whole answer is scanned and the *first* marker
    wins, which is why ``no — the flag is missing`` and ``yes, though I would also…``
    both come out right. Nothing recognizable at all means no.
    """
    stripped = text.strip()
    if not stripped:
        return Critique(
            passes=False, missing=f"{_UNPARSEABLE}: (empty answer)", raw=text, parsed=False
        )

    lines = [line.strip() for line in stripped.splitlines()]
    first = lines[0] if lines else ""
    rest = "\n".join(lines[1:]).strip()

    verdict = _verdict(first)
    if verdict is None:
        verdict = _verdict(stripped)
        rest = stripped
    if verdict is None:
        return Critique(
            passes=False, missing=f"{_UNPARSEABLE}: {stripped}", raw=text, parsed=False
        )
    if verdict:
        return Critique(passes=True, missing="", raw=text)
    return Critique(
        passes=False,
        missing=rest or first or "the critique said no without saying what is missing",
        raw=text,
    )


def _verdict(text: str) -> bool | None:
    """``True``/``False``/``None`` for yes / no / unrecognized, first marker wins."""
    yes = _YES.search(text)
    no = _NO.search(text)
    if yes is None and no is None:
        return None
    if yes is None:
        return False
    if no is None:
        return True
    return yes.start() < no.start()


#: The model seam: one prompt in, one answer out. Injected so this package needs
#: no provider, and so the tests are offline.
CritiqueModel = Callable[[str], Awaitable[str]]

#: The iteration seam: given what the critique said is missing, do one more pass
#: of work and return the new diff to be critiqued.
IterateStep = Callable[[str], Awaitable[str]]


async def critique_change(
    *,
    objective: str,
    diff: str,
    ask: CritiqueModel,
    limit: int = MAX_DIFF_CHARS,
) -> Critique:
    """One pass. Any failure of the model call becomes ``passes=False``.

    A critique that could not run is not a pass: treating an exception as approval
    would make the check silently vanish the moment the fast model is rate-limited,
    which is exactly when a hurried session most needs it.
    """
    prompt = CRITIQUE_PROMPT.format(objective=objective.strip(), diff=clamp_diff(diff, limit=limit))
    try:
        answer = await ask(prompt)
    # Any provider failure — timeout, rate limit, malformed response — means
    # "unknown", and unknown is not a pass.
    except Exception as exc:
        return Critique(
            passes=False,
            missing=(
                f"the critique could not run ({type(exc).__name__}: {exc}), so this "
                "change is unverified"
            ),
            raw="",
            parsed=False,
        )
    return parse_critique(answer)


@dataclass(frozen=True, slots=True)
class CritiqueGate:
    """The result of the gate: every round, and whether it ends up passing."""

    rounds: tuple[Critique, ...]
    iterated: bool
    capped: bool = False

    def __post_init__(self) -> None:
        if not self.rounds:
            raise ValueError("CritiqueGate needs at least one round")

    @property
    def passes(self) -> bool:
        return self.rounds[-1].passes

    @property
    def final(self) -> Critique:
        return self.rounds[-1]

    def render(self) -> str:
        lines: list[str] = []
        for index, critique in enumerate(self.rounds, start=1):
            verdict = "yes" if critique.passes else "no"
            lines.append(f"critique {index}: {verdict}")
            if not critique.passes:
                lines.append(f"  missing: {critique.missing}")
            if not critique.parsed:
                lines.append("  (the answer did not parse, so it counted as no)")
        if self.capped:
            lines.append(
                f"stopping after {MAX_EXTRA_ITERATIONS} extra iteration: the cap is "
                "reached, and this is still not done — say so rather than claiming it is"
            )
        return "\n".join(lines)


async def critique_gate(
    *,
    objective: str,
    diff: str,
    ask: CritiqueModel,
    iterate: IterateStep,
    limit: int = MAX_DIFF_CHARS,
) -> CritiqueGate:
    """Critique once; on "no", iterate exactly once and critique the result.

    Two rounds maximum, always. If the second round still says no,
    :attr:`CritiqueGate.capped` is set and :meth:`CritiqueGate.render` says the cap
    was hit — the caller is expected to report that to the user, not to quietly
    call it done.
    """
    first = await critique_change(objective=objective, diff=diff, ask=ask, limit=limit)
    if first.passes:
        return CritiqueGate(rounds=(first,), iterated=False)

    revised = await iterate(first.missing)
    second = await critique_change(objective=objective, diff=revised, ask=ask, limit=limit)
    return CritiqueGate(rounds=(first, second), iterated=True, capped=not second.passes)


__all__ = [
    "CRITIQUE_PROMPT",
    "MAX_DIFF_CHARS",
    "MAX_EXTRA_ITERATIONS",
    "Critique",
    "CritiqueGate",
    "CritiqueModel",
    "IterateStep",
    "clamp_diff",
    "critique_change",
    "critique_gate",
    "parse_critique",
]
