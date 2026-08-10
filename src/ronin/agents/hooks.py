"""hooks: shell commands on lifecycle events, and the one exit code that can say no.

A hook is a shell command the session runs when something happens. It receives the
event as JSON on stdin, and what it does with its exit code is the whole feature:

* **exit 0** — fine. Anything on stdout is context the model can be shown.
* **exit 2** — *block*. The action does not happen and stderr becomes the reason
  the model is told. This is the load-bearing case. It is how a team enforces
  "never touch ``migrations/``" or "reject an edit that drops a type annotation"
  without patching Ronin, and it is why ``PreToolUse`` runs before the tool rather
  than alongside it.
* **any other non-zero** — a warning. The action proceeds. A hook that exits 1
  because ``prettier`` was not installed must not stop the agent from working;
  making every failure a block would mean one broken hook wedges every session,
  and the first thing a user does with a wedged agent is delete all their hooks.

The distinction between 2 and other non-zero codes is the part people get wrong,
so it is spelled out in :class:`HookOutcome`, tested both ways, and printed by the
demo.

**Blocking only makes sense before the fact.** ``PreToolUse`` and
``UserPromptSubmit`` can block, because the tool has not run and the prompt has not
reached the model. ``PostToolUse``, ``Stop`` and ``SessionStart`` cannot: the file
is already written, and an exit 2 there is reported as a warning because there is
nothing left to prevent. See :data:`BLOCKING_EVENTS`.

**Timeouts do not block, and they take the children with them.** Every hook gets a
deadline (:data:`DEFAULT_TIMEOUT_SECONDS`). On expiry the *process group* is killed
— ``start_new_session=True`` makes the hook a group leader, so a hook that
backgrounds ``npm test`` cannot outlive its own timeout and keep holding the
terminal. A timeout is a warning, not a block, for the same reason a non-2 exit is:
a flaky hook must not be able to stop the agent from working. ``block_on_timeout``
exists for the rare hook whose whole purpose is to refuse (a policy check that
must fail closed), and it is opt-in per hook.

The subprocess call is injected (:data:`HookProcess`), so decision logic, matching,
capping and aggregation are all tested without spawning anything. One test does
spawn a real ``sh -c``, because argv construction, stdin plumbing and exit-code
propagation are exactly what a mock would paper over.

Config shape — this is the file users write by hand, ``.ronin/hooks.json``::

    {
      "PreToolUse": [
        {
          "matcher": "write|edit|multi_edit",
          "hooks": [
            {"command": "scripts/protect-migrations.sh", "timeout": 5,
             "name": "no-migrations", "block_on_timeout": true}
          ]
        }
      ],
      "PostToolUse": [
        {
          "matcher": "edit|write",
          "hooks": [{"command": "npx prettier --write $(jq -r .arguments.path)"}]
        }
      ],
      "SessionStart": [
        {"hooks": [{"command": "git rev-parse --abbrev-ref HEAD"}]}
      ]
    }

``matcher`` is a ``|``-separated list of glob patterns matched against the tool
name; it defaults to ``*`` and is only meaningful for the two tool events. The
event JSON on stdin looks like ``{"event": "PreToolUse", "tool_name": "write",
"arguments": {...}}`` — keys sorted, so a hook's behaviour is reproducible.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..core.types import ToolResult

#: The exit code that means "do not do this". Chosen by convention, not by us —
#: hooks are shared between agents and 2 is what the ecosystem already uses.
BLOCK_EXIT_CODE = 2

#: A hook is a check, not a build step. Long enough for a linter on one file.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Per stream. A hook that prints its whole test suite must not push the
#: transcript out of the context window on its way to saying "ok".
MAX_HOOK_OUTPUT_CHARS = 4_000

#: Grace period for a killed process group to actually die before we stop waiting.
KILL_GRACE_SECONDS = 1.0

#: Matcher that matches every tool.
MATCH_ALL = "*"


class HookEvent(StrEnum):
    """When a hook fires. Values are the strings users write in their config."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    SESSION_START = "SessionStart"


#: Events where exit 2 can actually stop something. Elsewhere the thing has
#: already happened, so a "block" would be a lie told after the fact.
BLOCKING_EVENTS: frozenset[HookEvent] = frozenset(
    {HookEvent.PRE_TOOL_USE, HookEvent.USER_PROMPT_SUBMIT}
)

#: Events that carry a tool name, and are therefore worth matching on.
TOOL_EVENTS: frozenset[HookEvent] = frozenset({HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE})


class HookOutcome(StrEnum):
    """What one hook run means for the action that triggered it."""

    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


class HookConfigError(ValueError):
    """A hook config that cannot be loaded, named by the key that is wrong.

    Loud because the alternative is a hook that silently never fires, which looks
    exactly like a hook that fires and does nothing.
    """


@dataclass(frozen=True, slots=True)
class HookSpec:
    """One configured hook: an event, a command, and how patient to be.

    ``name`` is cosmetic but earns its keep in the block reason — "no-migrations
    blocked this" is actionable where "hook 2 blocked this" sends the user hunting
    through a config file.
    """

    event: HookEvent
    command: str
    matcher: str = MATCH_ALL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    block_on_timeout: bool = False
    name: str = ""

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("HookSpec.command is empty")
        if self.timeout_seconds <= 0:
            raise ValueError("HookSpec.timeout_seconds must be positive")
        if self.matcher != MATCH_ALL and self.event not in TOOL_EVENTS:
            raise ValueError(
                f"{self.event.value} carries no tool name, so matcher "
                f"{self.matcher!r} would never match. Use '*' for this event."
            )

    @property
    def label(self) -> str:
        return self.name or self.command

    def matches(self, tool_name: str | None) -> bool:
        """Whether this hook applies to ``tool_name``.

        ``|`` separates alternatives so one entry can cover ``write|edit``, which
        is how the config reads most naturally for the rule people actually write.
        """
        if self.matcher == MATCH_ALL:
            return True
        if tool_name is None:
            return False
        return any(
            fnmatch(tool_name, pattern.strip())
            for pattern in self.matcher.split("|")
            if pattern.strip()
        )


@dataclass(frozen=True, slots=True)
class HookInvocation:
    """What the injected runner is asked to execute. Fully determined data."""

    command: str
    payload: str
    timeout_seconds: float
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookCompletion:
    """What the runner reports back. A timeout is a value, not an exception."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


#: How a hook is actually executed. Injected so every decision path is testable
#: without a shell, and so a sandboxed deployment can substitute its own launcher.
HookProcess = Callable[[HookInvocation], Awaitable[HookCompletion]]


def cap(text: str, *, label: str, limit: int = MAX_HOOK_OUTPUT_CHARS) -> str:
    """Truncate deterministically, naming exactly what was cut."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return f"{text[:limit]}\n…[truncated: {cut} chars cut from hook {label}]"


async def run_shell_hook(invocation: HookInvocation) -> HookCompletion:
    """Execute a hook with ``sh -c``, killing the whole process group on timeout.

    ``start_new_session=True`` puts the hook in its own process group so
    :func:`os.killpg` reaps anything it spawned. Without it, a hook that starts a
    watcher in the background survives its own timeout and the session inherits a
    process it never asked for.

    On timeout the streams come back empty: ``communicate`` was abandoned, and
    inventing partial output would be worse than admitting we have none.
    """
    process = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        invocation.command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=invocation.cwd,
        env=dict(invocation.env),
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(invocation.payload.encode()),
            timeout=invocation.timeout_seconds,
        )
    except TimeoutError:
        _kill_group(process.pid)
        # A group that refuses SIGKILL (uninterruptible sleep) is not worth waiting
        # on: we have already given up, and the timeout is the honest outcome.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
        return HookCompletion(exit_code=-1, timed_out=True)
    return HookCompletion(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


def _kill_group(pid: int) -> None:
    """SIGKILL the process group led by ``pid``, tolerating a race with exit."""
    # Already gone, or reparented out of reach: either way there is nothing left to
    # kill, and raising here would mask the timeout we are reporting.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


@dataclass(frozen=True, slots=True)
class HookRun:
    """One hook, executed. ``outcome`` is derived, never stored twice."""

    spec: HookSpec
    completion: HookCompletion

    @property
    def outcome(self) -> HookOutcome:
        if self.completion.timed_out:
            return HookOutcome.BLOCK if self.spec.block_on_timeout else HookOutcome.WARN
        if self.completion.exit_code == 0:
            return HookOutcome.OK
        if self.completion.exit_code == BLOCK_EXIT_CODE and self.spec.event in BLOCKING_EVENTS:
            return HookOutcome.BLOCK
        return HookOutcome.WARN

    @property
    def blocked(self) -> bool:
        return self.outcome is HookOutcome.BLOCK

    def message(self) -> str:
        """The text this run contributes: stderr if it said anything, else stdout.

        stderr first because a hook that refuses explains itself on stderr, and a
        blocking reason the model cannot read is a block it cannot comply with. The
        fallbacks exist so :meth:`HookReport.block_reason` is never empty.
        """
        stderr = cap(self.completion.stderr.strip(), label=f"{self.spec.label} stderr")
        if stderr:
            return stderr
        stdout = cap(self.completion.stdout.strip(), label=f"{self.spec.label} stdout")
        if stdout:
            return stdout
        if self.completion.timed_out:
            return (
                f"hook {self.spec.label!r} timed out after "
                f"{self.spec.timeout_seconds:g}s and was killed"
            )
        return f"hook {self.spec.label!r} exited {self.completion.exit_code} and printed nothing"


@dataclass(frozen=True, slots=True)
class HookReport:
    """Every hook that ran for one event, and what the session should do about it.

    All matching hooks run even after one blocks: a user with two ``PreToolUse``
    checks wants both reasons, not a race between them, and the tool is not going
    to run either way.
    """

    event: HookEvent
    runs: tuple[HookRun, ...] = ()
    tool_name: str | None = None

    @property
    def blocked(self) -> bool:
        return any(run.blocked for run in self.runs)

    def block_reason(self) -> str:
        """Why the action was refused, written for the model to act on.

        Empty only when nothing blocked. Every blocking run contributes a line, so
        two hooks refusing produce two reasons rather than one arbitrary winner.
        """
        blocking = [run for run in self.runs if run.blocked]
        if not blocking:
            return ""
        target = f" on {self.tool_name}" if self.tool_name else ""
        lines = [f"blocked by a {self.event.value} hook{target}:"]
        lines += [f"- {run.spec.label}: {run.message()}" for run in blocking]
        lines.append(
            "This is a project rule enforced outside the model. Do not retry the "
            "same call — satisfy the rule, or tell the user why you cannot."
        )
        return "\n".join(lines)

    def warnings(self) -> tuple[str, ...]:
        """Non-blocking failures, for the UI. Not fed to the model as instructions."""
        return tuple(
            f"{run.spec.label}: {run.message()}"
            for run in self.runs
            if run.outcome is HookOutcome.WARN
        )

    def context(self) -> str:
        """stdout from hooks that succeeded — additional context for the model.

        This is how ``SessionStart`` tells the model what branch it is on.
        """
        parts = [
            run.completion.stdout.strip()
            for run in self.runs
            if run.outcome is HookOutcome.OK and run.completion.stdout.strip()
        ]
        return "\n".join(cap(part, label=f"{self.event.value} stdout") for part in parts)

    def as_tool_result(self) -> ToolResult | None:
        """The block as the failed ``ToolResult`` the model sees, or ``None``.

        Errors are values across the tool boundary, and a hook block is exactly
        that: an observation the model can recover from, not an exception.
        """
        reason = self.block_reason()
        return None if not reason else ToolResult(ok=False, error=reason)


@dataclass(frozen=True, slots=True)
class HookConfig:
    """The parsed set of hooks. Immutable, so a session cannot grow hooks mid-run."""

    hooks: tuple[HookSpec, ...] = ()

    def for_event(self, event: HookEvent, tool_name: str | None = None) -> tuple[HookSpec, ...]:
        """Hooks that apply, in config order — users expect their order to hold."""
        return tuple(spec for spec in self.hooks if spec.event is event and spec.matches(tool_name))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> HookConfig:
        """Parse the documented config shape. Every rejection names the key.

        See the module docstring for a worked example.
        """
        specs: list[HookSpec] = []
        for raw_event, groups in data.items():
            try:
                event = HookEvent(str(raw_event))
            except ValueError as exc:
                known = [e.value for e in HookEvent]
                raise HookConfigError(
                    f"unknown hook event {raw_event!r}; known events are {known}"
                ) from exc
            if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
                raise HookConfigError(
                    f'{raw_event}: expected a list of {{"matcher": ..., "hooks": [...]}} groups'
                )
            for index, group in enumerate(groups):
                specs.extend(cls._parse_group(event, group, index))
        return cls(hooks=tuple(specs))

    @staticmethod
    def _parse_group(event: HookEvent, group: object, index: int) -> list[HookSpec]:
        where = f"{event.value}[{index}]"
        if not isinstance(group, Mapping):
            raise HookConfigError(f"{where}: expected an object, got {type(group).__name__}")
        matcher = str(group.get("matcher", MATCH_ALL))
        entries = group.get("hooks")
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            raise HookConfigError(f"{where}: 'hooks' must be a list of commands")
        specs: list[HookSpec] = []
        for position, entry in enumerate(entries):
            spot = f"{where}.hooks[{position}]"
            if not isinstance(entry, Mapping):
                raise HookConfigError(f"{spot}: expected an object with a 'command'")
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                raise HookConfigError(f"{spot}: 'command' is required and must be a string")
            raw_timeout = entry.get("timeout", DEFAULT_TIMEOUT_SECONDS)
            if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
                raise HookConfigError(f"{spot}: 'timeout' must be a number of seconds")
            try:
                specs.append(
                    HookSpec(
                        event=event,
                        command=command,
                        matcher=matcher,
                        timeout_seconds=float(raw_timeout),
                        block_on_timeout=bool(entry.get("block_on_timeout", False)),
                        name=str(entry.get("name", "")),
                    )
                )
            except ValueError as exc:
                raise HookConfigError(f"{spot}: {exc}") from exc
        return specs


def load_hook_config(path: Path) -> HookConfig:
    """Load ``.ronin/hooks.json``. A missing file means no hooks, which is normal."""
    if not path.is_file():
        return HookConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HookConfigError(f"{path}: not valid JSON — {exc}") from exc
    if not isinstance(data, Mapping):
        raise HookConfigError(f"{path}: expected an object keyed by event name")
    return HookConfig.from_mapping(data)


def event_payload(
    event: HookEvent,
    *,
    tool_name: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> str:
    """The JSON a hook reads on stdin. Keys sorted so hooks are reproducible."""
    body: dict[str, Any] = {"event": event.value}
    if tool_name is not None:
        body["tool_name"] = tool_name
    if data:
        body.update(data)
    return json.dumps(body, sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class HookRunner:
    """Fires the configured hooks for an event and reports what they decided.

    Holds no mutable state: the config is frozen, the process launcher is injected,
    and each :meth:`fire` returns a fresh :class:`HookReport`. A session can
    therefore share one runner across every turn without hooks accumulating.
    """

    config: HookConfig
    process: HookProcess = run_shell_hook
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)

    async def fire(
        self,
        event: HookEvent,
        *,
        tool_name: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> HookReport:
        """Run every matching hook, sequentially, and aggregate the outcomes.

        Sequential on purpose: hooks mutate the working tree (``prettier --write``),
        and two of them racing on one file is a corrupted file. The deadline is
        per hook, so ordering costs latency and never correctness.
        """
        specs = self.config.for_event(event, tool_name)
        if not specs:
            return HookReport(event=event, tool_name=tool_name)
        payload = event_payload(event, tool_name=tool_name, data=data)
        runs: list[HookRun] = []
        for spec in specs:
            completion = await self.process(
                HookInvocation(
                    command=spec.command,
                    payload=payload,
                    timeout_seconds=spec.timeout_seconds,
                    cwd=self.cwd,
                    env=self.env,
                )
            )
            runs.append(HookRun(spec=spec, completion=completion))
        return HookReport(event=event, runs=tuple(runs), tool_name=tool_name)
