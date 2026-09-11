"""What every adapter needs and none of them should own a copy of.

Reading an escalation answer, and deciding whether a piece of text is somebody
speaking or somebody quoting, are not platform-specific. They started life in
the GitHub adapter and Slack imported them from there, which is the shape of a
bug this project has already paid for three times in the safety layer: one
concept with two homes, and the copy that drifts is always the one in the module
that merely *uses* it.

So they live here, and every adapter imports them rather than reimplementing the
half it needs.

The stripping rule is the load-bearing one. ``@ronin`` — or ``<@U07RONIN>``, or
``allow esc-abc123`` — inside a fenced block is documentation, and inside a
``>`` quote is somebody repeating what was already said, usually the Retainer's
own earlier comment quoted back by the reply UI. Counting either is how a bot
answers the echo of its own voice, which is why the escalation templates put
their instructions in code: stripped before anything looks, they cannot wake a
Retainer or decide an approval no matter who reposts them.
"""

from __future__ import annotations

import re
from typing import Final

_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_QUOTED = re.compile(r"^\s*>.*$", re.MULTILINE)

_ANSWER = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<verdict>allow|deny|approve|reject)\s+(?P<id>esc-[A-Za-z0-9]+)",
    re.IGNORECASE,
)

#: The words that mean yes. Spelling both ``allow`` and ``approve`` costs
#: nothing and saves somebody who typed the obvious synonym from being ignored.
APPROVING: Final = frozenset({"allow", "approve"})


def strip_quotes_and_code(text: str) -> str:
    """Remove fenced blocks, inline code and quoted lines.

    Order matters: fenced blocks first, because a ``>`` inside one is code and
    not a quote, and removing quotes first would leave the fence unbalanced.
    """
    without_fences = _FENCED.sub(" ", text)
    without_code = _INLINE_CODE.sub(" ", without_fences)
    return _QUOTED.sub(" ", without_code)


def read_answer(text: str) -> tuple[str, bool] | None:
    """An escalation id and whether it was approved, or ``None`` if not an answer.

    Quoted and code-fenced text is stripped first, so a Retainer cannot read its
    own instructions back out of a quoted copy of the message it posted.
    """
    match = _ANSWER.search(strip_quotes_and_code(text))
    if match is None:
        return None
    return match.group("id"), match.group("verdict").lower() in APPROVING


__all__ = ["APPROVING", "read_answer", "strip_quotes_and_code"]
