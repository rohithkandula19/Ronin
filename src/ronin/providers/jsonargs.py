"""Turn whatever a model called "JSON arguments" into a dict, or say why not.

Every repair here exists because a real model shape needs it, and every repair
is *recorded* rather than applied silently: a tool that ran on guessed arguments
is worse than a tool that refused, so the caller always gets the list of what we
had to fix and can decide to reject.

Two rules keep this from becoming a guessing machine:

1. **Deterministic.** Same input, same output, same notes. The golden fixture
   tests depend on it, and so does any hope of reproducing a bug report.
2. **Ordered, cheapest first.** Each pass is attempted only if the previous
   parse failed, so well-formed JSON (the overwhelming majority) takes the fast
   path and records no repairs at all.

What is deliberately *not* here: inventing missing required fields, coercing
types to match a schema, or dropping keys we do not recognize. Those are schema
concerns and belong to the tool layer, which owns the schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Markdown fences a model wraps a payload in. Non-greedy body, any info string.
_FENCE_RE = re.compile(r"^\s*```[^\n`]*\n?(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ArgsParse:
    """The outcome of parsing one tool call's arguments.

    ``ok=False`` still carries ``value={}`` rather than ``None`` so callers never
    have to null-check; they check ``ok``. ``raw`` is preserved verbatim because
    the repair message we send back to the model must quote what it actually
    wrote, not our cleaned-up version of it.
    """

    ok: bool
    value: Mapping[str, Any] = field(default_factory=dict)
    raw: str = ""
    repairs: tuple[str, ...] = ()
    error: str = ""

    def __post_init__(self) -> None:
        if self.ok and self.error:
            raise ValueError("ArgsParse cannot be ok=True and carry an error")
        if not self.ok and not self.error:
            raise ValueError("a failed ArgsParse must say why")
        if not isinstance(self.repairs, tuple):
            raise TypeError("ArgsParse.repairs must be a tuple (frozen)")


# --------------------------------------------------------------------------- #
# Individual repairs
# --------------------------------------------------------------------------- #


def strip_fence(text: str) -> str:
    """Unwrap one ```…``` fence. Returns ``text`` unchanged when there is none."""
    match = _FENCE_RE.match(text)
    return match.group("body") if match else text


def _scan(text: str) -> tuple[list[str], bool, bool]:
    """Walk ``text`` once, tracking JSON structure outside of string literals.

    Returns the stack of unclosed openers, whether we ended inside a string, and
    whether we ended on a dangling escape. One scan feeds both the trailing-comma
    and truncation repairs, so neither has to re-implement string awareness —
    which is the bug both of them classically have.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()
    return stack, in_string, escaped


def remove_trailing_commas(text: str) -> str:
    """Drop ``,`` that immediately precedes ``}`` or ``]``, outside strings.

    A regex over the whole payload would corrupt a string value that legitimately
    contains ``", }"``, so this walks the text with the same string awareness as
    :func:`_scan`.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    # Index in `out` of the last comma we emitted outside a string, or -1.
    pending_comma = -1
    for char in text:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            pending_comma = -1
        elif char == ",":
            pending_comma = len(out)
        elif char in "}]":
            if pending_comma >= 0:
                del out[pending_comma:]
                pending_comma = -1
        elif not char.isspace():
            pending_comma = -1
        out.append(char)
    return "".join(out)


def close_truncated(text: str) -> str:
    """Best-effort close of a payload that was cut off mid-stream.

    A streamed tool call whose connection dropped leaves something like
    ``{"path": "src/main.py", "content": "def f(``. Closing the open string and
    the open braces recovers the arguments that *did* arrive, which is strictly
    better than discarding the call — the model asked for something real.

    Truncation is never silent: the caller records a ``truncated`` repair, and a
    partial ``content`` value is the tool's problem to validate, not ours.
    """
    stack, in_string, escaped = _scan(text)
    patched = text
    if escaped:
        # A dangling backslash would escape the quote we are about to add.
        patched = patched[:-1]
    if in_string:
        patched += '"'
    # A key with no value at all (``{"a": "b", "c"``) cannot be closed into valid
    # JSON by adding brackets; drop the dangling fragment first.
    patched = _drop_dangling_key(patched)
    for opener in reversed(stack):
        patched += "}" if opener == "{" else "]"
    return patched


def _drop_dangling_key(text: str) -> str:
    """Remove a trailing ``"key"`` or ``"key":`` that never got a value.

    The distinction that matters — and the one this originally got wrong — is
    *key versus value*. Both look like "a string at the end of the payload":

    * ``{"path": "a.py", "conte`` → the tail is a **key**. Closing the brace around
      it gives ``{"path": "a.py", "conte"}``, which is invalid. Drop it.
    * ``{"path": "a.py", "content": "def f(`` → the tail is a **value**. Dropping it
      would throw away the argument the model was actually sending. Keep it.

    What separates them is the character before the string opens: ``:`` means value,
    ``,`` or an opening brace means key.
    """
    stripped = text.rstrip()
    if stripped.endswith(":"):
        # `{"a": 1, "b":` — a key with a colon and nothing after it.
        stripped = stripped[:-1].rstrip()
    elif not stripped.endswith('"'):
        return text

    start = _string_start(stripped)
    if start is None:
        return text
    head = stripped[:start].rstrip()
    if head.endswith(":"):
        # The trailing string is a value. Leave the payload alone.
        return text
    if head.endswith(","):
        head = head[:-1]
    if head.endswith(("{", "[")):
        return head
    return head if head else text


def _string_start(text: str) -> int | None:
    """Index of the opening quote of the string literal that ends ``text``."""
    if not text.endswith('"'):
        return None
    index = len(text) - 2
    while index >= 0:
        if text[index] == '"':
            backslashes = 0
            probe = index - 1
            while probe >= 0 and text[probe] == "\\":
                backslashes += 1
                probe -= 1
            if backslashes % 2 == 0:
                return index
        index -= 1
    return None


def python_literals_to_json(text: str) -> str:
    """Rewrite ``None``/``True``/``False`` to JSON, outside string literals.

    Models fine-tuned on Python emit Python. This is a token-level rewrite rather
    than a replace, so a string value containing the word "None" survives.
    """
    mapping = {"None": "null", "True": "true", "False": "false"}
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        for literal, replacement in mapping.items():
            if text.startswith(literal, index) and not _is_word_char(text, index - 1):
                after = index + len(literal)
                if not _is_word_char(text, after):
                    out.append(replacement)
                    index = after
                    break
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _is_word_char(text: str, index: int) -> bool:
    if index < 0 or index >= len(text):
        return False
    return text[index].isalnum() or text[index] == "_"


def single_to_double_quotes(text: str) -> str:
    """Convert ``'`` string delimiters to ``"``, only when it is unambiguous.

    Refuses (returns the input unchanged) if the payload already contains a
    double quote, because then we cannot tell a delimiter from an apostrophe
    inside a value — and a wrong guess here silently changes the argument.
    """
    if '"' in text or "'" not in text:
        return text
    return text.replace("'", '"')


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #


def _loads(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except (ValueError, TypeError):
        return False, None


def _coerce_object(parsed: Any, repairs: list[str]) -> Mapping[str, Any] | None:
    """Reduce a parsed value to an arguments *object*, or None if impossible."""
    if isinstance(parsed, Mapping):
        return parsed
    if isinstance(parsed, str):
        # Double-encoded: the provider JSON-escaped an already-JSON string.
        ok, inner = _loads(parsed)
        if ok:
            repairs.append("double_encoded")
            return _coerce_object(inner, repairs)
        return None
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], Mapping):
        repairs.append("unwrapped_single_element_list")
        return parsed[0]
    return None


def parse_arguments(raw: object) -> ArgsParse:
    """Parse tool-call arguments from whatever the provider handed us.

    Accepts a mapping (pass-through), ``None``/empty (an argument-less call, which
    is legitimate), or a string in any of the shapes weak models produce.
    """
    if raw is None:
        return ArgsParse(ok=True, value={}, raw="")
    if isinstance(raw, Mapping):
        return ArgsParse(ok=True, value=dict(raw), raw="")
    if not isinstance(raw, str):
        return ArgsParse(
            ok=False,
            raw=repr(raw),
            error=f"arguments must be an object or a JSON string, got {type(raw).__name__}",
        )

    original = raw
    if not raw.strip():
        return ArgsParse(ok=True, value={}, raw=original)

    repairs: list[str] = []
    candidate = raw

    # Pass 0 — the common case: it is already valid JSON.
    ok, parsed = _loads(candidate)

    # Pass 1..n — one repair at a time, cheapest first, re-parsing after each.
    passes: tuple[tuple[str, Callable[[str], str]], ...] = (
        ("stripped_fence", strip_fence),
        ("removed_trailing_comma", remove_trailing_commas),
        ("python_literals", python_literals_to_json),
        ("single_quotes", single_to_double_quotes),
        ("truncated", close_truncated),
    )
    if not ok:
        for note, transform in passes:
            rewritten = transform(candidate)
            if rewritten == candidate:
                continue
            candidate = rewritten
            repairs.append(note)
            ok, parsed = _loads(candidate)
            if ok:
                break

    if not ok:
        return ArgsParse(
            ok=False,
            raw=original,
            repairs=tuple(repairs),
            error="arguments are not valid JSON, and no repair recovered them",
        )

    value = _coerce_object(parsed, repairs)
    if value is None:
        return ArgsParse(
            ok=False,
            raw=original,
            repairs=tuple(repairs),
            error=(
                f"arguments parsed to {type(parsed).__name__}, not an object — "
                "a tool call's arguments must be a JSON object"
            ),
        )
    return ArgsParse(ok=True, value=dict(value), raw=original, repairs=tuple(repairs))


# --------------------------------------------------------------------------- #
# Finding objects inside prose
# --------------------------------------------------------------------------- #


def find_json_objects(text: str) -> tuple[str, ...]:
    """Every balanced top-level ``{…}`` substring, in order.

    This is how "a model emitted multiple calls in one text block" gets handled:
    the shim hands the block here and gets back one candidate per object, instead
    of a regex matching from the first ``{`` to the last ``}`` and producing one
    unparseable blob.
    """
    found: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                found.append(text[start : index + 1])
                start = -1
    return tuple(found)
