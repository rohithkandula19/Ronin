"""hooks: exit 2 blocks, everything else does not, and a timeout takes its children.

Most of this suite injects the subprocess runner, because the interesting logic is
the decision table and nothing is learned by paying for a fork per case. The last
section runs real ``/bin/sh`` one-liners — local, offline, no keys — because argv
construction, stdin delivery and exit-code propagation are precisely what a mock
would paper over.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from agents_harness import FakeProcess

from ronin.agents.hooks import (
    BLOCK_EXIT_CODE,
    MAX_HOOK_OUTPUT_CHARS,
    HookCompletion,
    HookConfig,
    HookConfigError,
    HookEvent,
    HookInvocation,
    HookOutcome,
    HookReport,
    HookRun,
    HookRunner,
    HookSpec,
    event_payload,
    load_hook_config,
    run_shell_hook,
)

WORKED_EXAMPLE = {
    "PreToolUse": [
        {
            "matcher": "write|edit|multi_edit",
            "hooks": [
                {
                    "command": "scripts/protect-migrations.sh",
                    "timeout": 5,
                    "name": "no-migrations",
                    "block_on_timeout": True,
                }
            ],
        }
    ],
    "PostToolUse": [{"matcher": "edit|write", "hooks": [{"command": "npx prettier --write ."}]}],
    "SessionStart": [{"hooks": [{"command": "git rev-parse --abbrev-ref HEAD"}]}],
}


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("matcher", "tool", "expected"),
    [
        ("*", "write", True),
        ("*", None, True),
        ("write", "write", True),
        ("write", "read", False),
        ("write|edit", "edit", True),
        ("write|edit", "multi_edit", False),
        ("bash*", "bash_output", True),
        ("write", None, False),
    ],
)
def test_a_matcher_selects_the_tools_it_names(
    matcher: str, tool: str | None, expected: bool
) -> None:
    spec = HookSpec(event=HookEvent.PRE_TOOL_USE, command="true", matcher=matcher)
    assert spec.matches(tool) is expected


def test_a_matcher_on_an_event_with_no_tool_name_is_a_config_error() -> None:
    """It would never fire, and a hook that never fires looks like a hook that did."""
    with pytest.raises(ValueError, match="carries no tool name"):
        HookSpec(event=HookEvent.STOP, command="true", matcher="write")


def test_hooks_fire_in_config_order() -> None:
    config = HookConfig(
        hooks=(
            HookSpec(event=HookEvent.PRE_TOOL_USE, command="first"),
            HookSpec(event=HookEvent.PRE_TOOL_USE, command="second", matcher="write"),
            HookSpec(event=HookEvent.POST_TOOL_USE, command="other"),
        )
    )
    assert [s.command for s in config.for_event(HookEvent.PRE_TOOL_USE, "write")] == [
        "first",
        "second",
    ]
    assert [s.command for s in config.for_event(HookEvent.PRE_TOOL_USE, "read")] == ["first"]


# --------------------------------------------------------------------------- #
# The decision table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("event", "completion", "block_on_timeout", "expected"),
    [
        (HookEvent.PRE_TOOL_USE, HookCompletion(exit_code=0), False, HookOutcome.OK),
        (HookEvent.PRE_TOOL_USE, HookCompletion(exit_code=2), False, HookOutcome.BLOCK),
        (HookEvent.USER_PROMPT_SUBMIT, HookCompletion(exit_code=2), False, HookOutcome.BLOCK),
        (HookEvent.PRE_TOOL_USE, HookCompletion(exit_code=1), False, HookOutcome.WARN),
        (HookEvent.PRE_TOOL_USE, HookCompletion(exit_code=127), False, HookOutcome.WARN),
        (HookEvent.POST_TOOL_USE, HookCompletion(exit_code=2), False, HookOutcome.WARN),
        (HookEvent.STOP, HookCompletion(exit_code=2), False, HookOutcome.WARN),
        (
            HookEvent.PRE_TOOL_USE,
            HookCompletion(exit_code=-1, timed_out=True),
            False,
            HookOutcome.WARN,
        ),
        (
            HookEvent.PRE_TOOL_USE,
            HookCompletion(exit_code=-1, timed_out=True),
            True,
            HookOutcome.BLOCK,
        ),
    ],
)
def test_only_exit_two_before_the_fact_blocks(
    event: HookEvent,
    completion: HookCompletion,
    block_on_timeout: bool,
    expected: HookOutcome,
) -> None:
    """Exit 2 blocks; any other non-zero warns; a timeout warns unless configured."""
    run = HookRun(
        spec=HookSpec(event=event, command="c", block_on_timeout=block_on_timeout),
        completion=completion,
    )
    assert run.outcome is expected


def test_a_block_reason_carries_the_hook_stderr_to_the_model() -> None:
    report = HookReport(
        event=HookEvent.PRE_TOOL_USE,
        tool_name="write",
        runs=(
            HookRun(
                spec=HookSpec(event=HookEvent.PRE_TOOL_USE, command="c", name="no-migrations"),
                completion=HookCompletion(exit_code=2, stderr="migrations are owned by the DBA"),
            ),
        ),
    )
    assert report.blocked
    reason = report.block_reason()
    assert "no-migrations" in reason
    assert "migrations are owned by the DBA" in reason
    assert "Do not retry the same call" in reason
    result = report.as_tool_result()
    assert result is not None
    assert not result.ok
    assert result.error == reason


def test_a_silent_refusal_still_produces_a_usable_reason() -> None:
    """A block the model cannot read is a block it cannot comply with."""
    report = HookReport(
        event=HookEvent.PRE_TOOL_USE,
        runs=(
            HookRun(
                spec=HookSpec(event=HookEvent.PRE_TOOL_USE, command="check.sh"),
                completion=HookCompletion(exit_code=2),
            ),
        ),
    )
    assert "check.sh" in report.block_reason()
    assert "printed nothing" in report.block_reason()


def test_both_refusals_are_reported_when_two_hooks_block() -> None:
    runs = tuple(
        HookRun(
            spec=HookSpec(event=HookEvent.PRE_TOOL_USE, command=f"c{n}", name=f"hook{n}"),
            completion=HookCompletion(exit_code=2, stderr=f"reason {n}"),
        )
        for n in (1, 2)
    )
    reason = HookReport(event=HookEvent.PRE_TOOL_USE, runs=runs).block_reason()
    assert "reason 1" in reason
    assert "reason 2" in reason


def test_nothing_blocking_means_no_reason_and_no_tool_result() -> None:
    report = HookReport(
        event=HookEvent.POST_TOOL_USE,
        runs=(
            HookRun(
                spec=HookSpec(event=HookEvent.POST_TOOL_USE, command="c"),
                completion=HookCompletion(exit_code=0, stdout="formatted 3 files"),
            ),
        ),
    )
    assert report.block_reason() == ""
    assert report.as_tool_result() is None
    assert report.context() == "formatted 3 files"


def test_a_warning_is_reported_without_stopping_anything() -> None:
    report = HookReport(
        event=HookEvent.POST_TOOL_USE,
        runs=(
            HookRun(
                spec=HookSpec(event=HookEvent.POST_TOOL_USE, command="prettier", name="fmt"),
                completion=HookCompletion(exit_code=127, stderr="prettier: not found"),
            ),
        ),
    )
    assert not report.blocked
    assert report.warnings() == ("fmt: prettier: not found",)


def test_hook_output_is_capped_with_a_marker_naming_what_was_cut() -> None:
    run = HookRun(
        spec=HookSpec(event=HookEvent.PRE_TOOL_USE, command="noisy", name="noisy"),
        completion=HookCompletion(exit_code=2, stderr="x" * (MAX_HOOK_OUTPUT_CHARS + 500)),
    )
    message = run.message()
    assert "…[truncated: 500 chars cut from hook noisy stderr]" in message
    assert len(message) < MAX_HOOK_OUTPUT_CHARS + 200


# --------------------------------------------------------------------------- #
# Firing
# --------------------------------------------------------------------------- #


async def test_the_event_reaches_the_hook_as_sorted_json_on_stdin() -> None:
    process = FakeProcess()
    runner = HookRunner(
        config=HookConfig(hooks=(HookSpec(event=HookEvent.PRE_TOOL_USE, command="c"),)),
        process=process,
    )
    await runner.fire(
        HookEvent.PRE_TOOL_USE,
        tool_name="write",
        data={"arguments": {"path": "src/app.py"}},
    )
    payload = json.loads(process.calls[0].payload)
    assert payload == {
        "event": "PreToolUse",
        "tool_name": "write",
        "arguments": {"path": "src/app.py"},
    }
    assert process.calls[0].payload == event_payload(
        HookEvent.PRE_TOOL_USE, tool_name="write", data={"arguments": {"path": "src/app.py"}}
    )


async def test_firing_an_event_with_no_hooks_runs_nothing() -> None:
    process = FakeProcess()
    runner = HookRunner(config=HookConfig(), process=process)
    report = await runner.fire(HookEvent.STOP)
    assert report.runs == ()
    assert not report.blocked
    assert process.calls == []


async def test_every_matching_hook_runs_even_after_one_blocks() -> None:
    """Two reasons are more useful than a race, and the tool is not running either way."""
    process = FakeProcess(
        {
            "deny": HookCompletion(exit_code=2, stderr="nope"),
            "audit": HookCompletion(exit_code=0, stdout="logged"),
        }
    )
    runner = HookRunner(
        config=HookConfig(
            hooks=(
                HookSpec(event=HookEvent.PRE_TOOL_USE, command="deny"),
                HookSpec(event=HookEvent.PRE_TOOL_USE, command="audit"),
            )
        ),
        process=process,
    )
    report = await runner.fire(HookEvent.PRE_TOOL_USE, tool_name="write")
    assert list(process.commands) == ["deny", "audit"]
    assert report.blocked
    assert report.context() == "logged"


async def test_a_non_matching_hook_is_not_invoked() -> None:
    process = FakeProcess()
    runner = HookRunner(
        config=HookConfig(
            hooks=(HookSpec(event=HookEvent.PRE_TOOL_USE, command="c", matcher="write"),)
        ),
        process=process,
    )
    report = await runner.fire(HookEvent.PRE_TOOL_USE, tool_name="read")
    assert process.calls == []
    assert report.runs == ()


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_the_documented_worked_example_parses() -> None:
    config = HookConfig.from_mapping(WORKED_EXAMPLE)
    pre = config.for_event(HookEvent.PRE_TOOL_USE, "multi_edit")
    assert [spec.name for spec in pre] == ["no-migrations"]
    assert pre[0].timeout_seconds == 5.0
    assert pre[0].block_on_timeout is True
    assert config.for_event(HookEvent.PRE_TOOL_USE, "read") == ()
    assert len(config.for_event(HookEvent.SESSION_START)) == 1


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({"BeforeEverything": []}, "unknown hook event"),
        ({"PreToolUse": {"matcher": "*"}}, "expected a list"),
        ({"PreToolUse": ["nope"]}, "expected an object"),
        ({"PreToolUse": [{"matcher": "*"}]}, "'hooks' must be a list"),
        ({"PreToolUse": [{"hooks": [{}]}]}, "'command' is required"),
        ({"PreToolUse": [{"hooks": [{"command": "  "}]}]}, "'command' is required"),
        ({"PreToolUse": [{"hooks": [{"command": "c", "timeout": "soon"}]}]}, "'timeout'"),
        ({"PreToolUse": [{"hooks": [{"command": "c", "timeout": 0}]}]}, "must be positive"),
        ({"Stop": [{"matcher": "write", "hooks": [{"command": "c"}]}]}, "carries no tool name"),
    ],
)
def test_a_broken_config_says_which_key_is_wrong(data: object, needle: str) -> None:
    with pytest.raises(HookConfigError, match=needle):
        HookConfig.from_mapping(data)  # type: ignore[arg-type]  # the point is bad input


def test_no_hooks_file_means_no_hooks(tmp_path: Path) -> None:
    assert load_hook_config(tmp_path / "hooks.json").hooks == ()


def test_an_unparseable_hooks_file_is_loud(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HookConfigError, match="not valid JSON"):
        load_hook_config(path)


def test_a_hooks_file_round_trips_from_json(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(WORKED_EXAMPLE), encoding="utf-8")
    assert len(load_hook_config(path).hooks) == 3


# --------------------------------------------------------------------------- #
# Really executing — one shell, deliberately
# --------------------------------------------------------------------------- #


async def test_a_real_shell_hook_blocks_with_exit_two_and_its_stderr(tmp_path: Path) -> None:
    """The whole feature, end to end through a real fork/exec.

    The hook reads the event JSON on stdin, greps it, and refuses. Everything a mock
    would have hidden — argv, stdin plumbing, the exit code — is real here.
    """
    command = (
        'if grep -q migrations; then echo "migrations are owned by the DBA" >&2; '
        "exit 2; else exit 0; fi"
    )
    runner = HookRunner(
        config=HookConfig(
            hooks=(
                HookSpec(
                    event=HookEvent.PRE_TOOL_USE,
                    command=command,
                    matcher="write",
                    name="no-migrations",
                    timeout_seconds=10.0,
                ),
            )
        ),
        cwd=str(tmp_path),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )

    allowed = await runner.fire(
        HookEvent.PRE_TOOL_USE, tool_name="write", data={"arguments": {"path": "src/app.py"}}
    )
    assert not allowed.blocked
    assert allowed.runs[0].completion.exit_code == 0

    refused = await runner.fire(
        HookEvent.PRE_TOOL_USE,
        tool_name="write",
        data={"arguments": {"path": "migrations/003.sql"}},
    )
    assert refused.blocked
    assert refused.runs[0].completion.exit_code == BLOCK_EXIT_CODE
    assert "owned by the DBA" in refused.block_reason()


async def test_a_real_hook_can_read_the_payload_it_was_given(tmp_path: Path) -> None:
    completion = await run_shell_hook(
        HookInvocation(
            command="cat",
            payload=event_payload(HookEvent.SESSION_START, data={"cwd": "/x"}),
            timeout_seconds=10.0,
            cwd=str(tmp_path),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    )
    assert completion.exit_code == 0
    assert json.loads(completion.stdout) == {"cwd": "/x", "event": "SessionStart"}


async def test_a_slow_hook_times_out_as_a_warning_rather_than_wedging_the_agent(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    completion = await run_shell_hook(
        HookInvocation(
            command="sleep 30",
            payload="{}",
            timeout_seconds=0.3,
            cwd=str(tmp_path),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    )
    assert completion.timed_out
    assert time.monotonic() - started < 10
    run = HookRun(
        spec=HookSpec(event=HookEvent.PRE_TOOL_USE, command="sleep 30"),
        completion=completion,
    )
    assert run.outcome is HookOutcome.WARN
    assert "timed out" in run.message()


async def test_a_timeout_kills_the_children_the_hook_spawned(tmp_path: Path) -> None:
    """A hook that backgrounds a watcher must not outlive its own deadline."""
    pidfile = tmp_path / "child.pid"
    completion = await run_shell_hook(
        HookInvocation(
            command=f"sleep 30 & echo $! > {pidfile}; sleep 30",
            payload="{}",
            timeout_seconds=0.5,
            cwd=str(tmp_path),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    )
    assert completion.timed_out
    child = int(pidfile.read_text().strip())
    for _ in range(50):
        if not _alive(child):
            break
        time.sleep(0.05)
    assert not _alive(child), f"grandchild {child} outlived the hook's process group"


def _alive(pid: int) -> bool:
    """Whether ``pid`` still exists. A zombie counts as gone: it cannot run."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _state(pid) != "Z"


def _state(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return "Z"
    return stat.rsplit(") ", 1)[-1].split(" ", 1)[0]
