"""Preference-pair builder — where DPO fixes the *behaviours* SFT can't teach.

SFT teaches the format; DPO teaches the difference between two format-correct turns, one
better than the other. Small models improve most here, on the behavioural failures that a
verifier catches but imitation does not fix: looping, giving up, ignoring a denial, editing
blind. Each pair is ``(prompt, rejected, chosen)`` where **the prompt is identical and only
the assistant turn differs** — that shared prefix is the whole point, because DPO's loss is
a comparison *at the same state*, and a pair whose prefixes differ trains on the prefix
difference instead of the behaviour. :class:`Pair` enforces that at construction: one shared
prompt, one rejected assistant turn, one chosen assistant turn, and they must differ.

Seven failure classes, the ones the spec names, each with what "chosen" should look like:

======================= ============================================================
``MALFORMED_JSON``      a call that won't parse  →  the same call, valid JSON object
``REPEATED_ACTION``     a 3rd identical grep     →  a different strategy (read the file)
``BLIND_EDIT``          edit without reading     →  read, then edit
``IGNORED_DENIAL``      re-issue a denied call   →  acknowledge the no, offer an alternative
``PREMATURE_DONE``      "done" while red         →  run the tests, then continue
``TEST_DELETION``       delete/weaken a test     →  fix the code instead
``HALLUCINATED_API``    call a tool that isn't   →  grep for the real one first
======================= ============================================================

``chosen`` comes from a **donor** passing run where one exists (``synthesized=False``);
otherwise it is **synthesized** by a per-class rule and flagged (``synthesized=True``), so a
later analysis can down-weight the constructed half — the same honesty
:mod:`ronin_training.harvest.dpo` keeps. The rows are trl's conversational DPO layout
(``{prompt, chosen, rejected}``), byte-compatible with what the harvest emits, so both feed
one trainer.

:func:`balance` caps every class to the same count before training: a set that is 60%
malformed-json teaches mostly JSON and little else, and the class the model most needs is
rarely the most common in the wild.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ASSISTANT = "assistant"


class PreferenceClass(StrEnum):
    """The seven behavioural failures a pair can teach against."""

    MALFORMED_JSON = "malformed_json"
    REPEATED_ACTION = "repeated_action"
    BLIND_EDIT = "blind_edit"
    IGNORED_DENIAL = "ignored_denial"
    PREMATURE_DONE = "premature_done"
    TEST_DELETION = "test_deletion"
    HALLUCINATED_API = "hallucinated_api"


class PairError(ValueError):
    """A pair that would train on the wrong thing, refused with the reason."""


def _is_assistant(message: Mapping[str, Any]) -> bool:
    return str(message.get("role", "")) == ASSISTANT


@dataclass(frozen=True, slots=True)
class Pair:
    """One DPO row: a shared prompt, a rejected turn, a better chosen turn.

    Prefix-identity is enforced here, not hoped for: ``prompt`` is the state both turns
    answer, ``rejected``/``chosen`` are each exactly one assistant message, and they must
    differ. ``synthesized`` marks a chosen turn constructed by rule rather than taken from a
    passing run.
    """

    prompt: tuple[Mapping[str, Any], ...]
    rejected: Mapping[str, Any]
    chosen: Mapping[str, Any]
    pref_class: PreferenceClass
    synthesized: bool = False

    def __post_init__(self) -> None:
        if not self.prompt:
            raise PairError("a pair needs a non-empty shared prompt (the state both turns answer)")
        if not _is_assistant(self.rejected) or not _is_assistant(self.chosen):
            raise PairError("rejected and chosen must each be a single assistant turn")
        if self.rejected == self.chosen:
            raise PairError("rejected and chosen are identical — the pair teaches nothing")

    def to_row(self) -> dict[str, Any]:
        """trl's conversational DPO row: prompt shared, chosen/rejected the differing turns."""
        row: dict[str, Any] = {
            "prompt": [dict(m) for m in self.prompt],
            "chosen": [dict(self.chosen)],
            "rejected": [dict(self.rejected)],
            "pref_class": str(self.pref_class),
        }
        if self.synthesized:
            row["synthesized_chosen"] = True
        return row


def _assistant(content: str = "", *, tool: str | None = None, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": ASSISTANT, "content": content}
    if tool is not None:
        message["tool_calls"] = [{"function": {"name": tool, "arguments": dict(arguments or {})}}]
    return message


def correct_turn(pref_class: PreferenceClass, rejected: Mapping[str, Any]) -> dict[str, Any]:
    """Synthesize the chosen turn for a rejected one — the per-class "what good looks like".

    Deterministic and offline: a rule per class, never a model. The result is always a
    *different* assistant turn from the rejected one, so the resulting pair is valid.
    """
    call = (rejected.get("tool_calls") or [{}])[0].get("function", {}) if rejected.get("tool_calls") else {}
    name = str(call.get("name", "")) if isinstance(call, Mapping) else ""
    args = call.get("arguments") if isinstance(call, Mapping) else {}
    path = args.get("path") if isinstance(args, Mapping) else None

    if pref_class is PreferenceClass.MALFORMED_JSON:
        # Same intent, valid JSON *object* arguments (D2).
        fixed = args if isinstance(args, Mapping) else {"raw": str(args)}
        return _assistant("", tool=name or "read", arguments=fixed)
    if pref_class is PreferenceClass.REPEATED_ACTION:
        # Break the loop: read the file instead of grepping a third time.
        return _assistant("that search is not converging; reading the file directly", tool="read", arguments={"path": path or "src/target.py"})
    if pref_class is PreferenceClass.BLIND_EDIT:
        return _assistant("reading before editing", tool="read", arguments={"path": path or "src/target.py"})
    if pref_class is PreferenceClass.IGNORED_DENIAL:
        return _assistant("understood — I won't run that. Here is a safer alternative that gets the same result.")
    if pref_class is PreferenceClass.PREMATURE_DONE:
        return _assistant("not done yet — running the tests to check", tool="bash", arguments={"command": "python -m pytest -q"})
    if pref_class is PreferenceClass.TEST_DELETION:
        return _assistant("the test is correct; fixing the code it exercises instead", tool="read", arguments={"path": path or "src/target.py"})
    # HALLUCINATED_API
    return _assistant("I don't know that tool; grepping for the real API first", tool="grep", arguments={"pattern": "def "})


def pair_from_rejection(
    prompt: Sequence[Mapping[str, Any]],
    rejected: Mapping[str, Any],
    pref_class: PreferenceClass,
    *,
    chosen: Mapping[str, Any] | None = None,
) -> Pair:
    """Build a pair. ``chosen`` from a donor run if given, else synthesized by rule (flagged)."""
    if chosen is not None:
        return Pair(tuple(prompt), rejected, chosen, pref_class, synthesized=False)
    return Pair(tuple(prompt), rejected, correct_turn(pref_class, rejected), pref_class, synthesized=True)


def class_counts(pairs: Sequence[Pair]) -> dict[str, int]:
    """How many pairs of each class, every class present as a key (0 when absent)."""
    counts = {str(c): 0 for c in PreferenceClass}
    for pair in pairs:
        counts[str(pair.pref_class)] += 1
    return counts


def balance(pairs: Sequence[Pair], *, per_class: int | None = None, seed: int = 0) -> list[Pair]:
    """Cap every present class to the same count, so no class dominates the DPO set.

    Without ``per_class`` the cap is the smallest present class's count (the largest set
    that can be perfectly balanced). Deterministic: within a class the kept pairs are chosen
    by a seeded shuffle, so the same input balances the same way across runs.
    """
    by_class: dict[PreferenceClass, list[Pair]] = {}
    for pair in pairs:
        by_class.setdefault(pair.pref_class, []).append(pair)
    if not by_class:
        return []
    cap = per_class if per_class is not None else min(len(v) for v in by_class.values())
    rng = random.Random(seed)
    out: list[Pair] = []
    for pref_class in sorted(by_class, key=str):
        bucket = list(by_class[pref_class])
        rng.shuffle(bucket)
        out.extend(bucket[:cap])
    return out


__all__ = [
    "ASSISTANT",
    "Pair",
    "PairError",
    "PreferenceClass",
    "balance",
    "class_counts",
    "correct_turn",
    "pair_from_rejection",
]
