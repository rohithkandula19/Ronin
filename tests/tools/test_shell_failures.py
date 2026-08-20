"""The bash tool's failure paths: a dead shell, a missing binary, a kill that fails.

`test_shell.py` covers the happy protocol thoroughly — persistence, timeout, huge
output, background handles, the refusals — but it skips its **entire module** when
bash is absent, and every remaining uncovered line was a branch reached only when
something goes wrong with the process itself. Those are exactly the branches worth
testing, because each one decides between an error a model can act on and a hang:

* the shell died mid-command — surfaces as a reset transport, not EOF
* `bash` is not on PATH at all
* `killpg` fails, so the timeout must still kill *something*
* a process group that has already exited

Nothing here spawns a shell. The failures are injected at the process boundary with
fakes, so these tests run on a machine with no bash and cannot leave a stray
process behind — which also means the "bash is not installed" branch is finally
testable somewhere other than a machine without bash.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from harness import call, context

from ronin.tools import BashTool, ShellSession
from ronin.tools.base import ToolError
from ronin.tools.shell import (
    PersistentShell,
    check_command,
    clamp_output,
)

# --------------------------------------------------------------------------- #
# Fakes at the process boundary
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class FakeStdin:
    """A pipe that can be made to fail the way a killed shell's does."""

    error: type[BaseException] | None = None
    closed: bool = False
    written: list[bytes] = field(default_factory=list)

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        if self.error is not None:
            raise self.error("the shell went away")

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class FakeStdout:
    """A stream that raises on read, or returns EOF."""

    error: type[BaseException] | None = None

    async def readline(self) -> bytes:
        if self.error is not None:
            raise self.error("transport reset")
        return b""


@dataclass(slots=True)
class FakeProcess:
    pid: int = 424242
    returncode: int | None = None
    stdin: Any = None
    stdout: Any = None
    killed: bool = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def shell_with(process: FakeProcess, tmp_path: Path) -> PersistentShell:
    """A shell wired to a fake process, bypassing `start()`."""
    shell = PersistentShell(cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})
    # Reaching into the private field is deliberate: the whole point is to put the
    # shell into a state a real one only reaches by dying at an awkward moment.
    shell._process = cast(Any, process)
    return shell


# --------------------------------------------------------------------------- #
# A shell that is not there
# --------------------------------------------------------------------------- #


async def test_writing_to_a_shell_that_never_started_is_a_named_error(tmp_path: Path) -> None:
    shell = PersistentShell(cwd=tmp_path)
    with pytest.raises(ToolError, match="not running"):
        await shell._write("echo hi\n")


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError])
async def test_a_broken_stdin_reports_the_shell_died_and_forgets_it(
    tmp_path: Path, failure: type[BaseException]
) -> None:
    """A dead shell must be *forgotten*, not just reported.

    Leaving `_process` set means the next call believes it has a live shell and
    writes into a closed pipe, so the second failure is less legible than the
    first. Clearing it makes the next `run` start a fresh shell.
    """
    process = FakeProcess(stdin=FakeStdin(error=failure))
    shell = shell_with(process, tmp_path)

    with pytest.raises(ToolError, match="shell"):
        await shell._write("echo hi\n")
    assert not shell.alive, "a dead shell must not still look alive"


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError])
async def test_a_reset_transport_while_reading_becomes_an_actionable_error(
    tmp_path: Path, failure: type[BaseException]
) -> None:
    """A killed shell surfaces as a reset transport rather than EOF.

    A bare `ConnectionResetError` reaching the model says nothing it can act on;
    the tool contract is a `ToolResult`/`ToolError` that names what happened.
    """
    stream = FakeStdout(error=failure)
    shell = shell_with(FakeProcess(stdout=stream), tmp_path)

    with pytest.raises(ToolError, match="shell"):
        await shell._read_until_sentinel(cast(Any, stream))
    assert not shell.alive


async def test_eof_while_reading_is_reported_rather_than_looped_on(tmp_path: Path) -> None:
    """An empty read means the shell exited. Looping here would hang the turn."""
    stream = FakeStdout()
    shell = shell_with(FakeProcess(stdout=stream), tmp_path)

    with pytest.raises(ToolError, match="shell"):
        await shell._read_until_sentinel(cast(Any, stream))
    assert not shell.alive


# --------------------------------------------------------------------------- #
# No bash at all
# --------------------------------------------------------------------------- #


async def test_a_missing_bash_binary_is_a_tool_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch that could previously only run on a machine without bash — so on
    every CI runner it was unreachable, and on a bash-less one the whole module
    skipped instead of exercising it."""
    monkeypatch.setattr("ronin.tools.shell.shutil.which", lambda _name: None)
    shell = PersistentShell(cwd=tmp_path)
    with pytest.raises(ToolError, match="bash is not installed"):
        await shell.start()


async def test_background_mode_also_refuses_when_bash_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Background mode spawns its own `bash -c` rather than using the persistent
    shell, so it has its own copy of this guard — and a second copy is a second
    thing that can rot. Without a test, removing it would turn a missing binary
    into a `TypeError` on `None` from inside a subprocess spawn."""
    monkeypatch.setattr("ronin.tools.shell.shutil.which", lambda _name: None)
    session = ShellSession(shell=PersistentShell(cwd=tmp_path))
    result = await call(
        BashTool(session),
        context(tmp_path),
        command="sleep 30",
        description="Long job",
        run_in_background=True,
    )
    assert not result.ok
    assert "bash is not installed" in (result.error or "")
    assert session.jobs == {}, "a job was registered for a shell that cannot start"


# --------------------------------------------------------------------------- #
# Killing a process group
# --------------------------------------------------------------------------- #


async def test_killing_a_group_with_no_process_reports_nothing_to_kill(tmp_path: Path) -> None:
    shell = PersistentShell(cwd=tmp_path)
    assert await shell._kill_group() is False


async def test_killing_a_group_that_already_exited_reports_nothing_to_kill(
    tmp_path: Path,
) -> None:
    shell = shell_with(FakeProcess(returncode=0), tmp_path)
    assert await shell._kill_group() is False


async def test_a_failing_killpg_still_kills_the_shell_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback that keeps a timeout from being a lie.

    `killpg` can fail — the group is gone, or the platform refuses. If the tool
    reported "timed out and was killed" while the process kept running, the user
    would be told the machine was quiet when it was not. So the single process is
    killed directly and the return value says children were *not* reaped.
    """

    def refuse(*_args: object) -> int:
        raise ProcessLookupError("no such group")

    monkeypatch.setattr(os, "getpgid", refuse)
    process = FakeProcess()
    shell = shell_with(process, tmp_path)

    assert await shell._kill_group() is False
    assert process.killed, "the shell survived a failed group kill"


@pytest.mark.parametrize("failure", [ProcessLookupError, PermissionError, OSError])
async def test_close_falls_back_to_killing_the_process_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    """Every way `killpg` can refuse, and `close` must still not leak a process.

    `PermissionError` is the real one on a locked-down host; `OSError` is the
    catch-all that keeps a platform quirk from turning session teardown into a
    traceback the user sees on exit.
    """

    def refuse(*_args: object) -> int:
        raise failure("nope")

    monkeypatch.setattr(os, "killpg", refuse)
    stdin = FakeStdin()
    process = FakeProcess(stdin=stdin)
    shell = shell_with(process, tmp_path)

    await shell.close()
    assert process.killed
    assert stdin.closed, "the pipe was left open"


async def test_closing_an_already_dead_shell_is_a_no_op(tmp_path: Path) -> None:
    process = FakeProcess(returncode=0)
    shell = shell_with(process, tmp_path)
    await shell.close()
    assert not process.killed, "a finished process must not be killed again"


async def test_killpg_is_called_with_the_group_not_the_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinction the timeout depends on.

    Signalling the pid kills bash and leaves whatever it spawned holding the pipe,
    which is the hang this design exists to prevent. Asserted on the arguments
    rather than the outcome, because a fake cannot have real children.
    """
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: seen.append((pgid, sig)))

    process = FakeProcess(pid=4242)
    shell = shell_with(process, tmp_path)
    assert await shell._kill_group() is True
    assert seen == [(5242, signal.SIGKILL)]
    assert not process.killed, "the direct kill is only the fallback"


# --------------------------------------------------------------------------- #
# The sentinel and the command screen
# --------------------------------------------------------------------------- #


def test_each_shell_gets_its_own_unguessable_sentinel(tmp_path: Path) -> None:
    """A fresh uuid per shell is what stops a command that *prints* the sentinel
    from faking a completion."""
    first = PersistentShell(cwd=tmp_path).sentinel
    second = PersistentShell(cwd=tmp_path).sentinel
    assert first.startswith("__RONIN_") and first.endswith("__")
    assert first != second


@pytest.mark.parametrize(
    "command",
    ["echo a ;; echo b", "echo a ; ; echo b", "echo a || || echo b", "echo a | | echo b"],
    ids=["double-semi", "spaced-semi", "double-or", "double-pipe"],
)
def test_empty_segments_are_skipped_rather_than_refused(command: str) -> None:
    """Malformed-but-harmless separators must not be read as a denied command.

    An empty segment has no head word to check; treating it as suspicious would
    refuse shell that bash itself accepts.
    """
    check_command(command)


@pytest.mark.parametrize("command", ["sudo", "env", "time", "nohup", "sudo env time nohup"])
def test_a_bare_privilege_prefix_with_no_command_is_not_refused(command: str) -> None:
    """The prefix skip walks past `sudo`/`env`/`time`/`nohup` to find the real head
    word — so `sudo find /` is still caught. With nothing after them there is no
    head word to judge, and inventing a refusal would be a false positive."""
    check_command(command)


def test_a_denied_command_behind_a_privilege_prefix_is_still_caught() -> None:
    """The reason the skip exists, checked in the direction that matters."""
    with pytest.raises(ToolError, match="instead of `find`"):
        check_command("sudo find / -name '*.py'")


def test_clamp_keeps_both_ends_and_names_what_it_cut() -> None:
    text = "A" * 500 + "B" * 500
    clamped = clamp_output(text, limit=100)
    assert clamped.startswith("A")
    assert clamped.rstrip().endswith("B")
    assert "characters cut from the middle" in clamped
