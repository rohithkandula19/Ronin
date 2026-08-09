"""bash — one persistent shell, not one subprocess per command.

The difference matters more than it sounds. With a fresh subprocess per call,
``cd src`` followed by ``pwd`` prints the original directory, ``export TOKEN=x``
evaporates, and ``source .venv/bin/activate`` does nothing at all. A model working
that way has to re-establish its environment in every command, and it will forget.

So: spawn ``bash --noprofile --norc`` once, write commands into its stdin, and read
stdout until a **unique sentinel** echoes back carrying the exit code. cwd, exports
and activated venvs survive because it is the same process throughout.

``--noprofile --norc`` is deliberate. The user's ``.bashrc`` might print a banner,
set a fancy prompt, define aliases that shadow real commands, or take two seconds to
load — none of which should affect what the agent sees. The cost is that the shell
starts without the user's conveniences, which is the right trade for reproducibility.

**Not ``-i``**, despite it being the obvious flag for "a shell that stays around".
Interactive bash with a pipe for stdin echoes every command back before running it,
prints ``cannot set terminal process group`` and ``no job control in this shell`` on
startup, and emits a prompt string — all of which land in the model's result. The
persistence comes from the process being long-lived, not from interactive mode, and
with ``--norc`` there are no aliases or interactive niceties left for ``-i`` to
enable. It buys nothing and costs noise on every single call.

Three failure modes get explicit handling, because each one otherwise hangs a turn:

* **Timeout** — kill the whole process *group*, not just bash. A command that
  spawned children leaves them holding the pipe open, and killing only the parent
  waits forever on a read that will never finish.
* **Huge output** — cap it with head *and* tail preserved. The tail is where the
  error message lives; a head-only cap throws away the reason the command failed.
* **A command that never returns** — background mode, so ``npm run dev`` returns a
  handle immediately and ``bash_output`` tails it.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..core.types import DangerLevel, ToolResult
from .base import Example, Tool, ToolContext, ToolError, optional_bool, optional_int, require_str

#: What the model is told when the shell process is gone. One message for every way
#: it can happen (EOF, reset pipe, a stray `kill`), because they are the same event
#: as far as the caller is concerned.
_DIED = (
    "the shell exited unexpectedly. Any state it held (cwd, exports, activated "
    "venvs) is gone; the next command starts a fresh one."
)

DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0
#: Output cap, split between head and tail. The tail matters more (that is where the
#: traceback is), so it gets the larger share.
MAX_OUTPUT_CHARS = 30_000
HEAD_SHARE = 0.4

#: Commands the model must not run here, each with the tool to use instead. This is
#: about steering, not security — `bash grep` works, it is just worse than `grep`,
#: and a model that reaches for it silently loses gitignore handling and output caps.
REDIRECTS: Mapping[str, str] = {
    "grep": "the grep tool (wraps ripgrep: respects gitignore, caps output)",
    "egrep": "the grep tool",
    "fgrep": "the grep tool",
    "rg": "the grep tool, which already wraps ripgrep",
    "find": "the glob tool (newest-first, and it will not walk .git)",
    "cat": "the read tool (line numbers, truncation marker, binary detection)",
    "head": "the read tool with a limit",
    "tail": "the read tool with an offset, or bash_output for a background command",
    "ls": "the ls tool",
}

#: Interactive programs that will hang forever waiting for a terminal we do not have.
INTERACTIVE = frozenset(
    {"vi", "vim", "nvim", "emacs", "nano", "pico", "less", "more", "top", "htop", "man"}
)

#: Commands that would end the shell itself, taking cwd, exports and any activated
#: venv with them. Found by a test: a model running `exit 3` to "return an exit code"
#: silently destroys the session state every later command depends on. Running it in
#: a subshell — `(exit 3)` — does what the model meant.
SESSION_ENDING = frozenset({"exit", "logout"})

#: Destructive shapes refused outright. Deliberately narrow — a long blocklist gives
#: false confidence, and the real boundary is the approval gate plus the sandbox.
#: These are the ones where an approval prompt arrives too late to help.
DENIED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+/(\s|$)"),
        "recursive delete of the filesystem root",
    ),
    (
        re.compile(r"\bmkfs(\.\w+)?\b"),
        "formatting a filesystem",
    ),
    (
        re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|disk)"),
        "writing directly to a block device",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "a fork bomb",
    ),
    (
        re.compile(r"\bchmod\s+(-[a-zA-Z]*\s+)*777\s+/(\s|$)"),
        "making the filesystem root world-writable",
    ),
    (
        re.compile(r">\s*/dev/(sd|nvme|disk)"),
        "overwriting a block device",
    ),
)


def check_command(command: str) -> None:
    """Refuse commands that should be a different tool, or should not run at all.

    Checks the first word of each ``;``/``&&``/``||``-separated segment, because
    ``cd src && grep foo`` must be caught on the second segment — a check that only
    looks at the front of the string is a check that is trivially bypassed by
    accident.
    """
    stripped = command.strip()
    if not stripped:
        raise ToolError("command is empty")

    for pattern, description in DENIED_PATTERNS:
        if pattern.search(stripped):
            raise ToolError(
                f"refused: this looks like {description}. If you genuinely need it, "
                "the user has to run it themselves — an agent should not."
            )

    for segment in re.split(r"&&|\|\||;|\|", stripped):
        words = segment.strip().split()
        if not words:
            continue
        # Skip a leading `sudo`/`env`/`time` so `sudo find /` is still caught.
        index = 0
        while index < len(words) and words[index] in {"sudo", "env", "time", "nohup"}:
            index += 1
        if index >= len(words):
            continue
        head = Path(words[index]).name
        if head in REDIRECTS:
            raise ToolError(
                f"refused: use {REDIRECTS[head]} instead of `{head}` in bash. The "
                "dedicated tool is faster, respects ignore files, and caps its "
                "output so it cannot flood your context."
            )
        if head in SESSION_ENDING:
            raise ToolError(
                f"refused: `{head}` would end the persistent shell, discarding the "
                "working directory, exported variables and any activated venv that "
                "later commands depend on. To produce an exit code, run it in a "
                f"subshell: `({head} 1)`."
            )
        if head in INTERACTIVE:
            raise ToolError(
                f"refused: `{head}` is interactive and there is no terminal here, so "
                "it would hang until the timeout. Use a non-interactive equivalent "
                "(for a file, use the read tool)."
            )


def clamp_output(text: str, *, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap output keeping both ends. The tail holds the error; the head holds the setup."""
    if len(text) <= limit:
        return text
    head_chars = int(limit * HEAD_SHARE)
    tail_chars = limit - head_chars
    cut = len(text) - limit
    return (
        f"{text[:head_chars]}\n"
        f"…[{cut:,} characters cut from the middle; head and tail kept]…\n"
        f"{text[-tail_chars:]}"
    )


@dataclass(slots=True)
class BackgroundJob:
    """A command still running, and whatever it has printed so far."""

    handle: str
    command: str
    process: asyncio.subprocess.Process
    chunks: list[str] = field(default_factory=list)
    finished: bool = False
    exit_code: int | None = None
    reader: asyncio.Task[None] | None = None

    def drain(self) -> str:
        text = "".join(self.chunks)
        self.chunks.clear()
        return text


class PersistentShell:
    """One long-lived ``bash`` process, driven by sentinel round-trips.

    The protocol is the whole implementation: write ``<command>\\n`` then
    ``printf '%s %s\\n' SENTINEL "$?"``, and read until that sentinel appears. The
    sentinel is a fresh uuid per shell, so a command that *prints* the sentinel text
    cannot fake a completion.
    """

    def __init__(self, *, cwd: Path, env: Mapping[str, str] | None = None) -> None:
        self._cwd = cwd
        self._env = dict(env or os.environ)
        self._process: asyncio.subprocess.Process | None = None
        self._sentinel = f"__RONIN_{uuid.uuid4().hex}__"
        self._lock = asyncio.Lock()

    @property
    def sentinel(self) -> str:
        return self._sentinel

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.alive:
            return
        binary = shutil.which("bash")
        if binary is None:
            raise ToolError("bash is not installed, so the bash tool cannot run.")
        self._process = await asyncio.create_subprocess_exec(
            binary,
            "--noprofile",
            "--norc",
            cwd=str(self._cwd),
            env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Its own process group, so a timeout can kill the command *and* every
            # child it spawned. Killing only bash leaves orphans holding the pipe.
            start_new_session=True,
        )
        # Belt and braces: silence any prompt machinery, then handshake on the
        # sentinel so nothing a startup file managed to print can leak into the
        # first command's output.
        await self._write("PS1=''; PS2=''; unset PROMPT_COMMAND\n")
        await self._write(f"printf '%s %s\\n' {self._sentinel} 0\n")
        assert self._process.stdout is not None
        await self._read_until_sentinel(self._process.stdout)

    async def _write(self, text: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ToolError("the shell is not running")
        try:
            process.stdin.write(text.encode())
            await process.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            self._process = None
            raise ToolError(_DIED) from exc

    async def run(self, command: str, *, timeout: float) -> tuple[str, int]:
        """Run one command to completion. Returns ``(output, exit_code)``."""
        async with self._lock:
            await self.start()
            process = self._process
            assert process is not None and process.stdout is not None

            await self._write(f"{command}\nprintf '%s %s\\n' {self._sentinel} \"$?\"\n")
            try:
                output, code = await asyncio.wait_for(
                    self._read_until_sentinel(process.stdout), timeout=timeout
                )
            except TimeoutError as exc:
                killed = await self._kill_group()
                raise ToolError(
                    f"command timed out after {timeout:.0f}s and was killed"
                    f"{' along with its child processes' if killed else ''}. If it "
                    "was meant to keep running (a dev server, a watcher), pass "
                    "run_in_background=true instead."
                ) from exc
            return output, code

    async def _read_until_sentinel(self, stream: asyncio.StreamReader) -> tuple[str, int]:
        collected: list[str] = []
        while True:
            try:
                line = await stream.readline()
            except (ConnectionResetError, BrokenPipeError) as exc:
                # A killed shell surfaces as a reset transport rather than EOF. Both
                # mean the same thing to the caller, and a raw ConnectionResetError
                # tells the model nothing it can act on.
                self._process = None
                raise ToolError(_DIED) from exc
            if not line:
                # The shell died. Report it rather than looping on empty reads.
                self._process = None
                raise ToolError(_DIED)
            text = line.decode("utf-8", errors="replace")
            if self._sentinel in text:
                _, _, tail = text.partition(self._sentinel)
                code = int(tail.strip() or 0) if tail.strip().lstrip("-").isdigit() else 0
                return "".join(collected), code
            collected.append(text)

    async def _kill_group(self) -> bool:
        """Kill the shell's whole process group. Returns whether children existed."""
        process = self._process
        if process is None or process.returncode is not None:
            return False
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
            return False
        finally:
            self._process = None
        await asyncio.sleep(0)
        return True

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        await process.wait()


@dataclass(slots=True)
class ShellSession:
    """The persistent shell plus its background jobs, held for a whole session."""

    shell: PersistentShell
    jobs: dict[str, BackgroundJob] = field(default_factory=dict)

    async def close(self) -> None:
        """Kill and *reap* everything. Unreaped children surface later as noise.

        Awaiting ``wait()`` matters: a killed-but-unwaited subprocess is finalized
        after the event loop closes, which raises ``RuntimeError: Event loop is
        closed`` from a destructor — far from the code that caused it.
        """
        for job in self.jobs.values():
            if job.reader is not None:
                job.reader.cancel()
            if job.process.returncode is None:
                job.process.kill()
            await job.process.wait()
        self.jobs.clear()
        await self.shell.close()


class BashTool(Tool):
    name = "bash"
    summary = "Run a shell command in a persistent bash session."
    when_to_use = (
        "Running tests, builds, linters, git commands, package managers — anything "
        "with a CLI. The session persists, so `cd` and `export` and an activated "
        "venv carry over to your next call; set up once and then work."
    )
    when_not_to_use = (
        "Do not use bash for searching or reading. Use the grep tool instead of "
        "`grep`/`rg` (it respects gitignore and caps output), the glob tool instead "
        "of `find`, the read tool instead of `cat`/`head`/`tail`, and the ls tool "
        "instead of `ls` — those calls are refused here. Interactive programs (vim, "
        "less, top) are refused too: there is no terminal, so they would hang. For a "
        "command that does not exit — a dev server, a file watcher — pass "
        "run_in_background=true and tail it with bash_output."
    )
    danger_level = DangerLevel.DESTRUCTIVE
    requires_approval = True
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {
                "type": "integer",
                "description": (
                    f"Seconds before the command is killed. Default {DEFAULT_TIMEOUT:.0f}."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "5-10 words on what this does, shown in the approval prompt."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Return a handle immediately instead of waiting. For servers/watchers."
                ),
            },
        },
        "required": ["command", "description"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call='bash(command="uv run pytest tests/ -q", description="Run the test suite")',
            result="309 passed in 3.25s\n(exit 0)",
        ),
        Example(
            call='bash(command="cd src && pwd", description="Move into src")',
            result="/repo/src  — and the next bash call still starts in /repo/src",
        ),
        Example(
            call=(
                'bash(command="npm run dev", description="Start the dev server", '
                "run_in_background=true)"
            ),
            result="started in background: handle=bg_1. Tail it with bash_output(handle='bg_1')",
        ),
    )

    def __init__(self, session: ShellSession) -> None:
        self._session = session

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        command = require_str(args, "command")
        check_command(command)
        timeout = float(optional_int(args, "timeout") or DEFAULT_TIMEOUT)
        if timeout <= 0 or timeout > MAX_TIMEOUT:
            raise ToolError(f"timeout must be between 1 and {MAX_TIMEOUT:.0f} seconds")

        if optional_bool(args, "run_in_background"):
            return await self._start_background(command, ctx)

        output, code = await self._session.shell.run(command, timeout=timeout)
        body = clamp_output(output.rstrip("\n"))
        if code == 0:
            return ToolResult(ok=True, content=body or "(no output)")
        return ToolResult(
            ok=False,
            content=body,
            error=f"command exited {code}",
        )

    async def _start_background(self, command: str, ctx: ToolContext) -> ToolResult:
        handle = f"bg_{len(self._session.jobs) + 1}"
        binary = shutil.which("bash")
        if binary is None:
            raise ToolError("bash is not installed")
        process = await asyncio.create_subprocess_exec(
            binary,
            "--noprofile",
            "--norc",
            "-c",
            command,
            cwd=str(ctx.root),
            env=dict(ctx.env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        job = BackgroundJob(handle=handle, command=command, process=process)
        self._session.jobs[handle] = job
        job.reader = asyncio.create_task(_pump(job))
        return ToolResult(
            ok=True,
            content=(
                f"started in background: handle={handle!r}, pid={process.pid}\n"
                f"command: {command}\n"
                f"Tail it with bash_output(handle={handle!r}). It keeps running until "
                "it exits or the session ends."
            ),
        )


async def _pump(job: BackgroundJob) -> None:
    """Drain a background job's output into its buffer until it exits."""
    stream = job.process.stdout
    if stream is None:  # pragma: no cover - always a pipe here
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            job.chunks.append(line.decode("utf-8", errors="replace"))
    except asyncio.CancelledError:
        raise
    finally:
        job.finished = job.process.returncode is not None
        job.exit_code = job.process.returncode


class BashOutputTool(Tool):
    name = "bash_output"
    summary = "Read new output from a background command started by bash."
    when_to_use = (
        "Checking on something you launched with run_in_background=true: has the dev "
        "server come up, did the build finish, what did the watcher print. Each call "
        "returns only what is new since the last one."
    )
    when_not_to_use = (
        "Not for a command that already finished in the foreground — its output was "
        "in the bash result. Do not poll this in a tight loop; do other work and "
        "check back."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "handle": {"type": "string", "description": "The handle bash returned."},
        },
        "required": ["handle"],
    }
    examples: ClassVar[tuple[Example, ...]] = (
        Example(
            call="bash_output(handle=\"bg_1\")",
            result="ready on http://localhost:3000\n(still running)",
        ),
    )

    def __init__(self, session: ShellSession) -> None:
        self._session = session

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        handle = require_str(args, "handle")
        job = self._session.jobs.get(handle)
        if job is None:
            known = sorted(self._session.jobs) or ["(none)"]
            raise ToolError(f"no background job {handle!r}. Known handles: {', '.join(known)}")

        # Yield once so the pump task can move whatever has arrived into the buffer.
        await asyncio.sleep(0)
        new_output = clamp_output(job.drain())
        running = job.process.returncode is None
        status = "(still running)" if running else f"(exited {job.process.returncode})"
        body = new_output.rstrip("\n") or "(no new output)"
        return ToolResult(ok=True, content=f"{body}\n{status}")
