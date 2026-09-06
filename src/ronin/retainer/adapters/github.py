"""GitHub: a mention becomes a summons, an answer becomes a comment.

``docs/RETAINER.md`` §8 step 7a, the reference adapter. GitHub goes first
because Ronin already lives there and an App brings real identity, scoped
permissions and a signed webhook; the second and third adapters are cheap
precisely because everything this file learns stays in this file.

Four things here are decisions rather than plumbing.

**A Retainer never answers itself.** The single most expensive failure in the
product this design was drawn against was standing agents talking to each other
until a weekly budget was gone in twenty-one seconds. A comment whose author is
the Retainer's own login is dropped before anything else looks at it, and that
check comes first because every other rule is one bug away from re-entering.

**A mention only counts where somebody typed it.** ``@ronin`` inside a fenced
code block is documentation, and inside a ``>`` quote it is somebody repeating
what was already said — usually the Retainer's own earlier comment, quoted back
by the reply UI. Counting those is how a bot answers the echo of its own voice.
So quotes and code are removed before the handle is looked for.

**The comment body is untrusted, and so is the author's display name.** Both
arrive from whoever can comment on the repository. The body becomes prompt text
that the engine already taints and gates; the *login* is what any authority
check must use, because a display name is chosen by its owner and
``Summons.actor`` is documented as untrusted for exactly this reason.

**Nothing here opens a socket.** Posting is an injected callable. That keeps the
adapter testable offline, which the repository requires, and it keeps the
decision of *whether* to post — the effect ledger's business — out of the code
that merely knows the wire format.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from ronin.retainer.adapters.common import strip_quotes_and_code
from ronin.retainer.model import Channel, Summons, SummonsKind

#: The events worth waking a Retainer for. Everything else GitHub sends —
#: pushes, stars, workflow runs — is deliberately not here: a Retainer that
#: reacts to everything is a Retainer nobody can predict.
WAKING_EVENTS: Final[Mapping[str, tuple[str, ...]]] = {
    "issue_comment": ("created", "edited"),
    "pull_request_review_comment": ("created", "edited"),
    "issues": ("opened",),
    "pull_request": ("opened",),
}

#: Marks a bot account. GitHub appends it to every App's login.
BOT_SUFFIX: Final = "[bot]"


@dataclass(frozen=True, slots=True)
class Mention:
    """One delivery, understood: who said what, where, and to whom."""

    repo: str
    number: int
    actor: str
    """The author's **login** — the verified handle, not the display name."""
    text: str
    """What was said, with the handle removed. Untrusted content."""
    event: str
    action: str
    node: str = ""
    """The comment or issue id, for the idempotency key on any reply."""

    @property
    def thread(self) -> str:
        """The conversation key: ``owner/repo#number``, stable across events."""
        return f"{self.repo}#{self.number}"


def mentions(text: str, handle: str) -> bool:
    """Whether ``handle`` was addressed in prose rather than quoted or quoted-in-code.

    The boundary check is deliberate: ``@ronin`` must not match inside
    ``@ronindev`` or an email-shaped string, and a Retainer answering a mention
    of somebody else's name is worse than one that misses its own.
    """
    if not handle:
        return False
    speech = strip_quotes_and_code(text)
    pattern = rf"(?<![A-Za-z0-9_@/-])@{re.escape(handle)}(?![A-Za-z0-9_-])"
    return re.search(pattern, speech, re.IGNORECASE) is not None


def without_handle(text: str, handle: str) -> str:
    """The message with the address removed, so the prompt is the request itself."""
    pattern = rf"(?<![A-Za-z0-9_@/-])@{re.escape(handle)}(?![A-Za-z0-9_-])"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def _text_of(body: Mapping[str, Any], event: str) -> str:
    if event in {"issue_comment", "pull_request_review_comment"}:
        comment = body.get("comment")
        return str(comment.get("body", "")) if isinstance(comment, Mapping) else ""
    subject = body.get("issue") if event == "issues" else body.get("pull_request")
    if not isinstance(subject, Mapping):
        return ""
    return f"{subject.get('title', '')}\n\n{subject.get('body') or ''}".strip()


def _author_of(body: Mapping[str, Any], event: str) -> str:
    if event in {"issue_comment", "pull_request_review_comment"}:
        holder = body.get("comment")
    else:
        holder = body.get("issue") if event == "issues" else body.get("pull_request")
    if isinstance(holder, Mapping):
        user = holder.get("user")
        if isinstance(user, Mapping):
            return str(user.get("login", ""))
    sender = body.get("sender")
    return str(sender.get("login", "")) if isinstance(sender, Mapping) else ""


def _number_of(body: Mapping[str, Any], event: str) -> int:
    for key in ("issue", "pull_request"):
        subject = body.get(key)
        if isinstance(subject, Mapping) and isinstance(subject.get("number"), int):
            return int(subject["number"])
    number = body.get("number")
    return int(number) if isinstance(number, int) else 0


def _node_of(body: Mapping[str, Any], event: str) -> str:
    holder = body.get("comment") if "comment" in event else None
    if isinstance(holder, Mapping) and holder.get("id") is not None:
        return f"comment-{holder['id']}"
    return f"{event}-{_number_of(body, event)}"


def read_mention(
    body: Mapping[str, Any],
    *,
    event: str,
    handle: str,
    ourselves: str = "",
) -> Mention | None:
    """Understand one delivery, or ``None`` if it is not for us.

    ``None`` covers every uninteresting case with one answer, because a caller
    that has to distinguish "wrong event", "no mention" and "our own comment" is
    a caller that will forget one of them. The loop-prevention check is first:
    see the module docstring.
    """
    actions = WAKING_EVENTS.get(event)
    if actions is None or str(body.get("action", "")) not in actions:
        return None

    author = _author_of(body, event)
    if not author:
        return None
    if ourselves and author.lower() == ourselves.lower():
        return None
    if author.endswith(BOT_SUFFIX) and not ourselves:
        # With no identity configured we cannot tell our own comments from any
        # other App's, so no bot gets to wake a Retainer. Silence beats a loop.
        return None

    repository = body.get("repository")
    repo = str(repository.get("full_name", "")) if isinstance(repository, Mapping) else ""
    number = _number_of(body, event)
    if not repo or number <= 0:
        return None

    text = _text_of(body, event)
    if not mentions(text, handle):
        return None

    return Mention(
        repo=repo,
        number=number,
        actor=author,
        text=without_handle(text, handle),
        event=event,
        action=str(body.get("action", "")),
        node=_node_of(body, event),
    )


def to_summons(mention: Mention, retainer: str) -> Summons:
    """The normalised request. From here nothing downstream knows it was GitHub."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.MENTION,
        channel=Channel.GITHUB,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
    )


def escalation_summons(mention: Mention, retainer: str, escalation: str) -> Summons:
    """A reply that answers an escalation, rather than a fresh request."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.REPLY,
        channel=Channel.GITHUB,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
        escalation=escalation,
    )


#: How an escalation is asked for in a thread. The id is included because the
#: answer has to name it, and the wording is plain because whoever reads it may
#: not know what a Retainer is.
#:
#: The instructions are wrapped in inline code deliberately, and it is not
#: cosmetic: :func:`mentions` and :func:`read_answer` both strip code before
#: looking, so this comment can neither wake a Retainer nor answer its own
#: escalation — no matter who posts it or how faithfully they copy it. That is a
#: second layer under the author check, and either one alone would do.
ESCALATION_TEMPLATE: Final = """**{retainer} needs an approval to continue.**

It wants to run `{tool}`:

```
{rendered}
```

Reply with `@{handle} allow {escalation}` or `@{handle} deny {escalation}`.

Anyone with write access to this repository can answer. Approving permits the
action above and nothing else — it does not undo anything already done."""


def ask_body(*, retainer: str, handle: str, tool: str, rendered: str, escalation: str) -> str:
    """The comment that puts an escalation to the thread."""
    return ESCALATION_TEMPLATE.format(
        retainer=retainer,
        handle=handle,
        tool=tool,
        rendered=rendered.strip(),
        escalation=escalation,
    )


__all__ = [
    "BOT_SUFFIX",
    "ESCALATION_TEMPLATE",
    "WAKING_EVENTS",
    "Mention",
    "ask_body",
    "escalation_summons",
    "mentions",
    "read_mention",
    "to_summons",
    "without_handle",
]
