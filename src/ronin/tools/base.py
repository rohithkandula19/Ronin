"""What a tool is, and the session state that makes the write guard possible.

A tool is a class with a spec and one ``async run(args, ctx)``. The spec is data
(``ronin.core.types.ToolSpec``) so it can be sent to a model; the class holds the
behaviour so it can hold nothing else.

Two things in here carry more weight than their size suggests:

* **``ToolContext.read_files``** is why ``write`` can refuse to clobber a file the
  model has not looked at. That single rule prevents most destructive edits, and it
  only works if "has been read" is *session* state rather than per-call state.
* **Descriptions are prompts.** ``describe()`` renders when-to-use, when-not-to-use
  and a worked example in a fixed order, because a description that says only what
  a tool does gets called in situations it should not be. Half of agent quality is
  in these strings, so the shape of them is enforced here rather than left to each
  tool's discretion.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..core.types import DangerLevel, ToolResult, ToolSpec

#: Hard ceiling on what any tool hands back to the model, in characters. The loop
#: truncates too, but a tool that streams a gigabyte into memory first has already
#: lost — the cap belongs at the point of production as well as consumption.
MAX_RESULT_CHARS = 60_000


@dataclass(slots=True)
class ToolContext:
    """Everything a tool is allowed to know about the session it runs in.

    Mutable, and the *only* mutable state the tool layer has. Passed in rather than
    reached for, so a test constructs one in a temp directory and nothing touches
    the developer's actual filesystem.
    """

    #: The directory relative paths resolve against, and the boundary tools refuse
    #: to escape. Not a security boundary on its own — a determined ``bash`` can
    #: leave — but it stops the accidental ``../../etc/hosts``.
    root: Path
    #: Absolute paths the model has read this session. ``write`` consults this.
    read_files: set[str] = field(default_factory=set)
    #: Whether the model can accept image blocks. ``read`` returns a description
    #: instead of an image when it cannot, rather than silently returning nothing.
    vision: bool = False
    #: Environment for subprocesses. Injected so a test can run with a clean one.
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    #: The model's own scratchpad, owned by ``todo_write`` and read by the UI.
    todos: tuple[Any, ...] = ()
    #: Where a long-running tool may post output *while it runs*, so the interface can
    #: show a test suite or a build as it happens rather than only once it exits. Bound
    #: per call by the registry, so a chunk is always attributable to one tool use.
    #:
    #: Optional by design: a tool that ignores it behaves exactly as before, and the
    #: complete output still reaches the model in the returned ``ToolResult``. This is
    #: liveness, never the record — never route anything through here that the model
    #: needs, because nothing guarantees a consumer is listening.
    on_output: Callable[[str], None] | None = None

    def resolve(self, path: str) -> Path:
        """Turn a model-supplied path into an absolute one under ``root``.

        Rejects anything that escapes the root *after* symlink resolution, because
        a symlink pointing out of the tree is the interesting case and a naive
        ``startswith`` on the unresolved path misses it.
        """
        if not path or not path.strip():
            raise ToolError("path is required and cannot be empty")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = _resolve_without_requiring_existence(candidate)
        root = _resolve_without_requiring_existence(self.root)
        if resolved != root and root not in resolved.parents:
            raise ToolError(
                f"{path!r} resolves to {resolved}, which is outside the working "
                f"directory {root}. Tools refuse paths outside the tree."
            )
        return resolved

    def mark_read(self, path: Path) -> None:
        self.read_files.add(str(path))

    def has_been_read(self, path: Path) -> bool:
        return str(path) in self.read_files


def _resolve_without_requiring_existence(path: Path) -> Path:
    """``Path.resolve`` that works for a file that does not exist yet.

    ``write`` targets a path that is *supposed* to be missing, so resolution has to
    tolerate that while still following symlinks on the parts that do exist.
    """
    return Path(os.path.normpath(path.expanduser().absolute())).resolve()


class ToolError(Exception):
    """A tool refused, with a message written for the model to act on.

    Raised inside ``run`` and converted to ``ToolResult(ok=False)`` by
    :meth:`Tool.execute`, so a tool body can use ``raise`` for control flow while
    the boundary the loop sees still returns values, never exceptions.

    The message is a prompt. "invalid path" tells the model nothing; "the file has
    not been read in this session — call read first" tells it what to do next.
    """


@dataclass(frozen=True, slots=True)
class Example:
    """One worked example for a tool description."""

    call: str
    result: str


class Tool(ABC):
    """One capability the model can invoke.

    Subclasses set the class attributes and implement ``run``. Everything else —
    the rendered description, the ``ToolSpec``, the error boundary — is here so
    every tool presents identically to the model and to the loop.
    """

    #: The name the model calls. Short, lowercase, a verb.
    name: str = ""
    #: One line: what this does. The rest of the prompt is assembled from the
    #: fields below, in a fixed order.
    summary: str = ""
    #: When the model *should* reach for this.
    when_to_use: str = ""
    #: When it should not — the half that stops a tool being used as a hammer.
    when_not_to_use: str = ""
    #: JSON Schema for the arguments.
    schema: ClassVar[Mapping[str, Any]] = {}
    danger_level: DangerLevel = DangerLevel.READ_ONLY
    requires_approval: bool = False
    #: Worked examples. At least one is required; see :meth:`spec`.
    examples: ClassVar[Sequence[Example]] = ()

    def describe(self) -> str:
        """The full description sent to the model.

        A fixed order, because a model reading twenty tool descriptions benefits
        from every one having the same shape more than it benefits from any
        individual one being cleverly written.
        """
        parts = [self.summary.strip()]
        if self.when_to_use:
            parts.append(f"WHEN TO USE\n{self.when_to_use.strip()}")
        if self.when_not_to_use:
            parts.append(f"WHEN NOT TO USE\n{self.when_not_to_use.strip()}")
        for example in self.examples:
            parts.append(f"EXAMPLE\n{example.call.strip()}\n→ {example.result.strip()}")
        return "\n\n".join(parts)

    def spec(self) -> ToolSpec:
        """This tool as data. Validates that the description was actually written."""
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set a name")
        if not self.summary:
            raise ValueError(f"tool {self.name!r} must set a summary")
        if not self.when_to_use:
            raise ValueError(
                f"tool {self.name!r} must say when to use it — a description that "
                "only says what it does gets called in situations it should not be"
            )
        if not self.examples:
            raise ValueError(
                f"tool {self.name!r} must carry at least one worked example; "
                "models copy examples far more reliably than they read schemas"
            )
        return ToolSpec(
            name=self.name,
            description=self.describe(),
            json_schema=dict(self.schema),
            danger_level=self.danger_level,
            requires_approval=self.requires_approval,
        )

    @abstractmethod
    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        """Do the thing. May raise :class:`ToolError`; must not raise anything else."""

    async def execute(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        """``run`` with the error boundary the loop's contract requires.

        Errors are values across this boundary, including the ones we did not
        anticipate: an ``OSError`` from a vanished file is an observation the model
        can recover from, not a crash the user should see.
        """
        try:
            result = await self.run(args, ctx)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc))
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                ok=False, error=f"{self.name} failed unexpectedly: {type(exc).__name__}: {exc}"
            )
        if len(result.content) > MAX_RESULT_CHARS:
            return _truncate(result)
        return result


def _truncate(result: ToolResult) -> ToolResult:
    """Cap an oversized result, saying what was cut. Deterministic."""
    content = result.content
    cut = len(content) - MAX_RESULT_CHARS
    head = content[:MAX_RESULT_CHARS]
    return ToolResult(
        ok=result.ok,
        content=(
            f"{head}\n…[truncated: {cut} chars cut, result exceeded {MAX_RESULT_CHARS} chars]"
        ),
        error=result.error,
        artifacts=result.artifacts,
        tokens_estimate=result.tokens_estimate,
    )


def require_str(args: Mapping[str, Any], key: str) -> str:
    """Pull a required string argument, with a message the model can act on."""
    value = args.get(key)
    if value is None:
        raise ToolError(f"{key!r} is required")
    if not isinstance(value, str):
        raise ToolError(f"{key!r} must be a string, got {type(value).__name__}")
    return value


def optional_int(args: Mapping[str, Any], key: str, default: int | None = None) -> int | None:
    """Pull an optional integer, tolerating the string form models often send."""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolError(f"{key!r} must be an integer, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ToolError(f"{key!r} must be an integer, got {value!r}")


def optional_bool(args: Mapping[str, Any], key: str, default: bool = False) -> bool:
    """Pull an optional boolean, tolerating ``"true"``/``"false"`` strings."""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ToolError(f"{key!r} must be a boolean, got {value!r}")
