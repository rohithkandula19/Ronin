"""subagents as markdown files, and the path restriction that makes one of them safe.

A subagent is a description plus a tool list plus a system prompt, which is a
document, not code — so it lives in ``.ronin/agents/*.md`` with frontmatter, and a
team adds one by adding a file. Four are shipped in code (:data:`BUILTIN_AGENTS`)
and any of them can be replaced by a file of the same ``name``.

Three decisions here are worth the words:

**The frontmatter parser is stdlib.** Four to six ``key: value`` pairs do not
justify a YAML dependency, and a strict parser for a tiny grammar is shorter than
the code that would validate YAML's output anyway. What it accepts is exactly:
scalars, quoted scalars, inline ``[a, b, c]`` lists, and ``- item`` block lists.

**Malformed frontmatter is a named error with a line number, never a skip.** The
failure mode being prevented is specific and nasty: someone writes ``tool:``
instead of ``tools:``, the agent loads with no tools or does not load at all, and
what the user *sees* is the model being unhelpful — no error, no log, nothing to
search for. So an unknown key, a duplicate key, a bad name, an unclosed fence and
an empty body are all :class:`FrontmatterError` naming the file and the line.

**``test-writer`` cannot touch ``src/`` because of :class:`RestrictedPaths`, not
because it was asked nicely.** The wrapper composes over any tool, resolves every
path argument, and refuses anything outside the allowed globs with a
``ToolResult(ok=False)`` that names the roots it *may* write to. A prompt saying
"only write tests" is a preference; this is a mechanism.

Dispatch: the model normally names the type it wants (``task(subagent_type=...)``),
and that path does not come through here at all. :func:`select` is the *fallback*
for when a caller has a task description and no named type — token overlap against
each ``description``, and :data:`SELECTION_FLOOR` below which it returns ``None``,
because a wrong subagent (fresh context, wrong tools, confident answer) is worse
than no subagent.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..core.types import ToolResult, ToolSpec
from ..tools.base import Tool, ToolContext
from ..tools.registry import ToolRegistry
from ..tools.task import BUILTIN_SUBAGENTS, SubagentType

#: One value of a frontmatter key: a scalar or a list of scalars.
FrontmatterValue = str | tuple[str, ...]

#: The fence that opens and closes frontmatter.
FRONTMATTER_FENCE = "---"

#: Every key a definition file may set. An unknown key is an error, so ``tool:``
#: for ``tools:`` is caught at load instead of producing a toolless agent.
KNOWN_KEYS: tuple[str, ...] = (
    "name",
    "description",
    "tools",
    "model",
    "write_paths",
    "max_iterations",
)

#: Without these a definition cannot do anything or be dispatched to.
REQUIRED_KEYS: tuple[str, ...] = ("name", "description", "tools")

#: Role *names*, not models: this package may not import ``ronin.providers``, so a
#: definition names a role and the orchestrator resolves it against its router.
#: Restricting to roles is deliberate — a file naming a concrete model id would
#: need provider knowledge here to validate, and an unvalidated model id is a
#: runtime 404 at the worst moment.
MODEL_ROLES: frozenset[str] = frozenset({"main", "plan", "fast"})

#: Where definitions live, relative to the project root.
AGENTS_SUBDIR = Path(".ronin") / "agents"

#: Tools whose writes :func:`restrict_writes` wraps. Read tools need no wrapper —
#: a subagent reading outside its lane costs context, not correctness.
MUTATING_TOOL_NAMES: tuple[str, ...] = ("write", "edit", "multi_edit")

#: Argument keys that carry a path. Every shipped file tool uses ``path``; the
#: others are here so a future tool with a different name is covered by default
#: rather than silently unguarded.
PATH_ARG_KEYS: tuple[str, ...] = ("path", "file_path", "paths", "file_paths")

#: Below this share of the task's content words matching a description, dispatch
#: returns ``None``. A chosen floor, not a measured one: it is the point at which
#: "one word in common" stops counting as a match.
SELECTION_FLOOR = 0.25

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)$")
_BLOCK_ITEM_RE = re.compile(r"^-\s+(.*\S)\s*$")
_WORD_RE = re.compile(r"[a-z0-9_]+")

#: Words that appear in every task description and every agent description, so
#: counting them would make everything match everything.
STOPWORDS: frozenset[str] = frozenset(
    """a an and any are as at be but by can code do does doing for from has have how
    in into is it its not of on or that the their then there these this to use used
    using was what when where which who why will with you your file files find get
    make new one only other same see should some than them they thing things time up
    want way we work""".split()
)


class FrontmatterError(ValueError):
    """Malformed definition, identified by file and line.

    Carries ``source`` and ``line`` separately so a UI can point at the place; the
    message alone is already written to be pasted at a human.
    """

    def __init__(self, source: str, line: int, reason: str) -> None:
        super().__init__(f"{source}:{line}: {reason}")
        self.source = source
        self.line = line
        self.reason = reason


# --------------------------------------------------------------------------- #
# The frontmatter parser
# --------------------------------------------------------------------------- #


def _unquote(raw: str, source: str, line: int) -> str:
    """A scalar, with matched surrounding quotes removed."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if value[:1] in {'"', "'"}:
        raise FrontmatterError(
            source, line, f"unterminated quote in {value!r} — close it or drop both quotes"
        )
    if value[:1] in {">", "|"}:
        raise FrontmatterError(
            source,
            line,
            "multi-line block scalars (> and |) are not supported. Put the text on "
            "one line, or move it into the markdown body below the frontmatter.",
        )
    return value


def _inline_list(raw: str, source: str, line: int) -> tuple[str, ...]:
    """``[a, b, c]`` → a tuple. Empty entries are an error, not a shrug."""
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return ()
    items: list[str] = []
    for piece in inner.split(","):
        item = _unquote(piece, source, line)
        if not item:
            raise FrontmatterError(
                source, line, f"empty entry in list {raw.strip()!r} — remove the stray comma"
            )
        items.append(item)
    return tuple(items)


def parse_frontmatter(text: str, *, source: str = "<string>") -> tuple[
    Mapping[str, FrontmatterValue], str
]:
    """Split ``text`` into ``(frontmatter, body)``.

    The document must open with a ``---`` fence and close it. Everything after the
    closing fence is the body verbatim (leading blank lines trimmed), because that
    body is a system prompt and its internal formatting matters.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = 0
    while first < len(lines) and not lines[first].strip():
        first += 1
    if first >= len(lines) or lines[first].strip() != FRONTMATTER_FENCE:
        raise FrontmatterError(
            source,
            first + 1,
            f"expected the file to open with a {FRONTMATTER_FENCE!r} frontmatter "
            "fence naming at least 'name', 'description' and 'tools'",
        )

    fields: dict[str, FrontmatterValue] = {}
    pending_key: str | None = None
    pending_items: list[str] = []
    closed_at: int | None = None

    for offset, raw_line in enumerate(lines[first + 1 :], start=first + 2):
        stripped = raw_line.strip()
        if stripped == FRONTMATTER_FENCE:
            closed_at = offset
            break
        if not stripped or stripped.startswith("#"):
            continue

        block_item = _BLOCK_ITEM_RE.match(stripped)
        if block_item is not None:
            if pending_key is None:
                raise FrontmatterError(
                    source, offset, f"list item {stripped!r} does not follow a 'key:' line"
                )
            pending_items.append(_unquote(block_item.group(1), source, offset))
            continue

        match = _KEY_RE.match(stripped)
        if match is None:
            raise FrontmatterError(
                source, offset, f"expected 'key: value', got {stripped!r}"
            )
        if pending_key is not None:
            fields[pending_key] = tuple(pending_items)
            pending_key, pending_items = None, []

        key, raw_value = match.group(1), match.group(2)
        if key in fields:
            raise FrontmatterError(source, offset, f"duplicate key {key!r}")
        if key not in KNOWN_KEYS:
            raise FrontmatterError(
                source, offset, f"unknown key {key!r}; known keys are {list(KNOWN_KEYS)}"
            )
        value = raw_value.strip()
        if not value:
            # An empty value opens a `- item` block list; if none follows it stays
            # an empty list, and the required-key check reports it as missing.
            pending_key = key
            continue
        if value.startswith("["):
            if not value.endswith("]"):
                raise FrontmatterError(
                    source, offset, f"unclosed inline list {value!r} — add the ']'"
                )
            fields[key] = _inline_list(value, source, offset)
            continue
        fields[key] = _unquote(value, source, offset)

    if pending_key is not None:
        fields[pending_key] = tuple(pending_items)
    if closed_at is None:
        raise FrontmatterError(
            source,
            len(lines),
            f"frontmatter was never closed — add a {FRONTMATTER_FENCE!r} line before "
            "the system prompt",
        )

    body_lines = lines[closed_at:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    return fields, "\n".join(body_lines).rstrip() + "\n" if body_lines else ""


# --------------------------------------------------------------------------- #
# Definitions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """One subagent, as declared. Converts to a :class:`SubagentType` for the tool.

    ``model`` and ``write_paths`` have nowhere to live on ``SubagentType`` (which
    this package does not own), so they stay here: the orchestrator reads ``model``
    to pick a role and ``write_paths`` to build a restricted registry. Dropping
    either on conversion would be the quiet kind of bug — the agent would run with
    the session default model and unrestricted writes.
    """

    name: str
    description: str
    tools: tuple[str, ...]
    system_prompt: str
    model: str = ""
    write_paths: tuple[str, ...] = ()
    max_iterations: int = 20
    source: str = "<builtin>"

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"agent name {self.name!r} must be lowercase letters, digits, '-' or "
                "'_' — it is what the model types as subagent_type"
            )
        if not self.description.strip():
            raise ValueError(
                f"agent {self.name!r} needs a description: it is the only thing the "
                "dispatcher has to decide when to invoke it"
            )
        if not self.tools:
            raise ValueError(f"agent {self.name!r} declares no tools, so it can do nothing")
        if not self.system_prompt.strip():
            raise ValueError(
                f"agent {self.name!r} has an empty system prompt (the markdown body "
                "below the frontmatter)"
            )
        if self.model and self.model not in MODEL_ROLES:
            raise ValueError(
                f"agent {self.name!r} names model {self.model!r}; must be one of "
                f"{sorted(MODEL_ROLES)} (a role, resolved by the router)"
            )
        if self.max_iterations < 1:
            raise ValueError(f"agent {self.name!r} needs max_iterations >= 1")

    def to_subagent_type(self) -> SubagentType:
        """The value ``ronin.tools.task.TaskTool`` consumes, unchanged."""
        return SubagentType(
            name=self.name,
            description=self.description,
            tools=self.tools,
            system_prompt=self.system_prompt,
            max_iterations=self.max_iterations,
        )


def _require_list(
    fields: Mapping[str, FrontmatterValue], key: str, source: str
) -> tuple[str, ...]:
    """A list-valued key, accepting ``a, b`` as well as ``[a, b]`` and a block list."""
    value = fields[key]
    if isinstance(value, tuple):
        return value
    return tuple(part.strip() for part in value.split(",") if part.strip())


def parse_definition(text: str, *, source: str = "<string>") -> AgentDefinition:
    """Parse one ``.md`` definition. Every failure names ``source`` and a line."""
    fields, body = parse_frontmatter(text, source=source)
    missing = [key for key in REQUIRED_KEYS if not fields.get(key)]
    if missing:
        raise FrontmatterError(
            source, 1, f"frontmatter is missing required key(s): {missing}"
        )

    name = fields["name"]
    description = fields["description"]
    if isinstance(name, tuple) or isinstance(description, tuple):
        raise FrontmatterError(
            source, 1, "'name' and 'description' must be scalars, not lists"
        )

    raw_iterations = fields.get("max_iterations", "")
    if isinstance(raw_iterations, tuple):
        raise FrontmatterError(source, 1, "'max_iterations' must be a number, not a list")
    if raw_iterations and not raw_iterations.strip().isdigit():
        raise FrontmatterError(
            source, 1, f"'max_iterations' must be a positive integer, got {raw_iterations!r}"
        )
    model = fields.get("model", "")
    if isinstance(model, tuple):
        raise FrontmatterError(source, 1, "'model' must be a single role name, not a list")

    try:
        return AgentDefinition(
            name=name,
            description=description,
            tools=_require_list(fields, "tools", source),
            system_prompt=body,
            model=model,
            write_paths=(
                _require_list(fields, "write_paths", source) if fields.get("write_paths") else ()
            ),
            max_iterations=int(raw_iterations) if raw_iterations else 20,
            source=source,
        )
    except ValueError as exc:
        # AgentDefinition validates content; re-raise as the file-scoped error so a
        # user sees which file to open rather than a bare message.
        raise FrontmatterError(source, 1, str(exc)) from exc


def load_definition(path: Path) -> AgentDefinition:
    """Parse the definition at ``path``, reporting errors against that filename."""
    return parse_definition(path.read_text(encoding="utf-8"), source=str(path))


def load_directory(directory: Path) -> tuple[AgentDefinition, ...]:
    """Every ``*.md`` in ``directory``, sorted by filename. Missing dir → empty.

    A missing directory is normal (most projects define no agents); a *malformed
    file* inside one is not, and propagates.
    """
    if not directory.is_dir():
        return ()
    return tuple(load_definition(path) for path in sorted(directory.glob("*.md")))


def load_agents(
    project_root: Path,
    *,
    builtins: Mapping[str, AgentDefinition] | None = None,
) -> dict[str, AgentDefinition]:
    """The builtins, overridden by ``<project_root>/.ronin/agents/*.md`` by name.

    Override rather than merge: a file named ``reviewer.md`` replaces the built-in
    ``reviewer`` wholesale. A half-overridden definition — this project's tools
    with our prompt — is a configuration nobody chose.
    """
    agents = dict(builtins if builtins is not None else BUILTIN_AGENTS)
    for definition in load_directory(project_root / AGENTS_SUBDIR):
        agents[definition.name] = definition
    return agents


def subagent_catalogue(
    agents: Mapping[str, AgentDefinition],
    *,
    include_tool_builtins: bool = True,
) -> dict[str, SubagentType]:
    """The ``types`` mapping for ``TaskTool``: ours on top of ``BUILTIN_SUBAGENTS``.

    ``ronin.tools.task`` ships ``explore`` and ``summarize``; keeping them means a
    session that upgrades to file-defined agents does not lose the two types its
    prompts may already name.
    """
    catalogue: dict[str, SubagentType] = (
        dict(BUILTIN_SUBAGENTS) if include_tool_builtins else {}
    )
    for definition in agents.values():
        catalogue[definition.name] = definition.to_subagent_type()
    return catalogue


def model_roles(agents: Mapping[str, AgentDefinition]) -> dict[str, str]:
    """``name → role name`` for the agents that ask for a specific role."""
    return {name: a.model for name, a in agents.items() if a.model}


def gated_tools(definition: AgentDefinition, registry: ToolRegistry) -> tuple[str, ...]:
    """Tools this definition needs that ``registry`` marks ``requires_approval``.

    Worth checking at wiring time: ``ronin.session.SubagentPolicy`` denies every
    gated call, so a definition listing one gets a refusal it cannot act on in the
    middle of a run. Named here so the orchestrator can decide up front — widen the
    child policy, or drop the tool from the definition.
    """
    found: list[str] = []
    for name in definition.tools:
        spec = registry.get(name)
        if spec is not None and spec.requires_approval:
            found.append(name)
    return tuple(found)


# --------------------------------------------------------------------------- #
# Path restriction — the mechanism behind "test-writer cannot touch src"
# --------------------------------------------------------------------------- #


def path_allowed(relative: str, allowed: Sequence[str]) -> bool:
    """Whether ``relative`` (posix, root-relative) matches any allowed glob.

    ``fnmatch`` semantics, so ``*`` crosses directory separators and ``tests/*``
    and ``tests/**`` behave alike. Deliberate: the patterns here are permission
    boundaries, and a pattern that matched *less* than a user expected would be
    surprising in the direction of breaking their agent. A bare directory pattern
    (``tests`` or ``tests/``) allows everything beneath it.
    """
    for pattern in allowed:
        if fnmatch(relative, pattern):
            return True
        base = pattern.rstrip("/*")
        if base and (relative == base or relative.startswith(f"{base}/")):
            return True
    return False


def _paths_in(args: Mapping[str, Any], arg_keys: Sequence[str]) -> list[str]:
    """Every path-shaped argument value, flattened. Non-strings are ignored here.

    Ignored rather than rejected because argument *validation* is the wrapped
    tool's job — this wrapper only decides about the paths it can recognise, and a
    malformed argument reaches the real tool's error message intact.
    """
    found: list[str] = []
    for key in arg_keys:
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(item for item in value if isinstance(item, str))
    return found


class RestrictedPaths(Tool):
    """A tool that refuses to act outside ``allowed`` globs.

    Composes over any tool rather than subclassing a specific one, so the same
    mechanism restricts ``write``, ``edit`` and ``multi_edit`` without knowing what
    any of them do. The refusal is a ``ToolResult(ok=False)`` naming the allowed
    roots, because the model's next move should be "write it under tests/" and an
    error that does not say where to go produces a retry of the same call.

    Not a security boundary: a subagent with ``bash`` can write anywhere, and this
    wrapper does not pretend otherwise. It is a *lane* — enforcement of the one
    rule that keeps a test-writer from "fixing" the code under test.
    """

    def __init__(
        self,
        inner: Tool,
        allowed: Sequence[str],
        *,
        arg_keys: Sequence[str] = PATH_ARG_KEYS,
        label: str = "",
    ) -> None:
        if not allowed:
            raise ValueError(
                "RestrictedPaths with no allowed globs would refuse everything; "
                "omit the wrapper instead"
            )
        self._inner = inner
        self._allowed = tuple(allowed)
        self._arg_keys = tuple(arg_keys)
        self._label = label
        # Mirrored so `Tool.execute`'s unexpected-error message names the real tool.
        self.name = inner.name
        self.summary = inner.summary
        self.when_to_use = inner.when_to_use
        self.when_not_to_use = inner.when_not_to_use
        self.danger_level = inner.danger_level
        self.requires_approval = inner.requires_approval

    @property
    def inner(self) -> Tool:
        return self._inner

    @property
    def allowed(self) -> tuple[str, ...]:
        return self._allowed

    def _note(self) -> str:
        who = f"{self._label}: " if self._label else ""
        return (
            f"PATH RESTRICTION\n{who}this tool only acts on paths matching "
            f"{', '.join(self._allowed)}. Anything else is refused before it runs."
        )

    def describe(self) -> str:
        return f"{self._inner.describe()}\n\n{self._note()}"

    def spec(self) -> ToolSpec:
        """The inner spec with the restriction stated in the description.

        The model is told, *and* the check runs. Telling it saves a wasted call;
        the check is what makes the rule true.
        """
        return replace(self._inner.spec(), description=self.describe())

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        root = ctx.root.resolve()
        for raw in _paths_in(args, self._arg_keys):
            resolved = ctx.resolve(raw)
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                relative = resolved.as_posix()
            if not path_allowed(relative, self._allowed):
                who = f"{self._label} " if self._label else ""
                return ToolResult(
                    ok=False,
                    error=(
                        f"refused: {who}may only write to paths matching "
                        f"{', '.join(self._allowed)}, and {relative!r} is outside "
                        "those. Put the change somewhere allowed, or report what "
                        "needs changing elsewhere and let the caller do it."
                    ),
                )
        return await self._inner.run(args, ctx)


def restrict_writes(
    registry: ToolRegistry,
    allowed: Sequence[str],
    *,
    names: Sequence[str] = MUTATING_TOOL_NAMES,
    label: str = "",
    ctx: ToolContext | None = None,
) -> ToolRegistry:
    """A copy of ``registry`` with the named tools wrapped in :class:`RestrictedPaths`.

    Tools in ``names`` that the registry does not have are simply absent — a
    session with no ``multi_edit`` needs no wrapper for it. Every other tool passes
    through unchanged.
    """
    wanted = set(names)
    wrapped: list[Tool] = [
        RestrictedPaths(tool, allowed, label=label) if tool.name in wanted else tool
        for tool in registry.tools()
    ]
    return ToolRegistry(wrapped, ctx or registry.ctx)


def build_subagent_registry(
    base: ToolRegistry,
    definition: AgentDefinition,
    *,
    ctx: ToolContext | None = None,
) -> ToolRegistry:
    """The registry one subagent runs with: its declared subset, writes restricted.

    Unknown tool names raise (``subset``'s behaviour) — a definition naming a tool
    this session does not have is a typo, and a subagent quietly missing the tool
    it was built around looks like the model being lazy.
    """
    narrowed = base.subset(list(definition.tools), ctx=ctx)
    if not definition.write_paths:
        return narrowed
    return restrict_writes(
        narrowed, definition.write_paths, label=definition.name, ctx=ctx or narrowed.ctx
    )


# --------------------------------------------------------------------------- #
# The four builtins
# --------------------------------------------------------------------------- #

BUILTIN_AGENTS: Mapping[str, AgentDefinition] = {
    "explorer": AgentDefinition(
        name="explorer",
        description=(
            "Answers 'where is X handled' by searching the repository. Use it to "
            "locate the code behind a behaviour, symbol, error message or config "
            "key when you do not know which files are involved. Read-only; returns "
            "file:line references."
        ),
        tools=("read", "glob", "grep", "ls"),
        model="fast",
        system_prompt=(
            "You are the explorer subagent. You locate things in a repository and "
            "you cannot change anything.\n\n"
            "Answer with file:line references. Every claim you make must carry one — "
            "'the retry lives in providers/resilience.py:88', not 'the retry is in "
            "the provider layer'. If you cannot find a line to point at, say you "
            "could not find it and name where you looked.\n\n"
            "Your final message is the entire deliverable: the parent agent never "
            "sees your searches or the files you opened. So do not paste large "
            "excerpts, do not narrate the hunt, and do not stop at the first "
            "plausible hit when the question implies there may be several. Order "
            "your references by how likely they are to be the one that matters, and "
            "say which one you would start from."
        ),
    ),
    "reviewer": AgentDefinition(
        name="reviewer",
        description=(
            "Reviews a diff or a set of changed files and reports issues grouped by "
            "severity. Use it after making changes and before declaring them done. "
            "Read-only: it reports problems, it does not fix them."
        ),
        tools=("read", "glob", "grep"),
        model="main",
        system_prompt=(
            "You are the reviewer subagent. You read changes and report problems. "
            "You have no editing tools: fixing is the caller's job, and a review "
            "that quietly fixed something would leave the caller not knowing what "
            "changed.\n\n"
            "Group findings under exactly these headings, and omit a heading with "
            "nothing under it:\n\n"
            "BLOCKER — it is wrong, unsafe, or loses data. Say what input breaks it.\n"
            "MAJOR — it will bite: a missed error path, a broken invariant, a "
            "silent failure, a test that cannot fail.\n"
            "MINOR — worth fixing but nothing breaks: naming, duplication, a "
            "misleading comment.\n"
            "NOTE — an observation the caller should know, not a request.\n\n"
            "Every finding starts with file:line and states the consequence, not the "
            "rule: 'files.py:212 — write() does not check is_dir, so a directory "
            "path raises IsADirectoryError instead of a usable message', not 'add "
            "validation'. If you find nothing above MINOR, say so plainly — a review "
            "that manufactures a BLOCKER to look thorough trains the caller to "
            "ignore reviews."
        ),
    ),
    "test-writer": AgentDefinition(
        name="test-writer",
        description=(
            "Writes tests for existing behaviour. Use it to add coverage or to "
            "reproduce a bug as a failing test. It can only write under tests/ — it "
            "cannot change the code under test, so a test it writes either passes "
            "against the real behaviour or fails honestly."
        ),
        tools=("read", "glob", "grep", "write", "edit", "multi_edit"),
        model="main",
        write_paths=("tests/**",),
        system_prompt=(
            "You are the test-writer subagent. You write tests and you cannot write "
            "anywhere except tests/ — the restriction is enforced by the tool, not "
            "by this instruction, so do not plan around it.\n\n"
            "This is the point of you: a test that fails because the code is wrong "
            "is the deliverable. If the behaviour you are testing looks broken, "
            "write the test that demonstrates it, say so in your final message, and "
            "stop. Do not soften the assertion to make it pass.\n\n"
            "Read the code before testing it, and test behaviour the caller would "
            "notice — inputs and outputs, failure modes, edge values — not the "
            "implementation's internal steps. Name each test as a sentence about "
            "what it establishes. Use the repository's existing test style and "
            "helpers; a test that does not look like its neighbours will not be "
            "maintained.\n\n"
            "Report the paths you wrote and the exact command to run them."
        ),
    ),
    "fixer": AgentDefinition(
        name="fixer",
        description=(
            "Given a failing test, makes it pass. Use it when there is a concrete "
            "failure with a command that reproduces it. It edits code and reruns the "
            "test, and it stops after a bounded number of attempts."
        ),
        tools=("read", "glob", "grep", "write", "edit", "multi_edit", "bash"),
        model="main",
        max_iterations=5,
        system_prompt=(
            "You are the fixer subagent. You are given a failing test and you have "
            "five attempts to make it pass. That budget is the whole design: after "
            "five iterations a model is no longer fixing the bug, it is mutating "
            "code until the error message changes.\n\n"
            "Each attempt: read the failure, form one hypothesis about the cause, "
            "make the smallest change that tests it, rerun the test. Say the "
            "hypothesis before the change. If the change does not help, revert your "
            "thinking rather than stacking another change on top of it.\n\n"
            "Never edit the test to make it pass. Never delete, skip or xfail it. If "
            "the test is wrong, that is a finding, not a licence — report it and "
            "stop.\n\n"
            "If you run out of attempts, your final message must say: what you tried, "
            "what each attempt ruled out, and the single most likely cause remaining. "
            "A precise 'not fixed, and here is why' is worth more to the caller than "
            "a change that makes the error different."
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Dispatch fallback
# --------------------------------------------------------------------------- #


def _content_words(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(text.lower())
        if len(word) > 2 and word not in STOPWORDS
    }


def score_match(task_description: str, subagent: SubagentType) -> float:
    """Share of the task's content words that appear in the agent's description.

    Token overlap, on purpose. It is explainable — :func:`rank` can print the
    numbers — and its failure mode is a low score, which :func:`select` turns into
    ``None``. Anything cleverer here would be a second model call to decide which
    model call to make.
    """
    task_words = _content_words(task_description)
    if not task_words:
        return 0.0
    agent_words = _content_words(f"{subagent.name} {subagent.description}")
    agent_words |= {part for part in re.split(r"[-_]", subagent.name.lower()) if part}
    return len(task_words & agent_words) / len(task_words)


def rank(
    task_description: str, types: Mapping[str, SubagentType] | Iterable[SubagentType]
) -> tuple[tuple[str, float], ...]:
    """Every candidate with its score, best first. The explanation for a dispatch."""
    candidates = types.values() if isinstance(types, Mapping) else types
    scored = [(t.name, score_match(task_description, t)) for t in candidates]
    return tuple(sorted(scored, key=lambda pair: (-pair[1], pair[0])))


def select(
    task_description: str,
    types: Mapping[str, SubagentType] | Iterable[SubagentType],
    *,
    floor: float = SELECTION_FLOOR,
) -> SubagentType | None:
    """The best-matching subagent, or ``None`` when nothing clearly matches.

    ``None`` in three cases, all of which mean "do it yourself": nothing scores
    above ``floor``, the description is empty, or the top two tie. A tie is not a
    coin flip to be won — two agents describing themselves equally well for this
    task means the task does not distinguish them, and a fresh-context subagent
    with the wrong tools returns a confident wrong answer.

    Normally unused: the model names its subagent type in the ``task`` call, and
    that path never reaches here. This is for a caller that has a description and
    no name — a router, a slash command, a retry after an unknown type.
    """
    by_name = dict(types) if isinstance(types, Mapping) else {t.name: t for t in types}
    scores = rank(task_description, by_name)
    if not scores or scores[0][1] < floor:
        return None
    if len(scores) > 1 and scores[1][1] == scores[0][1]:
        return None
    return by_name[scores[0][0]]
