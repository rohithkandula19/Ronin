"""Hook-schema normalization: two dialects, one internal shape, and the outcomes.

Ronin's own ``hooks.json`` keys groups by event; a codex/OpenAI-style plugin ships a
flat list where each entry names its own event. :meth:`HookConfig.normalize` accepts
both and produces the same :class:`HookConfig`, so the gate never learns which dialect a
hook was written in. The firing tests then pin the two behaviours the spec calls out: a
``PreToolUse`` exit-2 blocks, and a ``PostToolUse`` formatter runs after the edit.
"""

from __future__ import annotations

import pytest

from ronin.agents.hooks import (
    HookCompletion,
    HookConfig,
    HookConfigError,
    HookEvent,
    HookInvocation,
    HookOutcome,
    HookRunner,
)

NESTED = {
    "PreToolUse": [{"matcher": "edit|write", "hooks": [{"command": "guard.sh"}]}],
    "PostToolUse": [{"matcher": "edit", "hooks": [{"command": "prettier --write"}]}],
}

FLAT = [
    {"event": "PreToolUse", "matcher": "edit|write", "command": "guard.sh"},
    {"event": "PostToolUse", "matcher": "edit", "command": "prettier --write"},
]

WRAPPED = {"hooks": FLAT}


class FakeProcess:
    """A hook launcher that records invocations and returns a scripted completion."""

    def __init__(self, completion: HookCompletion) -> None:
        self._completion = completion
        self.calls: list[HookInvocation] = []

    async def __call__(self, invocation: HookInvocation) -> HookCompletion:
        self.calls.append(invocation)
        return self._completion


# --------------------------------------------------------------------------- #
# normalization — both dialects reach the same internal shape
# --------------------------------------------------------------------------- #


def _summary(config: HookConfig) -> set[tuple[str, str, str]]:
    return {(spec.event.value, spec.matcher, spec.command) for spec in config.hooks}


def test_the_flat_and_nested_dialects_normalize_to_the_same_config() -> None:
    nested = HookConfig.normalize(NESTED)
    flat = HookConfig.normalize(FLAT)
    wrapped = HookConfig.normalize(WRAPPED)
    expected = {
        ("PreToolUse", "edit|write", "guard.sh"),
        ("PostToolUse", "edit", "prettier --write"),
    }
    assert _summary(nested) == expected
    assert _summary(flat) == expected
    assert _summary(wrapped) == expected


def test_normalize_dispatches_a_bare_list_to_the_flat_parser() -> None:
    config = HookConfig.normalize([{"event": "Stop", "command": "echo done"}])
    assert [spec.command for spec in config.hooks] == ["echo done"]


def test_a_flat_entry_missing_its_event_names_the_index() -> None:
    with pytest.raises(HookConfigError, match=r"hooks\[0\]: unknown or missing hook event"):
        HookConfig.normalize([{"command": "x"}])


def test_a_flat_entry_missing_its_command_is_named() -> None:
    with pytest.raises(HookConfigError, match="'command' is required"):
        HookConfig.normalize([{"event": "Stop"}])


def test_a_scalar_config_is_refused_with_its_type() -> None:
    with pytest.raises(HookConfigError, match="must be a list of entries or an object"):
        HookConfig.normalize("nope")


def test_nested_is_still_parsed_when_keys_are_all_events() -> None:
    config = HookConfig.normalize({"SessionStart": [{"hooks": [{"command": "git branch"}]}]})
    assert config.hooks[0].event is HookEvent.SESSION_START


# --------------------------------------------------------------------------- #
# firing — the two outcomes the spec names
# --------------------------------------------------------------------------- #


async def test_a_pretooluse_exit_2_blocks_the_action() -> None:
    """Exit 2 on a blocking event is a BLOCK, with the hook's stderr as the reason."""
    config = HookConfig.normalize(
        [{"event": "PreToolUse", "matcher": "edit", "command": "guard.sh"}]
    )
    process = FakeProcess(HookCompletion(exit_code=2, stderr="migrations are protected"))
    report = await HookRunner(config=config, process=process).fire(
        HookEvent.PRE_TOOL_USE, tool_name="edit", data={"arguments": {"path": "m/0001.py"}}
    )
    assert report.blocked
    assert "migrations are protected" in report.block_reason()
    assert process.calls, "the hook actually ran"


async def test_a_posttooluse_formatter_runs_after_the_edit() -> None:
    """The spec's PostToolUse case: a formatter fires on an edit and does not block."""
    config = HookConfig.normalize(WRAPPED)  # flat dialect, incl. the PostToolUse formatter
    process = FakeProcess(HookCompletion(exit_code=0, stdout="formatted 1 file"))
    report = await HookRunner(config=config, process=process).fire(
        HookEvent.POST_TOOL_USE, tool_name="edit", data={"arguments": {"path": "a.py"}}
    )
    assert not report.blocked
    assert process.calls and process.calls[0].command == "prettier --write"
    assert all(run.outcome is HookOutcome.OK for run in report.runs)


async def test_a_posttooluse_nonzero_is_a_warning_not_a_block() -> None:
    """Exit 2 after the fact cannot block — the file is already written."""
    config = HookConfig.normalize([{"event": "PostToolUse", "command": "check.sh"}])
    process = FakeProcess(HookCompletion(exit_code=2, stderr="late"))
    report = await HookRunner(config=config, process=process).fire(
        HookEvent.POST_TOOL_USE, tool_name="edit"
    )
    assert not report.blocked
    assert any(run.outcome is HookOutcome.WARN for run in report.runs)


async def test_the_matcher_survives_normalization_from_the_flat_shape() -> None:
    """A flat entry's matcher still gates which tool fires it."""
    config = HookConfig.normalize([{"event": "PreToolUse", "matcher": "write", "command": "g.sh"}])
    process = FakeProcess(HookCompletion(exit_code=0))
    fired = await HookRunner(config=config, process=process).fire(
        HookEvent.PRE_TOOL_USE, tool_name="read"
    )
    assert not fired.runs, "the matcher excluded 'read'"
    await HookRunner(config=config, process=process).fire(HookEvent.PRE_TOOL_USE, tool_name="write")
    assert process.calls, "the matcher included 'write'"
