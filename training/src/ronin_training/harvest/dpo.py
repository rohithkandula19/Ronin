"""Negatives -> preference pairs, with the origin of the *preferred* side recorded.

A pair is ``(prompt, rejected, chosen)``: the prompt is the run's state just before
the failure, ``rejected`` is the turn the model actually took, and ``chosen`` is the
turn it should have taken.

Where ``chosen`` comes from is the whole integrity question, so it is a field:

* **donor** — a real turn from a *passing* trajectory of the same task, in the same
  situation. Preferred whenever one exists.
* **synthesized** — constructed mechanically from the rule that was violated, and
  flagged ``synthesized_chosen: true`` so a later analysis can down-weight or
  exclude it. Never blended with donor pairs, never left unmarked.

Some negatives have no honest correction at all. A tool call missing a required key
cannot be repaired without inventing the value, so with no donor available the pair
is **not emitted** and is counted as unconstructible. A fabricated argument would be
the worst row in the dataset: syntactically perfect and factually invented.

Row format is trl's conversational DPO layout (``prompt``/``chosen``/``rejected`` as
message lists) plus the run's ``tools`` block and ``meta``. Conversational rather
than pre-rendered strings for one reason: the template that turns messages into text
belongs to the model's tokenizer, and this repo has already paid for a second
implementation of it once. :func:`to_text_row` produces the string form for a trainer
that needs it, from an injected renderer — the tokenizer's own
``apply_chat_template``, never a copy of it.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .negatives import Negative, NegativeClass, ToolRoles
from .prompt import (
    ASSISTANT_ROLE,
    PromptPayload,
    assistant_message,
    assistant_target_text,
    render_prefix,
)
from .provenance import NO_STEP, Provenance
from .trajectory import RecordedCall, Step, Trajectory

SCHEMA_PATH = Path(__file__).with_name("dpo_pair.schema.json")

#: Synthesized corrections are built from these templates. They quote only what the
#: transcript recorded — the gate's own reason, the repeated action, the unread path —
#: so a synthesized turn states no fact the run did not already contain.
DENIAL_CORRECTION = (
    "That action was refused: {reason} I will not reissue it. "
    "Tell me how you would like to proceed, or I can work on the part of the task "
    "that does not need it."
)
LOOP_CORRECTION = (
    "I have already tried {action} without progress, so repeating it will not help. "
    "I am stuck on that step; I will change approach rather than try it again."
)


@dataclass(frozen=True, slots=True)
class ChosenTurn:
    """The preferred side, with where it came from attached rather than assumed."""

    message: Mapping[str, Any]
    synthesized: bool
    donor_run_id: str = ""
    donor_turn_index: int = NO_STEP

    def __post_init__(self) -> None:
        if self.synthesized and self.donor_run_id:
            raise ValueError(
                "a synthesized chosen turn cannot also name a donor run — that is "
                "exactly the silent mixing the flag exists to prevent"
            )
        if not self.synthesized and not self.donor_run_id:
            raise ValueError(
                "a non-synthesized chosen turn must name the passing run it came "
                "from, or it is unauditable"
            )


@dataclass(frozen=True, slots=True)
class PreferencePair:
    """One DPO row. ``rule`` says what the chosen side is correcting."""

    prompt: PromptPayload
    rejected: Mapping[str, Any]
    chosen: ChosenTurn
    kind: NegativeClass
    rule: str
    provenance: Provenance
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        meta = self.provenance.as_dict()
        meta["rule"] = self.rule
        if self.detail:
            meta["detail"] = self.detail
        if self.chosen.donor_run_id:
            meta["donor_run_id"] = self.chosen.donor_run_id
            meta["donor_turn_index"] = self.chosen.donor_turn_index
        row: dict[str, Any] = {
            "prompt": [dict(m) for m in self.prompt.messages],
            "chosen": [dict(self.chosen.message)],
            "rejected": [dict(self.rejected)],
            "meta": meta,
        }
        if self.prompt.tools:
            row["tools"] = [dict(t) for t in self.prompt.tools]
        return row


@dataclass(frozen=True, slots=True)
class PairCounts:
    """Per-class pair yield, including the ones that could not be built honestly."""

    mined: Mapping[str, int]
    from_donor: Mapping[str, int]
    synthesized: Mapping[str, int]
    unconstructible: Mapping[str, int]

    def total(self) -> int:
        return sum(self.from_donor.values()) + sum(self.synthesized.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "mined": dict(self.mined),
            "from_donor": dict(self.from_donor),
            "synthesized": dict(self.synthesized),
            "unconstructible": dict(self.unconstructible),
            "pairs": self.total(),
        }

    def summary(self) -> str:
        lines = [f"{sum(self.mined.values())} negative(s) -> {self.total()} pair(s)"]
        for kind in NegativeClass:
            key = str(kind)
            lines.append(
                f"  {key}: mined {self.mined.get(key, 0)}, "
                f"donor {self.from_donor.get(key, 0)}, "
                f"synthesized {self.synthesized.get(key, 0)}, "
                f"unconstructible {self.unconstructible.get(key, 0)}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Finding a real corrected turn
# --------------------------------------------------------------------------- #


def _donor_key(negative: Negative) -> tuple[str, str]:
    """What a donor turn has to be, per class. ``("", "")`` means no key exists."""
    if negative.kind is NegativeClass.EDIT_WITHOUT_READ:
        return ("reads", str(negative.evidence.get("path", "")))
    if negative.kind is NegativeClass.MALFORMED_TOOL_JSON:
        call = negative.step.call_by_id(negative.call_id) if negative.call_id else None
        return ("valid_call", call.name if call is not None else "")
    if negative.kind is NegativeClass.IGNORED_DENIAL:
        return ("not_fingerprint", str(negative.evidence.get("fingerprint", "")))
    if negative.kind is NegativeClass.LOOPING:
        return ("not_fingerprint", str(negative.evidence.get("fingerprint", "")))
    raise AssertionError(f"unhandled negative class {negative.kind!r}")


def _step_matches(step: Step, key: tuple[str, str], roles: ToolRoles) -> bool:
    kind, value = key
    if not value:
        return False
    if kind == "reads":
        return any(
            roles.is_reader(call.name) and value in call.arguments.values()
            for call in step.calls
        )
    if kind == "valid_call":
        return any(call.name == value for call in step.calls)
    if kind == "not_fingerprint":
        return bool(step.calls) and all(
            call.fingerprint() != value for call in step.calls
        )
    return False


def find_donor(
    negative: Negative,
    donors: Sequence[Trajectory],
    *,
    roles: ToolRoles,
) -> ChosenTurn | None:
    """The earliest matching turn from a passing run of the same task, or ``None``.

    Restricted to the same ``task_id``: a correct turn from a different task is a
    correct turn for a different situation, and pairing across tasks would teach the
    model to answer the question it was not asked.
    """
    key = _donor_key(negative)
    if not key[1]:
        return None
    for donor in donors:
        if donor.task_id != negative.trajectory.task_id or not donor.verified:
            continue
        for index, step in enumerate(donor.steps):
            if _step_matches(step, key, roles):
                return ChosenTurn(
                    message=assistant_message(step),
                    synthesized=False,
                    donor_run_id=donor.run_id,
                    donor_turn_index=index,
                )
    return None


# --------------------------------------------------------------------------- #
# Constructing one mechanically, from the rule that was violated
# --------------------------------------------------------------------------- #


def synthesize(negative: Negative, *, roles: ToolRoles) -> ChosenTurn | None:
    """The correction the violated rule dictates, or ``None`` if none is honest.

    ``None`` is a real outcome, not a failure: a call missing a required key has no
    mechanical repair, and inventing the missing value would put a fabricated fact
    into the dataset dressed as a demonstration.
    """
    if negative.kind is NegativeClass.EDIT_WITHOUT_READ:
        path = str(negative.evidence.get("path", ""))
        reader = _reader_name(negative.trajectory, roles)
        if not path or reader is None:
            return None
        # The rule is "read before you write", so the corrected turn is the read.
        read_call = RecordedCall(
            id=f"{negative.call_id or 'call'}_read", name=reader, arguments={"path": path}
        )
        return ChosenTurn(
            message=assistant_message(Step(index=0, text="", calls=(read_call,))),
            synthesized=True,
        )
    if negative.kind is NegativeClass.IGNORED_DENIAL:
        reason = str(negative.evidence.get("denial_reason", "")).strip()
        if not reason:
            return None
        text = DENIAL_CORRECTION.format(reason=_as_clause(reason))
        return ChosenTurn(
            message={"role": ASSISTANT_ROLE, "content": text}, synthesized=True
        )
    if negative.kind is NegativeClass.LOOPING:
        action = str(negative.evidence.get("fingerprint", "")).split(":", 1)[0]
        if not action:
            return None
        return ChosenTurn(
            message={
                "role": ASSISTANT_ROLE,
                "content": LOOP_CORRECTION.format(action=action),
            },
            synthesized=True,
        )
    if negative.kind is NegativeClass.MALFORMED_TOOL_JSON:
        return _synthesize_malformed(negative)
    raise AssertionError(f"unhandled negative class {negative.kind!r}")


def _synthesize_malformed(negative: Negative) -> ChosenTurn | None:
    """Repair a call only when the schema makes the repair unambiguous.

    Exactly one case qualifies: a value of the wrong primitive type whose string
    form converts losslessly to the declared type (``"40"`` where the schema says
    ``integer``). Everything else — a missing required key, an unparseable block —
    has no correct answer derivable from the rule, so no pair is produced.
    """
    if negative.evidence.get("defect") != "schema" or not negative.call_id:
        return None
    call = negative.step.call_by_id(negative.call_id)
    tool = negative.trajectory.tool_named(call.name) if call is not None else None
    if call is None or tool is None:
        return None
    properties = tool.parameters.get("properties")
    if not isinstance(properties, dict):
        return None
    required = tool.parameters.get("required")
    required_keys = set(required) if isinstance(required, list) else set()
    if required_keys - set(call.arguments):
        return None  # missing key: no honest value exists
    repaired: dict[str, Any] = {}
    for key, value in call.arguments.items():
        declared = properties.get(key)
        want = declared.get("type") if isinstance(declared, dict) else None
        coerced = _coerce(value, want)
        if coerced is None:
            return None
        repaired[key] = coerced
    fixed = RecordedCall(id=call.id, name=call.name, arguments=repaired)
    return ChosenTurn(
        message=assistant_message(Step(index=0, text="", calls=(fixed,))),
        synthesized=True,
    )


def _coerce(value: Any, want: Any) -> Any:
    """``value`` as the declared JSON type, or ``None`` if the change would guess."""
    if want is None or not isinstance(want, str):
        return value
    if want == "string":
        return value if isinstance(value, str) else None
    if want == "integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        return None
    if want == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        return None
    return value


def _reader_name(trajectory: Trajectory, roles: ToolRoles) -> str | None:
    """The run's own reader tool. Never a hardcoded name — v1 and v2 differ."""
    for tool in trajectory.tools:
        if roles.is_reader(tool.name):
            return tool.name
    return None


def _as_clause(reason: str) -> str:
    return reason if reason.endswith((".", "!", "?")) else f"{reason}."


# --------------------------------------------------------------------------- #
# Building the corpus
# --------------------------------------------------------------------------- #


def build_pairs(
    negatives: Sequence[Negative],
    *,
    donors: Sequence[Trajectory] = (),
    roles: ToolRoles,
    allow_synthesized: bool = True,
) -> tuple[tuple[PreferencePair, ...], PairCounts]:
    """Turn mined negatives into pairs, preferring real corrected turns.

    ``allow_synthesized=False`` emits donor-backed pairs only, which is the honest
    way to answer "how much of this dataset is real?" — run it both ways and compare.
    """
    mined: dict[str, int] = {str(k): 0 for k in NegativeClass}
    donor_counts = dict.fromkeys(mined, 0)
    synth_counts = dict.fromkeys(mined, 0)
    unconstructible = dict.fromkeys(mined, 0)
    pairs: list[PreferencePair] = []

    for negative in negatives:
        key = str(negative.kind)
        mined[key] += 1
        chosen = find_donor(negative, donors, roles=roles)
        if chosen is None and allow_synthesized:
            chosen = synthesize(negative, roles=roles)
        if chosen is None:
            unconstructible[key] += 1
            continue
        rejected = assistant_message(negative.step)
        if chosen.synthesized:
            synth_counts[key] += 1
        else:
            donor_counts[key] += 1
        pairs.append(
            PreferencePair(
                prompt=render_prefix(negative.trajectory, negative.step_index),
                rejected=rejected,
                chosen=chosen,
                kind=negative.kind,
                rule=negative.rule,
                detail=negative.detail,
                provenance=Provenance(
                    task_id=negative.trajectory.task_id,
                    run_id=negative.trajectory.run_id,
                    model=negative.trajectory.model,
                    source=negative.trajectory.source,
                    turn_index=negative.step_index,
                    negative_class=key,
                    synthesized_chosen=chosen.synthesized,
                ),
            )
        )
    counts = PairCounts(
        mined=mined,
        from_donor=donor_counts,
        synthesized=synth_counts,
        unconstructible=unconstructible,
    )
    return tuple(pairs), counts


# --------------------------------------------------------------------------- #
# Schema validation and output
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def load_schema() -> Mapping[str, Any]:
    """The DPO row schema that ships inside this package."""
    loaded: Any = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):  # pragma: no cover - the file is a checked-in object
        raise TypeError(f"{SCHEMA_PATH} does not contain a JSON object")
    return loaded


def validate_row(row: Mapping[str, Any]) -> list[str]:
    """Every reason ``row`` is not a usable DPO row, or ``[]`` if it is."""
    # No stubs for jsonschema, and types-jsonschema is not a dev dependency here.
    import jsonschema  # type: ignore[import-untyped]

    validator = jsonschema.Draft7Validator(dict(load_schema()))
    errors = [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<row>'}: {err.message}"
        for err in validator.iter_errors(dict(row))
    ]
    meta = row.get("meta")
    if (
        isinstance(meta, Mapping)
        and meta.get("synthesized_chosen") is True
        and meta.get("donor_run_id")
    ):
        errors.append(
            "meta: synthesized_chosen is true but a donor_run_id is named — the "
            "row claims both origins for its chosen side"
        )
    return errors


def write_jsonl(path: Path, pairs: Sequence[PreferencePair], *, validate: bool = True) -> int:
    """Write pairs as JSONL. Every row is validated before anything is written.

    Validation happens up front and refuses the whole file, rather than skipping bad
    rows: a partially written dataset whose row count nobody checked is how an
    invalid example reaches a training run.
    """
    rows = [pair.as_row() for pair in pairs]
    if validate:
        problems = [
            f"row {index}: {'; '.join(errors)}"
            for index, row in enumerate(rows)
            if (errors := validate_row(row))
        ]
        if problems:
            raise ValueError(
                "refusing to write an invalid DPO dataset:\n" + "\n".join(problems)
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


#: ``(messages, tools) -> prompt text``. The tokenizer's own ``apply_chat_template``,
#: injected. Nothing in this package implements one.
ChatRenderer = Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], str]


def to_text_row(pair: PreferencePair, render: ChatRenderer) -> dict[str, str]:
    """The string-format DPO row (``prompt``/``chosen``/``rejected`` as text).

    For a trainer that wants pre-rendered text. The prompt comes from the injected
    renderer; the two completions come from the canonical ``ronin_dialect`` wire
    format, so the chosen side is byte-identical to what the runtime would parse.
    """
    return {
        "prompt": render(list(pair.prompt.messages), list(pair.prompt.tools)),
        "chosen": _completion_text(pair.chosen.message),
        "rejected": _completion_text(pair.rejected),
    }


def _completion_text(message: Mapping[str, Any]) -> str:
    calls = message.get("tool_calls") or []
    step = Step(
        index=0,
        text=str(message.get("content") or ""),
        calls=tuple(
            RecordedCall(
                id=str(c["id"]),
                name=str(c["function"]["name"]),
                arguments=dict(c["function"]["arguments"]),
            )
            for c in calls
        ),
    )
    return assistant_target_text(step)


__all__ = [
    "SCHEMA_PATH",
    "ChatRenderer",
    "ChosenTurn",
    "PairCounts",
    "PreferencePair",
    "build_pairs",
    "find_donor",
    "load_schema",
    "synthesize",
    "to_text_row",
    "validate_row",
    "write_jsonl",
]
