"""bash: persistence, timeout, huge output, background mode, and the refusals.

The persistence test is the one that justifies the whole design. ``cd`` then ``pwd``
must print the new directory, because with a subprocess per command it would print the
old one — and a model that cannot rely on its own ``cd`` re-derives its environment on
every call and eventually gets it wrong.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from harness import call, context

from ronin.tools import BashOutputTool, BashTool, PersistentShell, ShellSession, clamp_output
from ronin.tools.base import ToolError
from ronin.tools.shell import MAX_OUTPUT_CHARS, check_command

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[ShellSession]:
    shell = ShellSession(shell=PersistentShell(cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}))
    try:
        yield shell
    finally:
        await shell.close()


# --------------------------------------------------------------------------- #
# Persistence — the reason this is one shell and not many
# --------------------------------------------------------------------------- #


async def test_cd_persists_across_calls(session: ShellSession, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    bash = BashTool(session)
    ctx = context(tmp_path)

    first = await call(bash, ctx, command="cd sub", description="Move into sub")
    assert first.ok, first.error
    second = await call(bash, ctx, command="pwd", description="Print the directory")
    assert second.ok
    assert second.content.strip().endswith("/sub")


async def test_exports_persist_across_calls(session: ShellSession, tmp_path: Path) -> None:
    bash = BashTool(session)
    ctx = context(tmp_path)
    await call(bash, ctx, command="export RONIN_TEST=persisted", description="Set a var")
    result = await call(bash, ctx, command='echo "$RONIN_TEST"', description="Read it back")
    assert result.content.strip() == "persisted"


async def test_a_shell_function_defined_once_survives(
    session: ShellSession, tmp_path: Path
) -> None:
    bash = BashTool(session)
    ctx = context(tmp_path)
    await call(bash, ctx, command="greet() { echo hello; }", description="Define a function")
    result = await call(bash, ctx, command="greet", description="Call it")
    assert result.content.strip() == "hello"


async def test_the_exit_code_is_reported_as_a_failure(
    session: ShellSession, tmp_path: Path
) -> None:
    """A subshell, because a bare `exit` would end the persistent shell."""
    result = await call(
        BashTool(session), context(tmp_path), command="(exit 3)", description="Fail on purpose"
    )
    assert not result.ok
    assert "exited 3" in result.error


async def test_a_bare_exit_is_refused_because_it_destroys_the_session(
    session: ShellSession, tmp_path: Path
) -> None:
    """Found by a test: `exit 3` looks like "return 3" and silently kills the shell."""
    result = await call(
        BashTool(session), context(tmp_path), command="exit 3", description="Return a code"
    )
    assert not result.ok
    assert "would end the persistent shell" in result.error
    assert "(exit 1)" in result.error, "point at the subshell form"


async def test_the_shell_is_respawned_if_it_dies_anyway(
    session: ShellSession, tmp_path: Path
) -> None:
    """`kill $$` gets past the word-level check; the next command must still work."""
    bash = BashTool(session)
    ctx = context(tmp_path)
    died = await call(bash, ctx, command="kill -9 $$", description="Kill the shell")
    assert not died.ok
    assert "exited unexpectedly" in died.error
    recovered = await call(bash, ctx, command="echo alive", description="Check")
    assert recovered.ok
    assert "alive" in recovered.content


async def test_a_nonzero_exit_still_returns_the_output(
    session: ShellSession, tmp_path: Path
) -> None:
    """The output is where the reason lives; dropping it on failure is useless."""
    result = await call(
        BashTool(session),
        context(tmp_path),
        command="echo 'the reason' >&2; false",
        description="Fail with a message",
    )
    assert not result.ok
    assert "the reason" in result.content


async def test_stderr_is_merged_into_the_output(session: ShellSession, tmp_path: Path) -> None:
    result = await call(
        BashTool(session),
        context(tmp_path),
        command="echo out; echo err >&2",
        description="Write to both streams",
    )
    assert "out" in result.content
    assert "err" in result.content


async def test_a_command_with_no_output_says_so(session: ShellSession, tmp_path: Path) -> None:
    result = await call(BashTool(session), context(tmp_path), command="true", description="No-op")
    assert result.ok
    assert result.content == "(no output)"


async def test_a_multiline_command_runs(session: ShellSession, tmp_path: Path) -> None:
    result = await call(
        BashTool(session),
        context(tmp_path),
        command="for i in 1 2 3; do echo n=$i; done",
        description="Loop three times",
    )
    assert result.content.count("n=") == 3


async def test_a_command_printing_the_sentinel_text_cannot_fake_completion(
    session: ShellSession, tmp_path: Path
) -> None:
    """The sentinel is a fresh uuid per shell, so echoing a guess does nothing."""
    bash = BashTool(session)
    ctx = context(tmp_path)
    result = await call(
        bash, ctx, command="echo __RONIN_deadbeef__ 0; echo real", description="Try to spoof"
    )
    assert result.ok
    assert "real" in result.content, "the real command must still have run to completion"


# --------------------------------------------------------------------------- #
# Timeout
# --------------------------------------------------------------------------- #


async def test_a_hanging_command_times_out_and_is_killed(
    session: ShellSession, tmp_path: Path
) -> None:
    result = await call(
        BashTool(session), context(tmp_path), command="sleep 30", description="Sleep", timeout=1
    )
    assert not result.ok
    assert "timed out after 1s" in result.error
    assert "run_in_background" in result.error, "point at the right tool for long jobs"


async def test_the_timeout_kills_child_processes_too(session: ShellSession, tmp_path: Path) -> None:
    """A command that spawned children leaves them holding the pipe; killing only
    bash waits forever on a read that will never finish."""
    marker = tmp_path / "child_still_running"
    command = f"( sleep 30; touch {marker} ) & sleep 30"
    result = await call(
        BashTool(session),
        context(tmp_path),
        command=command,
        description="Spawn a child",
        timeout=1,
    )
    assert not result.ok
    await asyncio.sleep(0.2)
    assert not marker.exists()


async def test_the_shell_recovers_after_a_timeout(session: ShellSession, tmp_path: Path) -> None:
    bash = BashTool(session)
    ctx = context(tmp_path)
    await call(bash, ctx, command="sleep 30", description="Hang", timeout=1)
    result = await call(bash, ctx, command="echo alive", description="Check the shell")
    assert result.ok
    assert "alive" in result.content


async def test_an_absurd_timeout_is_rejected(session: ShellSession, tmp_path: Path) -> None:
    result = await call(
        BashTool(session), context(tmp_path), command="true", description="x", timeout=99999
    )
    assert not result.ok
    assert "between 1 and" in result.error


# --------------------------------------------------------------------------- #
# Huge output
# --------------------------------------------------------------------------- #


async def test_huge_output_is_capped_keeping_head_and_tail(
    session: ShellSession, tmp_path: Path
) -> None:
    """The tail is where the traceback is; a head-only cap throws away the reason."""
    command = 'for i in $(seq 1 20000); do echo "line $i of output padding here"; done'
    result = await call(
        BashTool(session), context(tmp_path), command=command, description="Print a lot", timeout=60
    )
    assert result.ok
    assert len(result.content) <= MAX_OUTPUT_CHARS + 200
    assert "line 1 of output" in result.content, "head kept"
    assert "line 20000 of output" in result.content, "tail kept"
    assert "cut from the middle" in result.content


def test_clamp_output_is_a_no_op_under_the_limit() -> None:
    assert clamp_output("short") == "short"


def test_clamp_output_keeps_both_ends() -> None:
    text = "START" + ("x" * 100_000) + "END"
    clamped = clamp_output(text, limit=1000)
    assert clamped.startswith("START")
    assert clamped.endswith("END")
    assert "cut from the middle" in clamped


# --------------------------------------------------------------------------- #
# Background mode
# --------------------------------------------------------------------------- #


async def test_a_background_command_returns_a_handle_immediately(
    session: ShellSession, tmp_path: Path
) -> None:
    result = await call(
        BashTool(session),
        context(tmp_path),
        command="echo starting; sleep 30",
        description="Long job",
        run_in_background=True,
    )
    assert result.ok
    assert "handle='bg_1'" in result.content
    assert "bash_output" in result.content


async def test_bash_output_tails_a_background_command(
    session: ShellSession, tmp_path: Path
) -> None:
    bash = BashTool(session)
    tail = BashOutputTool(session)
    ctx = context(tmp_path)
    await call(
        bash,
        ctx,
        command="echo ready on port 3000; sleep 30",
        description="Fake dev server",
        run_in_background=True,
    )
    for _ in range(50):
        result = await call(tail, ctx, handle="bg_1")
        if "ready on port 3000" in result.content:
            break
        await asyncio.sleep(0.05)
    assert "ready on port 3000" in result.content
    assert "still running" in result.content


async def test_bash_output_returns_only_what_is_new(session: ShellSession, tmp_path: Path) -> None:
    bash = BashTool(session)
    tail = BashOutputTool(session)
    ctx = context(tmp_path)
    await call(bash, ctx, command="echo first; sleep 30", description="Job", run_in_background=True)
    for _ in range(50):
        first = await call(tail, ctx, handle="bg_1")
        if "first" in first.content:
            break
        await asyncio.sleep(0.05)
    second = await call(tail, ctx, handle="bg_1")
    assert "first" not in second.content
    assert "no new output" in second.content


async def test_bash_output_reports_a_finished_job(session: ShellSession, tmp_path: Path) -> None:
    bash = BashTool(session)
    tail = BashOutputTool(session)
    ctx = context(tmp_path)
    await call(bash, ctx, command="echo done", description="Quick job", run_in_background=True)
    for _ in range(50):
        result = await call(tail, ctx, handle="bg_1")
        if "exited" in result.content:
            break
        await asyncio.sleep(0.05)
    assert "exited 0" in result.content


async def test_an_unknown_handle_lists_the_known_ones(
    session: ShellSession, tmp_path: Path
) -> None:
    result = await call(BashOutputTool(session), context(tmp_path), handle="bg_99")
    assert not result.ok
    assert "no background job" in result.error
    assert "(none)" in result.error


# --------------------------------------------------------------------------- #
# Refusals — the redirects belong in bash's description and in its behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("grep -r foo .", "grep tool"),
        ("rg foo", "grep tool"),
        ("find . -name '*.py'", "glob tool"),
        ("cat README.md", "read tool"),
        ("head -20 file.txt", "read tool"),
        ("tail -f log.txt", "read tool"),
        ("ls -la", "ls tool"),
        ("egrep x .", "grep tool"),
    ],
)
def test_commands_with_a_better_tool_are_refused(command: str, expected: str) -> None:
    with pytest.raises(ToolError, match=expected):
        check_command(command)


def test_a_redirect_is_caught_in_a_later_segment() -> None:
    """`cd src && grep foo` must be caught; checking only the first word is not enough."""
    with pytest.raises(ToolError, match="grep tool"):
        check_command("cd src && grep foo .")


def test_a_redirect_is_caught_through_a_pipe() -> None:
    with pytest.raises(ToolError, match="read tool"):
        check_command("echo x | cat")


def test_a_redirect_is_caught_behind_sudo() -> None:
    with pytest.raises(ToolError, match="glob tool"):
        check_command("sudo find / -name passwd")


@pytest.mark.parametrize("command", ["vim file.py", "less log.txt", "top", "man bash"])
def test_interactive_programs_are_refused_because_they_would_hang(command: str) -> None:
    with pytest.raises(ToolError, match="interactive"):
        check_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -fr /",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "chmod -R 777 /",
        "echo x > /dev/sda",
    ],
)
def test_destructive_shapes_are_refused_outright(command: str) -> None:
    with pytest.raises(ToolError, match="refused"):
        check_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q",
        "git status",
        "npm ci",
        "rm -rf build/",
        "python -c 'print(1)'",
        "make test",
        "cd src && uv run mypy",
    ],
)
def test_ordinary_commands_are_allowed(command: str) -> None:
    check_command(command)


def test_an_empty_command_is_rejected() -> None:
    with pytest.raises(ToolError, match="empty"):
        check_command("   ")


async def test_the_refusal_reaches_the_model_as_a_value_not_an_exception(
    session: ShellSession, tmp_path: Path
) -> None:
    result = await call(
        BashTool(session), context(tmp_path), command="grep foo .", description="Search"
    )
    assert not result.ok
    assert "grep tool" in result.error


def test_bash_declares_itself_destructive_and_gated() -> None:
    from ronin.core.types import DangerLevel

    spec = BashTool(ShellSession(shell=PersistentShell(cwd=Path.cwd()))).spec()
    assert spec.danger_level is DangerLevel.DESTRUCTIVE
    assert spec.requires_approval


def test_the_bash_description_carries_the_tool_redirects() -> None:
    """The work order puts "use grep instead of bash grep" here specifically."""
    description = BashTool(ShellSession(shell=PersistentShell(cwd=Path.cwd()))).spec().description
    for expected in ("grep tool", "glob tool", "read tool", "ls tool", "run_in_background"):
        assert expected in description
