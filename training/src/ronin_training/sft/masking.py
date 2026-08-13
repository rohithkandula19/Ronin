"""The assistant-only loss mask — the one thing an agent SFT run must get right.

Training on non-assistant tokens is the bug that silently ruins agent fine-tunes, and it
is silent precisely because the loss curve looks normal and the model gets *worse* in a
way only an eval catches:

* train on **tool-result** tokens and the model learns to *predict* what a tool would
  return — it hallucinates file contents instead of calling ``read`` and looking;
* train on the **user** turn and it learns to write the human's half of the conversation;
* train on the **system** prompt and it spends capacity memorising a fixed prefix.

So every token that is not part of an assistant turn gets the label :data:`IGNORE_INDEX`
(``-100``, the value ``torch``'s cross-entropy and every HF collator skip), and only the
assistant tokens carry a real target. This module builds that label array from the
``{messages, tools}`` rows :mod:`ronin_training.harvest` already emits, and the proof that
it is right is a test that reads the labels back and asserts the mask, rather than a
comment promising it.

**Tokeniser-agnostic by construction.** The real run tokenises with the model's own
``apply_chat_template``; that needs the model's weights and is the trainer's job. What is
*testable here*, with no model and no GPU, is the invariant that matters: given the
per-message text and a tokeniser, the assistant messages are learned and nothing else is.
The tokeniser is injected (:data:`Tokenizer`), so a test drives it with a trivial
whitespace tokeniser and the same code runs under the real one. The one rule the trainer
must honour to keep this valid — **no sequence packing** — is a property of the config
(:mod:`ronin_training.sft.profiles`), not something this function can enforce, and it is
named there and asserted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: The label value every loss function skips. Non-assistant tokens carry it, so the model
#: is never trained to reproduce a prompt, a tool result, or the user's words.
IGNORE_INDEX: Final[int] = -100

#: The role whose tokens are the *only* training targets.
ASSISTANT_ROLE: Final[str] = "assistant"

#: A tokeniser: text in, token ids out. The model's own at train time; a fake in a test.
Tokenizer = Callable[[str], Sequence[int]]

#: Renders one chat message to the text a tokeniser sees. The real run uses the model's
#: chat template; this seam lets a test pin the mask without one. Assistant messages with
#: tool calls render their call blocks so those tokens are (correctly) learned.
MessageRenderer = Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class LabeledExample:
    """One training example: token ids and their labels, same length, aligned.

    ``labels[i]`` is ``input_ids[i]`` on assistant tokens and :data:`IGNORE_INDEX`
    everywhere else. ``learn_count`` is how many tokens actually carry loss — a row that
    masks *everything* is a row that teaches nothing, and :func:`build_labels` refuses it
    rather than letting a silent no-op into the dataset.
    """

    input_ids: tuple[int, ...]
    labels: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.input_ids) != len(self.labels):
            raise ValueError("input_ids and labels must be the same length")

    @property
    def learn_count(self) -> int:
        return sum(1 for label in self.labels if label != IGNORE_INDEX)

    @property
    def masked_count(self) -> int:
        return len(self.labels) - self.learn_count


def default_render(message: Mapping[str, Any]) -> str:
    """A minimal, deterministic message rendering for the mask seam.

    Not the model's chat template — that owns the real text and is applied by the trainer.
    This renders enough that a tokeniser produces tokens to mask: the role's content, plus
    a compact form of any ``tool_calls`` on an assistant turn (so those tokens are present
    to be *learned*). Tool-result messages render their content, which must be masked.
    """
    content = str(message.get("content", "") or "")
    calls = message.get("tool_calls") or []
    if calls:
        rendered_calls = " ".join(
            f"{c.get('function', {}).get('name', '')}("
            f"{c.get('function', {}).get('arguments', '')})"
            for c in calls
        )
        content = f"{content} {rendered_calls}".strip()
    return content


def build_labels(
    messages: Sequence[Mapping[str, Any]],
    tokenize: Tokenizer,
    *,
    render: MessageRenderer = default_render,
) -> LabeledExample:
    """Tokenise each message and label assistant tokens; mask everything else to ``-100``.

    The whole example — system, user, assistant, tool results — is tokenised and kept as
    ``input_ids`` (the model needs the context to predict the next assistant turn), but the
    ``labels`` carry a real id only on assistant tokens. Raises when nothing is learnable:
    a fully-masked row is a bug (no assistant turn, or an empty one), not a valid example.
    """
    input_ids: list[int] = []
    labels: list[int] = []
    for message in messages:
        role = str(message.get("role", ""))
        tokens = list(tokenize(render(message)))
        learn = role == ASSISTANT_ROLE
        input_ids.extend(tokens)
        labels.extend(tokens if learn else [IGNORE_INDEX] * len(tokens))
    example = LabeledExample(input_ids=tuple(input_ids), labels=tuple(labels))
    if example.learn_count == 0:
        raise ValueError(
            "every token in this example is masked — no assistant turn is learnable, so "
            "the row teaches nothing and must not enter the dataset"
        )
    return example


def masked_roles(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The roles whose tokens will be masked — everything that is not the assistant.

    A convenience for a test or a report to state, in role terms, what the loss ignores,
    without re-tokenising: ``('system', 'user', 'tool')`` for a normal turn.
    """
    return tuple(
        str(m.get("role", "")) for m in messages if str(m.get("role", "")) != ASSISTANT_ROLE
    )


__all__ = [
    "ASSISTANT_ROLE",
    "IGNORE_INDEX",
    "LabeledExample",
    "MessageRenderer",
    "Tokenizer",
    "build_labels",
    "default_render",
    "masked_roles",
]
