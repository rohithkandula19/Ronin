"""Reading real recorded runs. Eval transcripts freely; user sessions only on request.

Two source kinds, deliberately asymmetric.

**Eval-suite runs** are the agent's own output on the agent's own fixtures. Reading
them needs nothing but a path.

**Real sessions** are somebody's work. So the consent rule is structural, not a
convention: :class:`SessionHarvest` defaults to ``enabled=False`` with
``session_dir=None``, and there is no default that points anywhere — no ``~/.ronin``,
no ``Path.cwd()``, no environment variable. A caller who has not named a directory
cannot cause one to be read, because :meth:`SessionHarvest.resolve` raises before the
first filesystem call. Enabling it also requires a scrubber: a session may not enter
a corpus unredacted, and the loud failure is the point.

The ``ronin.*`` import is lazy. The training package installs on a bare Colab runtime
where only ``training/`` is importable (``training/FREE_TRAINING.md``), so the
harvester must load and its pure pipeline must run there even when the agent tree is
absent. Importing ``ronin.persistence`` at module scope would break that.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provenance import SourceKind
from .trajectory import (
    RecordedCall,
    RecordedResult,
    Step,
    ToolSchema,
    Trajectory,
)

#: A transcript reader: path -> the events it holds. Injected so the consent test can
#: prove no path is read, and so the pure pipeline is testable with no agent tree.
TranscriptReader = Callable[[Path], Sequence[Any]]


class HarvestError(RuntimeError):
    """A source could not be read, or was not permitted to be read."""


@dataclass(frozen=True, slots=True)
class SessionHarvest:
    """Opt-in configuration for harvesting real user sessions. Off by default.

    ``enabled`` and ``session_dir`` are separate on purpose. A single ``dir or None``
    field would make "I passed a path" and "I meant to harvest" the same statement,
    and the flag is what a reviewer looks for.
    """

    enabled: bool = False
    session_dir: Path | None = None
    #: ``text -> redacted text``. Required when enabled; there is no default, because
    #: a default scrubber is a scrubber nobody chose.
    scrub: Callable[[str], str] | None = None

    def resolve(self) -> Path:
        """The directory to read, or a refusal. Raises **before** any filesystem call."""
        if not self.enabled:
            raise HarvestError(
                "real-session harvesting is off. Pass "
                "SessionHarvest(enabled=True, session_dir=<dir>, scrub=<fn>) to opt in; "
                "there is no default directory, on purpose"
            )
        if self.session_dir is None:
            raise HarvestError(
                "session harvesting is enabled but no session_dir was given. This "
                "code has no default location and will not guess one — name the "
                "directory explicitly"
            )
        if self.scrub is None:
            raise HarvestError(
                "session harvesting needs a scrub callable (e.g. a wrapper over "
                "ronin_training.redaction.redact). A real session may not enter a "
                "training corpus unredacted"
            )
        return self.session_dir

    def scrubber(self) -> Callable[[str], str]:
        scrub = self.scrub
        if scrub is None:  # pragma: no cover - resolve() refuses first
            raise HarvestError("no scrub callable configured")
        return scrub


@dataclass(frozen=True, slots=True)
class RunManifest:
    """What an eval run recorded *about* itself, beside its transcript.

    The verify outcome and the toolset are here rather than inferred from the
    transcript because neither is derivable from it: the events do not say whether
    ``verify.sh`` passed, and a tool the run never called still has to appear in the
    prompt. Absent means absent — :class:`Trajectory` then treats the run as
    unverified and the filter drops it.
    """

    task_id: str
    run_id: str
    transcript: Path
    verified: bool = False
    model: str = ""
    prompt: str = ""
    system: str = ""
    tools: tuple[ToolSchema, ...] = ()
    source: SourceKind = SourceKind.EVAL_SUITE
    #: Free-form extras a runner recorded; carried, never interpreted.
    extra: dict[str, Any] = field(default_factory=dict)


def read_transcript_events(path: Path) -> Sequence[Any]:
    """The default :data:`TranscriptReader`: ``ronin.persistence.read_events``.

    Imported here rather than at module scope so the pure pipeline stays importable
    without the agent tree on the path.
    """
    try:
        from ronin.persistence import read_events
    except ImportError as exc:  # pragma: no cover - depends on install layout
        raise HarvestError(
            f"reading a recorded transcript needs ronin.persistence on the path "
            f"({exc}). The pure harvest pipeline works without it; only transcript "
            f"loading does not"
        ) from exc
    return read_events(path).events


def steps_from_events(
    events: Iterable[Any], *, scrub: Callable[[str], str] | None = None
) -> tuple[Step, ...]:
    """Fold a ``ronin.core.types.Event`` stream into assistant turns.

    Structural rather than isinstance-based: the events are matched by the attributes
    the fold needs, so this works on the persisted events and equally on an
    eval-suite record that carries the same shape. A duck-typed fold is the seam that
    lets this module stay import-free of the agent tree.
    """
    clean = scrub or (lambda text: text)
    steps: list[Step] = []
    text_parts: list[str] = []
    calls: list[RecordedCall] = []
    results: list[RecordedResult] = []
    index = 0

    def flush() -> None:
        nonlocal index, text_parts, calls, results
        if not (text_parts or calls or results):
            return
        steps.append(
            Step(
                index=index,
                text=clean("".join(text_parts)),
                calls=tuple(calls),
                results=tuple(results),
            )
        )
        index += 1
        text_parts = []
        calls = []
        results = []

    for event in events:
        name = type(event).__name__
        if name == "TextDelta":
            if not getattr(event, "thinking", False):
                text_parts.append(str(getattr(event, "text", "")))
        elif name == "StreamReset":
            # The provider re-streamed this turn; the text already collected is not
            # what the model finally said, so keeping it would train a phantom turn.
            text_parts = []
        elif name == "ToolStart":
            calls.append(
                RecordedCall(
                    id=str(event.tool_use_id),
                    name=str(event.name),
                    arguments=dict(getattr(event, "arguments", {}) or {}),
                )
            )
        elif name == "ToolEnd":
            result = event.result
            results.append(
                RecordedResult(
                    call_id=str(event.tool_use_id),
                    name=str(event.name),
                    ok=bool(result.ok),
                    content=clean(str(getattr(result, "content", "") or "")),
                    error=clean(str(getattr(result, "error", "") or "")),
                )
            )
        elif name == "TurnEnd":
            # One recorded turn per TurnEnd: the loop yields TurnStart/TurnEnd around
            # each model call, and a turn boundary is where an SFT example is cut.
            flush()
    flush()
    return tuple(steps)


def load_run(
    manifest: RunManifest,
    *,
    read: TranscriptReader | None = None,
    scrub: Callable[[str], str] | None = None,
) -> Trajectory:
    """One recorded run as a :class:`Trajectory`."""
    reader = read or read_transcript_events
    events = reader(manifest.transcript)
    prompt = manifest.prompt or _first_user_prompt(events)
    if not prompt:
        raise HarvestError(
            f"run {manifest.run_id!r} has no task prompt: the manifest did not carry "
            f"one and {manifest.transcript} contains no user turn to recover it from"
        )
    return Trajectory(
        task_id=manifest.task_id,
        run_id=manifest.run_id,
        prompt=(scrub or (lambda t: t))(prompt),
        model=manifest.model,
        system=manifest.system,
        tools=manifest.tools,
        steps=steps_from_events(events, scrub=scrub),
        verified=manifest.verified,
        source=manifest.source,
    )


def _first_user_prompt(events: Iterable[Any]) -> str:
    """Recover the task text from a recorded state, if any event carries one."""
    for event in events:
        state = getattr(event, "agent_state", None)
        for message in getattr(state, "messages", ()) or ():
            if str(getattr(message, "role", "")) != "user":
                continue
            for block in getattr(message, "content_blocks", ()) or ():
                text = getattr(block, "text", None)
                if isinstance(text, str) and text:
                    return text
    return ""


def load_runs(
    manifests: Sequence[RunManifest],
    *,
    read: TranscriptReader | None = None,
    scrub: Callable[[str], str] | None = None,
) -> tuple[Trajectory, ...]:
    """Load a whole eval sweep. A run that cannot be read fails the load loudly."""
    return tuple(load_run(m, read=read, scrub=scrub) for m in manifests)


def load_sessions(
    config: SessionHarvest,
    manifests: Sequence[RunManifest],
    *,
    read: TranscriptReader | None = None,
) -> tuple[Trajectory, ...]:
    """Harvest real sessions. Refuses unless explicitly enabled, pathed and scrubbed.

    ``config.resolve()`` runs first and raises before ``read`` is touched, which is
    what makes "no path is read without being passed" checkable rather than asserted.
    """
    root = config.resolve()
    scrub = config.scrubber()
    inside = tuple(m for m in manifests if _within(root, m.transcript))
    outside = [str(m.transcript) for m in manifests if m not in inside]
    if outside:
        raise HarvestError(
            "refusing to read session transcript(s) outside the directory that was "
            f"opted in ({root}): {', '.join(sorted(outside))}"
        )
    return tuple(
        load_run(
            RunManifest(
                task_id=m.task_id,
                run_id=m.run_id,
                transcript=m.transcript,
                verified=m.verified,
                model=m.model,
                prompt=m.prompt,
                system=m.system,
                tools=m.tools,
                source=SourceKind.SESSION,
                extra=m.extra,
            ),
            read=read,
            scrub=scrub,
        )
        for m in inside
    )


def _within(root: Path, candidate: Path) -> bool:
    """Whether ``candidate`` is under ``root``, without touching the filesystem."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "HarvestError",
    "RunManifest",
    "SessionHarvest",
    "TranscriptReader",
    "load_run",
    "load_runs",
    "load_sessions",
    "read_transcript_events",
    "steps_from_events",
]
