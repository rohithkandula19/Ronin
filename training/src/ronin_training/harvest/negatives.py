"""Mine failures for the four things a small model actually gets wrong.

Not a general failure taxonomy — four named classes, chosen because they are what a
1.5B model fails at when it is otherwise capable:

``malformed_tool_json``
    Unparseable arguments, wrong types, or a missing required key. Detected two
    ways: a ``<tool_call>`` block the canonical parser refuses (so the runtime
    would never have executed it), and a call whose arguments fail its own tool
    schema. Both are checked against real artefacts — the parser the runtime uses
    and the schema the registry declares — never against error prose, because
    matching on wording turns a message edit into a silently empty miner.

``ignored_denial``
    The approval gate refused with feedback and the model reissued the *same*
    action. Sameness is the loop's own fingerprint rule, so "same" here means
    exactly what stall detection means by it.

``looping``
    One action fingerprint repeated within the loop's own stall window. The
    thresholds mirror ``ronin.core.loop`` (3 repeats inside a rolling 6) so the
    corpus teaches against the behaviour the harness actually aborts on.

``edit_without_read``
    A write or edit on a path never read — the exact rule ``ToolContext.read_files``
    guards. A successful write also counts as a read, mirroring the tool's own
    ``mark_read`` after writing, or the second edit of a file the model just wrote
    would be mined as a violation it is not.

Tool *names* are configurable because Ronin has shipped two registries: v1 named
its file reader ``read_file`` and v2 names it ``read``. A miner with either name
hardcoded goes quietly blind on half the corpus.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .prompt import parse_target_text
from .trajectory import RecordedCall, Step, Trajectory

#: Mirrors ``ronin.core.loop.STALL_REPEATS`` / ``STALL_WINDOW``: the same
#: fingerprint this many times inside a rolling window of that many calls.
LOOP_REPEATS = 3
LOOP_WINDOW = 6

#: Argument keys whose value is a path, for the read-before-write rule. Covers both
#: registries' spellings; an unrecognised key means the call is not path-shaped and
#: the rule does not apply, which is the safe direction (no false accusation).
PATH_KEYS: tuple[str, ...] = ("path", "file_path", "filename", "file")


class NegativeClass(StrEnum):
    MALFORMED_TOOL_JSON = "malformed_tool_json"
    IGNORED_DENIAL = "ignored_denial"
    LOOPING = "looping"
    EDIT_WITHOUT_READ = "edit_without_read"


@dataclass(frozen=True, slots=True)
class ToolRoles:
    """Which tool names read and which write, per registry generation."""

    readers: frozenset[str] = frozenset()
    writers: frozenset[str] = frozenset()

    def is_reader(self, name: str) -> bool:
        return name in self.readers

    def is_writer(self, name: str) -> bool:
        return name in self.writers


#: Both shipped registries. v1 (``training/config/tool_registry.json``) and v2
#: (``src/ronin/tools/files.py``) name the same operations differently.
DEFAULT_TOOL_ROLES = ToolRoles(
    readers=frozenset({"read", "read_file"}),
    writers=frozenset({"write", "write_file", "edit", "edit_file", "multi_edit"}),
)


@dataclass(frozen=True, slots=True)
class Negative:
    """One mined failure: where it happened, which rule broke, and why we say so.

    ``rule`` is prose because it becomes the ``rule`` field on a preference pair —
    the record of *what* the corrected side is correcting. A pair whose rule reads
    "violated something" is a pair a reviewer cannot check.
    """

    trajectory: Trajectory
    step_index: int
    kind: NegativeClass
    rule: str
    detail: str = ""
    call_id: str = ""
    #: Extra keys a pair builder needs: the denied fingerprint, the unread path, the
    #: schema error. Kept as data so :mod:`.dpo` does not re-derive any of it.
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def step(self) -> Step:
        return self.trajectory.steps[self.step_index]


def _path_of(call: RecordedCall) -> str:
    for key in PATH_KEYS:
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _schema_errors(call: RecordedCall, trajectory: Trajectory) -> str:
    """The first schema violation in ``call``'s arguments, or "".

    ``jsonschema`` is already a hard dependency of this package, but the import is
    local so a bare-runtime import of the harvester cannot fail on it.
    """
    tool = trajectory.tool_named(call.name)
    if tool is None or not tool.parameters:
        return ""
    # jsonschema is a declared dependency of this package but ships no stubs and
    # types-jsonschema is not in the root dev group; narrowed, not silenced.
    import jsonschema  # type: ignore[import-untyped]

    try:
        jsonschema.validate(dict(call.arguments), dict(tool.parameters))
    except jsonschema.ValidationError as exc:
        return str(exc.message)
    except jsonschema.SchemaError as exc:  # pragma: no cover - a broken fixture schema
        return f"tool {call.name!r} declares an invalid schema: {exc.message}"
    return ""


def _unparsed_blocks(text: str) -> tuple[str, ...]:
    """``<tool_call>`` blocks the canonical parser leaves in the text.

    The parser keeps a malformed block verbatim rather than repairing it, so a
    block still present after parsing is one the runtime would refuse to execute.
    """
    if "<tool_call>" not in text:
        return ()
    parsed = parse_target_text(text)
    from ronin_dialect import BLOCK_RE  # type: ignore[import-untyped]

    blocks: list[str] = [str(m.group(0)) for m in BLOCK_RE.finditer(text)]
    return () if len(parsed) == len(blocks) else tuple(blocks)


def _mine_malformed(trajectory: Trajectory) -> list[Negative]:
    found: list[Negative] = []
    for index, step in enumerate(trajectory.steps):
        for block in _unparsed_blocks(step.text):
            found.append(
                Negative(
                    trajectory=trajectory,
                    step_index=index,
                    kind=NegativeClass.MALFORMED_TOOL_JSON,
                    rule=(
                        "a tool call must be one JSON object inside a <tool_call> "
                        "block, with a string name and object arguments; the runtime "
                        "executes nothing else"
                    ),
                    detail="the canonical parser could not read this block",
                    evidence={"block": block, "defect": "unparseable"},
                )
            )
        for call in step.calls:
            message = _schema_errors(call, trajectory)
            if message:
                found.append(
                    Negative(
                        trajectory=trajectory,
                        step_index=index,
                        kind=NegativeClass.MALFORMED_TOOL_JSON,
                        rule=(
                            f"arguments to {call.name!r} must satisfy its declared "
                            f"schema — every required key present, every value the "
                            f"declared type"
                        ),
                        detail=message,
                        call_id=call.id,
                        evidence={"defect": "schema", "message": message},
                    )
                )
    return found


def _mine_ignored_denial(trajectory: Trajectory) -> list[Negative]:
    """A denial, then the same fingerprint again. The gate's feedback went unread."""
    denied: dict[str, tuple[int, str]] = {}
    found: list[Negative] = []
    for index, step in enumerate(trajectory.steps):
        for call in step.calls:
            mark = call.fingerprint()
            if mark in denied:
                denied_at, reason = denied[mark]
                found.append(
                    Negative(
                        trajectory=trajectory,
                        step_index=index,
                        kind=NegativeClass.IGNORED_DENIAL,
                        rule=(
                            "when approval is refused, adapt to the reason given; "
                            "reissuing the identical action cannot succeed and "
                            "spends the user's attention twice"
                        ),
                        detail=(
                            f"call refused at turn {denied_at} with "
                            f"{reason!r} and repeated verbatim here"
                        ),
                        call_id=call.id,
                        evidence={
                            "fingerprint": mark,
                            "denial_reason": reason,
                            "denied_at": denied_at,
                        },
                    )
                )
        for call in step.calls:
            result = step.result_for(call.id)
            if result is not None and result.denied:
                denied.setdefault(call.fingerprint(), (index, result.denial_reason))
    return found


def _mine_looping(trajectory: Trajectory) -> list[Negative]:
    """The loop's own stall rule, applied to a recorded run."""
    window: deque[str] = deque(maxlen=LOOP_WINDOW)
    found: list[Negative] = []
    reported: set[str] = set()
    for index, step in enumerate(trajectory.steps):
        for call in step.calls:
            mark = call.fingerprint()
            window.append(mark)
            repeats = sum(1 for seen in window if seen == mark)
            if repeats >= LOOP_REPEATS and mark not in reported:
                reported.add(mark)
                found.append(
                    Negative(
                        trajectory=trajectory,
                        step_index=index,
                        kind=NegativeClass.LOOPING,
                        rule=(
                            "an action that produced no progress twice will not "
                            "produce it a third time; say what is blocking and change "
                            "approach"
                        ),
                        detail=(
                            f"fingerprint repeated {repeats} times within the last "
                            f"{len(window)} call(s)"
                        ),
                        call_id=call.id,
                        evidence={"fingerprint": mark, "repeats": repeats},
                    )
                )
    return found


def _mine_edit_without_read(
    trajectory: Trajectory, roles: ToolRoles
) -> list[Negative]:
    read: set[str] = set()
    found: list[Negative] = []
    for index, step in enumerate(trajectory.steps):
        for call in step.calls:
            path = _path_of(call)
            result = step.result_for(call.id)
            succeeded = result is None or result.ok
            if roles.is_writer(call.name) and path and path not in read:
                found.append(
                    Negative(
                        trajectory=trajectory,
                        step_index=index,
                        kind=NegativeClass.EDIT_WITHOUT_READ,
                        rule=(
                            f"read {path} before writing it — an edit written without "
                            f"seeing the file is a guess, and the write guard refuses it"
                        ),
                        detail=f"{call.name} on {path} with no prior read in this run",
                        call_id=call.id,
                        evidence={"path": path, "writer": call.name},
                    )
                )
            if path and succeeded and (roles.is_reader(call.name) or roles.is_writer(call.name)):
                # A successful write marks the file read, exactly as the tool does.
                read.add(path)
    return found


def mine(
    trajectory: Trajectory, *, roles: ToolRoles = DEFAULT_TOOL_ROLES
) -> tuple[Negative, ...]:
    """Every negative in one trajectory, in step order then class order."""
    found = [
        *_mine_malformed(trajectory),
        *_mine_ignored_denial(trajectory),
        *_mine_looping(trajectory),
        *_mine_edit_without_read(trajectory, roles),
    ]
    found.sort(key=lambda n: (n.step_index, str(n.kind), n.call_id))
    return tuple(found)


def mine_all(
    trajectories: Sequence[Trajectory], *, roles: ToolRoles = DEFAULT_TOOL_ROLES
) -> tuple[Negative, ...]:
    """Mine a corpus. Failing runs are the point here — nothing is filtered out."""
    return tuple(n for traj in trajectories for n in mine(traj, roles=roles))


def class_counts(negatives: Sequence[Negative]) -> dict[str, int]:
    """Per-class totals, with every class present so a zero is visible as a zero."""
    counts = {str(kind): 0 for kind in NegativeClass}
    for negative in negatives:
        counts[str(negative.kind)] += 1
    return counts


__all__ = [
    "DEFAULT_TOOL_ROLES",
    "LOOP_REPEATS",
    "LOOP_WINDOW",
    "PATH_KEYS",
    "Negative",
    "NegativeClass",
    "ToolRoles",
    "class_counts",
    "mine",
    "mine_all",
]
