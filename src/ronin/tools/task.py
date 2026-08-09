"""todo_write and task — the model's scratchpad, and nested loops with fresh context.

``todo_write`` looks like a UI nicety and is not. A model that writes down a plan and
crosses items off stays coherent over a long task in a way the same model does not
when it holds the plan in its head; the list survives compaction, and it is the thing
the user reads to know whether the agent is still on the thing they asked for. It is
required for any task with three or more steps for exactly that reason.

``task`` spawns a nested loop with its own context, its own tool subset and its own
budget, and returns **only a final summary**. The parent never sees the subagent's
intermediate turns, which is the entire point: forty grep hits and eight file reads
cost the subagent's context, not the parent's. It uses the ``fast`` model, because
triage and summarizing are mechanical work — see ``ronin.providers.router``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from ..core.types import Todo, TodoStatus, ToolResult
from .base import Example, Tool, ToolContext, ToolError, require_str

#: Beyond this a "plan" is a transcript, and the model stops reading its own list.
MAX_TODOS = 30


def parse_todos(raw: object) -> tuple[Todo, ...]:
    """Validate the model's todo list into ``Todo`` values.

    Enforces exactly one ``in_progress`` item. Two things in progress means the model
    has lost track of what it is doing, and zero while items remain means nothing is
    driving — both are worth refusing with a message rather than rendering.
    """
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ToolError("'todos' must be a list of {content, status} objects")
    if not raw:
        raise ToolError(
            "'todos' is empty. To clear the list after finishing, mark every item "
            "completed instead — an empty list loses the record of what was done."
        )
    if len(raw) > MAX_TODOS:
        raise ToolError(
            f"{len(raw)} todos is too many to be a plan ({MAX_TODOS} max). Group the "
            "work into fewer, larger steps."
        )

    todos: list[Todo] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ToolError(f"todos[{index}] must be an object, got {type(entry).__name__}")
        # `ronin.core.types.Todo` calls the text `subject`; models overwhelmingly write
        # `content`. Accept both rather than making the model learn our field name.
        key = "content" if "content" in entry else "subject"
        subject = require_str(entry, key).strip()
        if not subject:
            raise ToolError(f"todos[{index}].{key} is empty")
        raw_status = entry.get("status", "pending")
        try:
            status = TodoStatus(str(raw_status))
        except ValueError as exc:
            valid = [s.value for s in TodoStatus]
            raise ToolError(
                f"todos[{index}].status is {raw_status!r}; must be one of {valid}"
            ) from exc
        # Ids are positional and derived, not model-supplied: a model asked to invent
        # stable ids across calls reuses them inconsistently, and the list is replaced
        # wholesale every call anyway.
        todos.append(Todo(id=f"todo-{index + 1}", subject=subject, status=status))

    active = [t for t in todos if t.status is TodoStatus.IN_PROGRESS]
    if len(active) > 1:
        raise ToolError(
            f"{len(active)} todos are in_progress at once. Exactly one thing should "
            "be in progress — mark the others pending until you get to them."
        )
    unfinished = [t for t in todos if t.status is not TodoStatus.COMPLETED]
    if unfinished and not active:
        raise ToolError(
            "no todo is in_progress but work remains. Mark the one you are starting "
            "as in_progress so the user can see what is happening."
        )
    return tuple(todos)


def render_todos(todos: Sequence[Todo]) -> str:
    """The list as the UI shows it, and as the model reads it back."""
    marks = {
        TodoStatus.PENDING: "[ ]",
        TodoStatus.IN_PROGRESS: "[~]",
        TodoStatus.COMPLETED: "[x]",
    }
    lines = [f"{marks[todo.status]} {todo.subject}" for todo in todos]
    completed = sum(1 for t in todos if t.status is TodoStatus.COMPLETED)
    lines.append(f"({completed}/{len(todos)} done)")
    return "\n".join(lines)


class TodoWriteTool(Tool):
    name = "todo_write"
    summary = "Write or update your plan as a checklist the user can see."
    when_to_use = (
        "Any task with three or more steps, and any task where you will be working "
        "for a while. Write the plan before starting, then update it as you go: mark "
        "one item in_progress, complete it, move to the next. This is what keeps a "
        "long task coherent, and it is what the user reads to know you are still on "
        "the thing they asked for."
    )
    when_not_to_use = (
        "Not for a single-step task — a one-item checklist is noise. Not as a place "
        "to write notes or findings; the items are steps, and the plan should stay "
        "readable at a glance."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The complete list, every call. Not a delta.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The step, imperative."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call=(
                "todo_write(todos=[\n"
                '  {"content": "Read the failing test", "status": "completed"},\n'
                '  {"content": "Fix the off-by-one in parse()", "status": "in_progress"},\n'
                '  {"content": "Run the suite", "status": "pending"}\n])'
            ),
            result="[x] Read the failing test\n[~] Fix the off-by-one in parse()\n"
            "[ ] Run the suite\n(1/3 done)",
        ),
    )

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        todos = parse_todos(args.get("todos"))
        ctx.todos = todos
        return ToolResult(ok=True, content=render_todos(todos))


# --------------------------------------------------------------------------- #
# Subagents
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SubagentType:
    """One kind of subagent: what it is for, and what it may touch.

    ``tools`` is a subset of the parent's registry by name. A subagent that can only
    read and search cannot damage anything, which is what makes spawning one a cheap
    decision rather than a risky one.

    ``model_role`` is a *role name* (``"main"``/``"plan"``/``"fast"``), never a model
    and never a client — the tool layer may not import ``providers``. Empty means
    "whatever the orchestrator gives a subagent by default", which is the fast model.
    It exists because the honest default is wrong for one case: a subagent that edits
    code and reruns a test is not mechanical work, and running it on the cheap model
    to save tokens spends more of them. Only a definition **on disk** may set it —
    the model cannot pick its own tier by naming one in a ``task`` call.
    """

    name: str
    description: str
    tools: tuple[str, ...]
    system_prompt: str
    max_iterations: int = 20
    model_role: str = ""


#: The two that pay for themselves immediately. Both are read-only, so the parent can
#: spawn them without an approval prompt and without wondering what they changed.
BUILTIN_SUBAGENTS: Mapping[str, SubagentType] = {
    "explore": SubagentType(
        name="explore",
        description=(
            "Read-only search across many files. Use when answering a question means "
            "sweeping a directory and you only want the conclusion."
        ),
        tools=("read", "glob", "grep", "ls"),
        system_prompt=(
            "You are a search subagent. Find what was asked for and report only the "
            "conclusion with file:line references. Do not paste large excerpts — the "
            "parent agent cannot see your intermediate work, so your final message is "
            "the entire deliverable. Be specific and be brief."
        ),
    ),
    "summarize": SubagentType(
        name="summarize",
        description="Condense a file, a diff, or command output into the parts that matter.",
        tools=("read", "grep"),
        system_prompt=(
            "You are a summarizing subagent. Return the parts that change what "
            "someone would do next. Preserve exact identifiers, paths and numbers; "
            "drop everything decorative."
        ),
    ),
}

#: How a subagent actually runs. Injected because the tool layer must not import the
#: loop — that would put a cycle between two layers the architecture keeps apart.
SubagentRunner = Callable[[str, SubagentType], Awaitable[str]]


class TaskTool(Tool):
    name = "task"
    summary = "Spawn a subagent with fresh context; get back only its final answer."
    when_to_use = (
        "When answering means reading across many files and you want the conclusion, "
        "not the file dumps. The subagent's forty grep hits and eight reads cost its "
        "context, not yours. Also for independent work you can run while you do "
        "something else."
    )
    when_not_to_use = (
        "Not for a single lookup where you already know the file — read it yourself, "
        "a subagent is slower. Not for anything needing your conversation history: "
        "the subagent starts fresh and cannot see it. Not for work that must edit "
        "files, unless you spawn a type whose tool list includes editing."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The complete task. The subagent sees nothing else — no history, "
                    "no context — so state everything it needs."
                ),
            },
            "subagent_type": {
                "type": "string",
                "description": "Which kind of subagent to spawn.",
            },
        },
        "required": ["prompt", "subagent_type"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call=(
                'task(prompt="Find every place we construct a ModelRequest and list '
                'file:line for each", subagent_type="explore")'
            ),
            result=(
                "3 sites: providers/assembly.py:98, providers/bridge.py:81, "
                "providers/shim.py:412 — assembly.py is the only one callers should use."
            ),
        ),
    )

    def __init__(
        self,
        runner: SubagentRunner,
        *,
        types: Mapping[str, SubagentType] | None = None,
    ) -> None:
        self._runner = runner
        self._types = dict(types or BUILTIN_SUBAGENTS)

    def describe(self) -> str:
        """The base description plus the subagent types that actually exist.

        Listed dynamically because a model told about a type that is not registered
        will call it and get an error, and a model not told about one that is will
        never use it.
        """
        catalogue = "\n".join(
            f"- {t.name}: {t.description} (tools: {', '.join(t.tools)})"
            for t in self._types.values()
        )
        return f"{super().describe()}\n\nAVAILABLE SUBAGENT TYPES\n{catalogue}"

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = require_str(args, "prompt")
        kind = require_str(args, "subagent_type")
        subagent = self._types.get(kind)
        if subagent is None:
            known = ", ".join(sorted(self._types)) or "(none registered)"
            raise ToolError(f"unknown subagent_type {kind!r}. Available: {known}")
        if not prompt.strip():
            raise ToolError("'prompt' is empty — the subagent has no other context to work from")

        summary = await self._runner(prompt, subagent)
        if not summary.strip():
            return ToolResult(
                ok=False,
                error=(
                    f"the {kind} subagent returned nothing. Its context is gone, so "
                    "there is no transcript to inspect — try a more specific prompt."
                ),
            )
        return ToolResult(ok=True, content=summary.strip())
