"""Run the *relevant* subset of the verify commands, and hand failures back as
an observation.

Two decisions carry this module.

**Failures come back as a** :class:`~ronin.core.types.ToolResult`, never as a
user-role message. This is not a stylistic preference. A tool result reads to the
model as "here is what happened when I did the thing I chose to do" and it keeps
working on the objective it already has. The same bytes delivered as a user turn
read as a *new instruction from the human*, and the model re-scopes: it starts
explaining the failure, asking whether to continue, or treating the pytest output
as the new task and abandoning the original one. :func:`as_tool_result` is the
only supported way out of here for that reason.

**Relevance is computed, not guessed.** A changed ``.py`` file gets lint and
typecheck *on that file* and pytest on the test paths that actually exist for it;
the full suite is a separate, explicit, end-of-task step. When no test file can
be found the honest answer is a note saying so and naming the paths that were
looked for — not "run everything", which turns a one-second check into a
five-minute one and trains the user to disable verification.

The subprocess runner is injected (:data:`CommandRunner`) so every logic test in
this package is offline and deterministic; :func:`run_command` is the one real
implementation, exercised against ``python -c`` so the argv and exit-code
plumbing is proven rather than assumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final

from ..core.types import Mode, ToolResult
from .spec import CommandKind, DetectedCommand, VerifySpec, is_scopable

# --------------------------------------------------------------------------- #
# Output clamping
# --------------------------------------------------------------------------- #

#: Matches ``ronin.tools.shell``: head and tail both kept, tail favoured because
#: that is where the traceback and the failure summary live. Duplicated rather
#: than imported because ``verify`` may not depend on ``tools`` — the constants
#: are deliberately the same values so a user sees one truncation style.
MAX_OUTPUT_CHARS: Final[int] = 30_000
HEAD_SHARE: Final[float] = 0.4

#: What a whole verify pass may contribute to one tool result. Smaller than the
#: per-command cap: five commands each clamped to 30k would blow the turn.
MAX_TOOL_RESULT_CHARS: Final[int] = 12_000

DEFAULT_COMMAND_TIMEOUT: Final[float] = 300.0


def clamp_output(text: str, *, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap output keeping both ends, and say exactly how much was cut."""
    if limit <= 0 or len(text) <= limit:
        return text
    head_chars = int(limit * HEAD_SHARE)
    tail_chars = limit - head_chars
    cut = len(text) - limit
    return (
        f"{text[:head_chars]}\n"
        f"…[{cut:,} characters cut from the middle; head and tail kept]…\n"
        f"{text[-tail_chars:]}"
    )


# --------------------------------------------------------------------------- #
# One command's outcome
# --------------------------------------------------------------------------- #


class CommandFailure(StrEnum):
    """Why a command produced no usable exit code. ``NONE`` means it ran."""

    NONE = "none"
    NOT_FOUND = "not_found"
    TIMED_OUT = "timed_out"
    NOT_PERMITTED = "not_permitted"


#: Exit code recorded when the process never ran. 127 is the shell's own "command
#: not found", so the number a user sees means what they expect it to mean.
EXIT_NOT_RUN: Final[int] = 127


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """The result of trying to run one command.

    "Tried" is the operative word: a missing binary and a timeout are *outcomes*,
    not exceptions, because the repair loop has to reason about them the same way
    it reasons about a failing assertion.
    """

    argv: tuple[str, ...]
    exit_code: int
    output: str = ""
    duration_s: float = 0.0
    failure: CommandFailure = CommandFailure.NONE

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("CommandOutcome.argv must be non-empty")
        if self.failure is not CommandFailure.NONE and self.exit_code == 0:
            raise ValueError(
                f"CommandOutcome cannot report {self.failure.value} with exit_code 0 — "
                "a command that did not run did not succeed"
            )
        if self.duration_s < 0:
            raise ValueError("CommandOutcome.duration_s must be >= 0")

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failure is CommandFailure.NONE

    @property
    def display(self) -> str:
        return " ".join(self.argv)

    def describe(self) -> str:
        """One line naming the command and how it ended, for a human or a model."""
        if self.failure is CommandFailure.NOT_FOUND:
            return f"{self.display} — not found on PATH"
        if self.failure is CommandFailure.TIMED_OUT:
            return f"{self.display} — timed out after {self.duration_s:.0f}s"
        if self.failure is CommandFailure.NOT_PERMITTED:
            return f"{self.display} — refused: {self.output.strip() or 'not permitted'}"
        return f"{self.display} — exit {self.exit_code}"


#: The seam every model-driven and test-driven path goes through. Keyword
#: arguments (``cwd``, ``timeout``, ``env``) are part of the contract; the
#: ``...`` parameter list is what lets a fake accept only what it cares about.
CommandRunner = Callable[..., Awaitable[CommandOutcome]]


async def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> CommandOutcome:
    """Run one command with no shell, merging stderr into stdout.

    No shell, because these commands come from a detector and a model and there
    is no reason for either to have metacharacters. stderr is merged because
    every tool here writes half its story to each stream and interleaving them
    afterwards is guesswork.

    On timeout the whole process *group* is killed: a ``pytest`` that spawned
    workers leaves them holding the pipe, and killing only the parent hangs the
    read forever (the same failure ``ronin.tools.shell`` handles).
    """
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except FileNotFoundError:
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=EXIT_NOT_RUN,
            output=f"{argv[0]}: command not found",
            failure=CommandFailure.NOT_FOUND,
        )
    except (PermissionError, OSError) as exc:
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=EXIT_NOT_RUN,
            output=f"{argv[0]}: {exc}",
            failure=CommandFailure.NOT_PERMITTED,
        )

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(BaseException):
            await process.wait()
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=EXIT_NOT_RUN,
            output=f"killed after {timeout:.0f}s",
            duration_s=time.monotonic() - started,
            failure=CommandFailure.TIMED_OUT,
        )

    return CommandOutcome(
        argv=tuple(argv),
        exit_code=process.returncode if process.returncode is not None else EXIT_NOT_RUN,
        output=clamp_output(stdout.decode("utf-8", errors="replace")),
        duration_s=time.monotonic() - started,
    )


# --------------------------------------------------------------------------- #
# Language rules
# --------------------------------------------------------------------------- #


class Scoping(StrEnum):
    """Whether narrowing a command to changed files preserves its meaning."""

    PER_FILE = "per_file"
    WHOLE_PROJECT = "whole_project"


@dataclass(frozen=True, slots=True)
class LanguageRules:
    """Per-language knowledge: what can be narrowed, and where tests live.

    ``tools`` is what makes narrowing safe in a polyglot repo. The spec holds one
    lint command; appending ``app.ts`` to ``ruff check`` would fail for a reason
    that has nothing to do with the edit, so a command is only ever narrowed with
    files belonging to the language that owns its binary.

    ``test_patterns`` are templates over ``{dir}`` (the source's own directory),
    ``{dir*}`` (that directory *and* every suffix of it, so ``src/pkg`` also tries
    ``pkg`` and the empty path), ``{stem}`` and ``{ext}``.
    """

    name: str
    extensions: frozenset[str]
    tools: frozenset[str]
    test_patterns: tuple[str, ...]
    test_file_globs: tuple[str, ...]
    lint_scoping: Scoping = Scoping.PER_FILE
    typecheck_scoping: Scoping = Scoping.PER_FILE
    test_scoping: Scoping = Scoping.PER_FILE

    def scoping_for(self, kind: CommandKind) -> Scoping:
        if kind is CommandKind.LINT:
            return self.lint_scoping
        if kind is CommandKind.TYPECHECK:
            return self.typecheck_scoping
        if kind is CommandKind.TEST:
            return self.test_scoping
        return Scoping.WHOLE_PROJECT

    def is_test_file(self, path: str) -> bool:
        name = PurePosixPath(path).name
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.test_file_globs)


PYTHON_RULES: Final[LanguageRules] = LanguageRules(
    name="python",
    extensions=frozenset({".py", ".pyi"}),
    tools=frozenset({"ruff", "mypy", "pytest", "pyright", "flake8", "black", "python", "python3"}),
    test_patterns=(
        "{dir}/test_{stem}.py",
        "{dir}/{stem}_test.py",
        "tests/{dir*}/test_{stem}.py",
        "test/{dir*}/test_{stem}.py",
    ),
    test_file_globs=("test_*.py", "*_test.py", "conftest.py"),
)

#: JS/TS ships as *data*, not as a claim of correctness: the patterns below are
#: the common conventions, and ``tsc`` is marked WHOLE_PROJECT because
#: ``tsc app.ts`` silently ignores tsconfig.json and reports errors the project
#: build does not have.
TYPESCRIPT_RULES: Final[LanguageRules] = LanguageRules(
    name="typescript",
    extensions=frozenset({".ts", ".tsx", ".mts", ".cts"}),
    tools=frozenset({"eslint", "tsc", "jest", "vitest", "prettier", "biome", "tsd"}),
    test_patterns=(
        "{dir}/{stem}.test.{ext}",
        "{dir}/{stem}.spec.{ext}",
        "{dir}/__tests__/{stem}.test.{ext}",
        "tests/{dir*}/{stem}.test.{ext}",
    ),
    test_file_globs=("*.test.*", "*.spec.*"),
    typecheck_scoping=Scoping.WHOLE_PROJECT,
)

JAVASCRIPT_RULES: Final[LanguageRules] = LanguageRules(
    name="javascript",
    extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    tools=frozenset({"eslint", "jest", "vitest", "prettier", "biome"}),
    test_patterns=(
        "{dir}/{stem}.test.{ext}",
        "{dir}/{stem}.spec.{ext}",
        "{dir}/__tests__/{stem}.test.{ext}",
        "tests/{dir*}/{stem}.test.{ext}",
    ),
    test_file_globs=("*.test.*", "*.spec.*"),
)

LANGUAGE_RULES: Final[tuple[LanguageRules, ...]] = (
    PYTHON_RULES,
    TYPESCRIPT_RULES,
    JAVASCRIPT_RULES,
)


def rules_for_path(
    path: str, rules: Sequence[LanguageRules] = LANGUAGE_RULES
) -> LanguageRules | None:
    suffix = PurePosixPath(path).suffix
    for rule in rules:
        if suffix in rule.extensions:
            return rule
    return None


def owning_rules(
    command: DetectedCommand, rules: Sequence[LanguageRules] = LANGUAGE_RULES
) -> LanguageRules | None:
    """Which language's toolchain this command belongs to, if any.

    Looks past an environment prefix (``uv run mypy`` → ``mypy``) and past a
    module invocation (``python -m pytest`` → ``pytest``).
    """
    argv = [PurePosixPath(part).name for part in command.argv]
    while len(argv) >= 2 and argv[0] in {"uv", "poetry", "pdm", "hatch"} and argv[1] == "run":
        argv = argv[2:]
    if len(argv) >= 3 and argv[0].startswith("python") and argv[1] == "-m":
        argv = argv[2:]
    if not argv:
        return None
    head = argv[0]
    for rule in rules:
        if head in rule.tools:
            return rule
    return None


# --------------------------------------------------------------------------- #
# Source path → test path
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TestTargets:
    """Where a source file's tests are, and what was looked at to decide.

    ``considered`` exists so "no test found" is a reviewable claim rather than an
    assertion — a user who disagrees can see which paths were tried.
    """

    source: str
    matched: tuple[str, ...]
    considered: tuple[str, ...]

    @property
    def found(self) -> bool:
        return bool(self.matched)


def _normalize(path: str) -> str:
    return re.sub(r"/{2,}", "/", path.replace("\\", "/")).strip("/")


def root_relative(path: str | Path, root: Path) -> str | None:
    """``path`` as a root-relative posix string, or ``None`` if it is outside.

    Edit tools report absolute paths and verify commands want repo-relative ones;
    doing the conversion here means a file outside the repo is *dropped with a
    note* rather than handed to pytest as an absolute path that quietly widens
    collection to another project.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        return _normalize(str(candidate)) or None
    try:
        relative = candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return _normalize(relative.as_posix()) or None


def candidate_test_paths(source: str, rules: LanguageRules) -> tuple[str, ...]:
    """Every path that could hold this file's tests, most specific first."""
    posix = PurePosixPath(_normalize(source))
    stem = posix.stem
    ext = posix.suffix.lstrip(".")
    dir_parts = posix.parent.parts if str(posix.parent) != "." else ()

    candidates: list[str] = []
    for template in rules.test_patterns:
        if "{dir*}" in template:
            variants = [
                template.replace("{dir*}", "/".join(dir_parts[index:]))
                for index in range(len(dir_parts) + 1)
            ]
        else:
            variants = [template.replace("{dir}", "/".join(dir_parts))]
        for variant in variants:
            resolved = _normalize(variant.replace("{stem}", stem).replace("{ext}", ext))
            if resolved and resolved != str(posix) and resolved not in candidates:
                candidates.append(resolved)
    return tuple(candidates)


def resolve_test_targets(source: str, *, root: Path, rules: LanguageRules) -> TestTargets:
    """Find the test files for ``source``, or report that there are none.

    A changed *test* file is its own target — the most common edit in a
    test-driven turn, and routing it through the source→test mapping would find
    nothing and run the whole suite instead.
    """
    normalized = _normalize(source)
    if rules.is_test_file(normalized):
        return TestTargets(source=normalized, matched=(normalized,), considered=(normalized,))
    candidates = candidate_test_paths(normalized, rules)
    matched = tuple(path for path in candidates if (root / path).is_file())
    return TestTargets(source=normalized, matched=matched, considered=candidates)


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


class Scope(StrEnum):
    """How wide one step is, so a human can see the subset being run."""

    CHANGED_FILES = "changed_files"
    MATCHED_TESTS = "matched_tests"
    WHOLE_PROJECT = "whole_project"
    FULL_SUITE = "full_suite"


@dataclass(frozen=True, slots=True)
class PlanStep:
    kind: CommandKind
    argv: tuple[str, ...]
    scope: Scope
    why: str

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("PlanStep.argv must be non-empty")
        if not self.why:
            raise ValueError("PlanStep.why is required — an unexplained step cannot be audited")

    @property
    def display(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True, slots=True)
class VerifyPlan:
    """What will run, and every honest gap in it."""

    steps: tuple[PlanStep, ...] = ()
    notes: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.steps

    def render(self) -> str:
        lines = [
            f"{step.kind.value:<9} {step.display}   [{step.scope.value}]" for step in self.steps
        ]
        if not lines:
            lines.append("(nothing to run)")
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)


def plan_for_changes(
    spec: VerifySpec,
    changed_paths: Sequence[str],
    *,
    root: Path,
    rules: Sequence[LanguageRules] = LANGUAGE_RULES,
    include_full_suite: bool = False,
) -> VerifyPlan:
    """Build the relevant subset for one turn's changes.

    ``include_full_suite`` is the end-of-task pass: the full test command with no
    narrowing, plus the build. Both are deliberately excluded from the per-turn
    pass, because a per-turn check that takes minutes is a check that gets turned
    off.
    """
    steps: list[PlanStep] = []
    notes: list[str] = []
    resolved: list[str] = []
    for raw in changed_paths:
        if not str(raw).strip():
            continue
        relative = root_relative(raw, root)
        if relative is None:
            notes.append(f"{raw} is outside {root}, so it was left out of the verify plan")
        else:
            resolved.append(relative)
    changed = _dedupe(resolved)

    by_language: dict[str, list[str]] = {}
    unknown: list[str] = []
    for path in changed:
        rule = rules_for_path(path, rules)
        if rule is None:
            unknown.append(path)
        else:
            by_language.setdefault(rule.name, []).append(path)
    if unknown:
        notes.append(
            "no language rules for "
            + ", ".join(unknown[:5])
            + ("…" if len(unknown) > 5 else "")
            + " — nothing language-specific was run for them"
        )

    lookup = {rule.name: rule for rule in rules}

    # ------------------------------------------------------- lint + typecheck
    for kind in (CommandKind.LINT, CommandKind.TYPECHECK):
        command = spec.get(kind)
        if command is None:
            notes.append(f"no {kind.value} command was detected, so none ran")
            continue
        owner = owning_rules(command, rules)
        if owner is None:
            steps.append(
                PlanStep(
                    kind=kind,
                    argv=command.argv,
                    scope=Scope.WHOLE_PROJECT,
                    why=f"{command.argv[0]} is not a known per-file tool, so it ran unnarrowed",
                )
            )
            continue
        files = by_language.get(owner.name, [])
        if not files:
            notes.append(
                f"{kind.value} ({command.display}) covers {owner.name}, and no "
                f"{owner.name} file changed"
            )
            continue
        if owner.scoping_for(kind) is Scoping.PER_FILE and is_scopable(command):
            steps.append(
                PlanStep(
                    kind=kind,
                    argv=command.with_args(*files).argv,
                    scope=Scope.CHANGED_FILES,
                    why=f"{len(files)} changed {owner.name} file(s)",
                )
            )
        else:
            reason = (
                f"{command.display} dispatches to a script, so a path cannot be appended"
                if not is_scopable(command)
                else f"{owner.name} {kind.value} reads project config and cannot be narrowed"
            )
            steps.append(
                PlanStep(kind=kind, argv=command.argv, scope=Scope.WHOLE_PROJECT, why=reason)
            )

    # -------------------------------------------------------------- the tests
    test_command = spec.test
    if test_command is None:
        notes.append(
            "no test command was detected, so nothing was proven about behaviour; "
            "declare one in RONIN.md if this repo has tests"
        )
    elif not is_scopable(test_command):
        steps.append(
            PlanStep(
                kind=CommandKind.TEST,
                argv=test_command.argv,
                scope=Scope.WHOLE_PROJECT,
                why=f"{test_command.display} dispatches to a script and cannot be narrowed",
            )
        )
    else:
        matched: list[str] = []
        unmatched: list[TestTargets] = []
        for name, files in by_language.items():
            rule = lookup[name]
            if rule.scoping_for(CommandKind.TEST) is not Scoping.PER_FILE:
                continue
            for path in files:
                targets = resolve_test_targets(path, root=root, rules=rule)
                if targets.found:
                    matched.extend(targets.matched)
                else:
                    unmatched.append(targets)
        selected = _dedupe(matched)
        if selected:
            steps.append(
                PlanStep(
                    kind=CommandKind.TEST,
                    argv=test_command.with_args(*selected).argv,
                    scope=Scope.MATCHED_TESTS,
                    why=f"{len(selected)} test file(s) matched the changed sources",
                )
            )
        for targets in unmatched:
            notes.append(
                f"no test file found for {targets.source} (looked for "
                + ", ".join(targets.considered[:4])
                + ("…" if len(targets.considered) > 4 else "")
                + ") — the full suite was not run in its place"
            )

    # ------------------------------------------------------- the end-of-task pass
    if include_full_suite:
        if test_command is not None:
            steps.append(
                PlanStep(
                    kind=CommandKind.TEST,
                    argv=test_command.argv,
                    scope=Scope.FULL_SUITE,
                    why="end-of-task pass: the narrowed runs cannot see a break elsewhere",
                )
            )
        if spec.build is not None:
            steps.append(
                PlanStep(
                    kind=CommandKind.BUILD,
                    argv=spec.build.argv,
                    scope=Scope.FULL_SUITE,
                    why="end-of-task pass",
                )
            )

    notes.extend(spec.notes)
    return VerifyPlan(steps=tuple(steps), notes=tuple(notes), changed_paths=changed)


# --------------------------------------------------------------------------- #
# Running the plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StepResult:
    step: PlanStep
    outcome: CommandOutcome

    @property
    def ok(self) -> bool:
        return self.outcome.ok


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """Everything one verify pass learned, including what it chose not to run."""

    plan: VerifyPlan
    results: tuple[StepResult, ...] = ()
    skipped: tuple[PlanStep, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ran(self) -> bool:
        return bool(self.results)

    @property
    def failures(self) -> tuple[StepResult, ...]:
        return tuple(result for result in self.results if not result.ok)

    @property
    def ok(self) -> bool:
        """True only when something ran and all of it passed.

        A pass with nothing to run is *not* ok — it is unknown, and callers must
        be forced to tell the difference (see :attr:`unknown`).
        """
        return self.ran and not self.failures

    @property
    def unknown(self) -> bool:
        return not self.ran

    @property
    def failure_output(self) -> str:
        """Just the failing commands' output — the input to signature matching."""
        return "\n".join(
            f"$ {result.outcome.display}\n{result.outcome.output}" for result in self.failures
        )

    def render(self) -> str:
        lines: list[str] = []
        for result in self.results:
            mark = "ok  " if result.ok else "FAIL"
            lines.append(f"{mark} {result.outcome.describe()}")
        for step in self.skipped:
            lines.append(f"skip {step.display} (an earlier step failed)")
        if not self.results and not self.skipped:
            lines.append("nothing ran")
        for note in (*self.plan.notes, *self.notes):
            lines.append(f"note: {note}")
        return "\n".join(lines)


async def run_plan(
    plan: VerifyPlan,
    *,
    run: CommandRunner,
    cwd: Path,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    stop_after_first_failure: bool = False,
) -> VerifyOutcome:
    """Execute the plan in order.

    Every step runs by default, even after a failure: one round trip that shows
    the model the lint error *and* the two failing tests is worth more than three
    round trips that reveal them one at a time. ``stop_after_first_failure`` is
    there for the fast inner loop of a repair attempt, where the next command's
    output is usually a consequence of the first failure rather than news.
    """
    results: list[StepResult] = []
    skipped: list[PlanStep] = []
    for index, step in enumerate(plan.steps):
        if stop_after_first_failure and any(not result.ok for result in results):
            skipped.extend(plan.steps[index:])
            break
        outcome = await run(list(step.argv), cwd=cwd, timeout=timeout)
        results.append(StepResult(step=step, outcome=outcome))
    return VerifyOutcome(plan=plan, results=tuple(results), skipped=tuple(skipped))


# --------------------------------------------------------------------------- #
# Back to the model
# --------------------------------------------------------------------------- #

_NEXT_STEP = (
    "Fix the cause, then this check runs again. If you believe the check itself is "
    "wrong, say so explicitly instead of editing around it."
)

_UNKNOWN_NEXT_STEP = (
    "This repo declared no verify command that applies to these changes, so nothing "
    "was proven. Do not report success as if it had been verified."
)


def as_tool_result(outcome: VerifyOutcome, *, limit: int = MAX_TOOL_RESULT_CHARS) -> ToolResult:
    """Package a verify pass as a tool result — **not** as a user message.

    The distinction is the whole point of this function. Delivered as a
    ``ToolResult``, a failing suite is an observation about an action the model
    just took, and it keeps pursuing the objective it already has. Delivered as a
    user-role message, the same text reads as a fresh instruction from the human:
    the model re-scopes to "explain this pytest output", asks whether to proceed,
    or adopts the traceback as the new task and drops the original one. Feed this
    value back through the tool-result path (``result.as_block(tool_use_id)``) and
    never as ``Message(role=Role.USER, ...)``.

    ``ok=False`` carries the failures in ``error`` because that is the field the
    loop shows the model when a call fails, and the error string is a prompt: it
    names what broke and what to do next.
    """
    body = clamp_output(outcome.render(), limit=limit)
    if outcome.unknown:
        return ToolResult(
            ok=True,
            content=f"verify: nothing to run.\n{body}\n\n{_UNKNOWN_NEXT_STEP}",
        )
    if outcome.ok:
        passed = f"verify: all {len(outcome.results)} check(s) passed."
        return ToolResult(ok=True, content=f"{passed}\n{body}")
    failed = ", ".join(result.step.kind.value for result in outcome.failures)
    detail = clamp_output(outcome.failure_output, limit=limit)
    return ToolResult(
        ok=False,
        error=(f"verify failed ({failed}).\n{body}\n\n{detail}\n\n{_NEXT_STEP}"),
    )


def should_verify(*, mode: Mode, changed_paths: Sequence[str]) -> bool:
    """Whether a turn that touched these paths should be verified without asking.

    Gated on the mode ladder rather than a boolean: ``PLAN`` mutates nothing so
    there is nothing to verify, and in ``ASK`` the human is already in the loop
    for every edit and does not need a second opinion they did not request.
    """
    return bool(changed_paths) and mode.at_least(Mode.AUTO_EDIT)


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT",
    "EXIT_NOT_RUN",
    "HEAD_SHARE",
    "JAVASCRIPT_RULES",
    "LANGUAGE_RULES",
    "MAX_OUTPUT_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "PYTHON_RULES",
    "TYPESCRIPT_RULES",
    "CommandFailure",
    "CommandOutcome",
    "CommandRunner",
    "LanguageRules",
    "PlanStep",
    "Scope",
    "Scoping",
    "StepResult",
    "TestTargets",
    "VerifyOutcome",
    "VerifyPlan",
    "as_tool_result",
    "candidate_test_paths",
    "clamp_output",
    "owning_rules",
    "plan_for_changes",
    "resolve_test_targets",
    "root_relative",
    "rules_for_path",
    "run_command",
    "run_plan",
    "should_verify",
]
