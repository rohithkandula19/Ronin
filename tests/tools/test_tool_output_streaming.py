"""Live tool output: from the shell's pipe to a ``ToolOutput`` event.

The friction is a long command — a test suite, a build — showing nothing until it
exits. The fix is a sink the tool may write to as it runs, which the loop forwards as
``ToolOutput`` events between the tool's ``ToolStart`` and its ``ToolEnd``.

Two properties matter more than the feature itself, and most of these tests are about
them: a registry that knows nothing about streaming must keep working untouched, and a
consumer that raises must not turn a working command into a failed one. Liveness is
never allowed to cost correctness.

Offline: the shell is real bash, everything else is scripted.
"""

from __future__ import annotations

import asyncio

import pytest

from ronin.core.loop import _execute_one, _execute_streaming
from ronin.core.protocols import StreamingToolRegistry
from ronin.core.types import DangerLevel, ToolResult, ToolSpec, ToolUse
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry
from ronin.tools.shell import PersistentShell


class _Recorder:
    """A tool that reports what it was handed, and streams if it was given a sink."""

    def __init__(self, chunks: tuple[str, ...] = ()) -> None:
        self.chunks = chunks
        self.saw_sink: bool | None = None

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="noisy",
            description="Prints as it goes.",
            danger_level=DangerLevel.READ_ONLY,
        )

    async def execute(self, arguments: object, ctx: ToolContext) -> ToolResult:
        self.saw_sink = ctx.on_output is not None
        for chunk in self.chunks:
            if ctx.on_output is not None:
                ctx.on_output(chunk)
            await asyncio.sleep(0)  # let the loop drain between chunks
        return ToolResult(ok=True, content="".join(self.chunks) or "done")


class _Legacy:
    """A registry from before streaming existed. It must keep working, untouched."""

    def specs(self) -> tuple[ToolSpec, ...]:
        return ()

    def get(self, name: str) -> ToolSpec | None:
        return None

    async def execute(self, use: ToolUse) -> ToolResult:
        return ToolResult(ok=True, content="legacy ran")


def _registry(tool: _Recorder, tmp_path: object) -> ToolRegistry:
    return ToolRegistry((tool,), ToolContext(root=tmp_path))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the capability protocol — opt-in, never required
# --------------------------------------------------------------------------- #


def test_the_real_registry_advertises_streaming(tmp_path: object) -> None:
    assert isinstance(_registry(_Recorder(), tmp_path), StreamingToolRegistry)


def test_a_registry_without_streaming_is_not_mistaken_for_one() -> None:
    assert not isinstance(_Legacy(), StreamingToolRegistry)


async def test_a_legacy_registry_still_runs_when_a_sink_is_offered() -> None:
    # The regression that matters: widening `execute` would make this call raise
    # TypeError, which becomes a failed ToolResult — a working tool silently stopping.
    result = await _execute_one(
        _Legacy(),
        ToolUse(id="t1", name="anything"),
        on_output=lambda _chunk: None,
    )
    assert result.ok and result.content == "legacy ran"


async def test_no_sink_means_the_tool_is_not_given_one(tmp_path: object) -> None:
    tool = _Recorder()
    await _registry(tool, tmp_path).execute(ToolUse(id="t1", name="noisy"))
    assert tool.saw_sink is False  # unchanged behaviour for every non-streaming path


async def test_the_sink_reaches_the_tool_through_the_context(tmp_path: object) -> None:
    tool = _Recorder()
    await _registry(tool, tmp_path).execute_streaming(
        ToolUse(id="t1", name="noisy"), lambda _chunk: None
    )
    assert tool.saw_sink is True


async def test_binding_a_sink_does_not_disturb_the_shared_context(tmp_path: object) -> None:
    # The copy must be shallow: `read_files` is how the write guard knows what was read,
    # and a per-call duplicate of that set would quietly weaken it.
    registry = _registry(_Recorder(), tmp_path)
    registry.ctx.read_files.add("/seen.py")
    await registry.execute_streaming(ToolUse(id="t1", name="noisy"), lambda _chunk: None)
    assert registry.ctx.on_output is None  # the shared ctx was never mutated
    assert "/seen.py" in registry.ctx.read_files


# --------------------------------------------------------------------------- #
# the loop turns chunks into events, in order, before the result
# --------------------------------------------------------------------------- #


async def test_chunks_arrive_as_events_while_the_tool_runs(tmp_path: object) -> None:
    tool = _Recorder(("collected 412 items\n", "..........\n", "412 passed\n"))
    produced: list[ToolResult] = []
    events = [
        event
        async for event in _execute_streaming(
            _registry(tool, tmp_path), ToolUse(id="t1", name="noisy"), produced
        )
    ]
    assert [event.chunk for event in events] == list(tool.chunks)
    assert all(event.tool_use_id == "t1" for event in events)
    assert produced[0].ok  # the result still lands, and carries the whole output
    assert "412 passed" in produced[0].content


async def test_a_tool_that_streams_nothing_yields_no_events(tmp_path: object) -> None:
    produced: list[ToolResult] = []
    events = [
        event
        async for event in _execute_streaming(
            _registry(_Recorder(), tmp_path), ToolUse(id="t1", name="noisy"), produced
        )
    ]
    assert events == []
    assert produced[0].ok


async def test_chunks_emitted_just_before_exit_are_not_lost(tmp_path: object) -> None:
    # The race the drain-after-finish exists for: output posted in the same tick the
    # tool returns must still be reported.
    tool = _Recorder(tuple(f"line {n}\n" for n in range(50)))
    produced: list[ToolResult] = []
    events = [
        event
        async for event in _execute_streaming(
            _registry(tool, tmp_path), ToolUse(id="t1", name="noisy"), produced
        )
    ]
    assert len(events) == 50
    assert events[-1].chunk == "line 49\n"


async def test_a_failing_tool_still_reports_what_it_printed(tmp_path: object) -> None:
    class Boom(_Recorder):
        async def execute(self, arguments: object, ctx: ToolContext) -> ToolResult:
            if ctx.on_output is not None:
                ctx.on_output("about to fail\n")
            await asyncio.sleep(0)
            raise RuntimeError("nope")

    produced: list[ToolResult] = []
    events = [
        event
        async for event in _execute_streaming(
            _registry(Boom(), tmp_path), ToolUse(id="t1", name="noisy"), produced
        )
    ]
    assert [event.chunk for event in events] == ["about to fail\n"]
    assert not produced[0].ok and "nope" in produced[0].error


# --------------------------------------------------------------------------- #
# the real shell
# --------------------------------------------------------------------------- #


async def test_bash_reports_its_lines_as_it_produces_them(tmp_path: object) -> None:
    seen: list[str] = []
    shell = PersistentShell(cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})  # type: ignore[arg-type]
    try:
        output, code = await shell.run(
            "printf 'one\\ntwo\\nthree\\n'", timeout=30.0, on_output=seen.append
        )
    finally:
        await shell.close()
    assert code == 0
    assert [line.strip() for line in seen] == ["one", "two", "three"]
    # The buffered return is unchanged — this is an addition, not a redirection.
    assert output.splitlines() == ["one", "two", "three"]


async def test_bash_without_a_sink_behaves_exactly_as_before(tmp_path: object) -> None:
    shell = PersistentShell(cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})  # type: ignore[arg-type]
    try:
        output, code = await shell.run("echo hello", timeout=30.0)
    finally:
        await shell.close()
    assert code == 0 and output.strip() == "hello"


async def test_a_sink_that_raises_does_not_fail_the_command(tmp_path: object) -> None:
    """A broken observer is not a tool failure — the command's result is unaffected."""

    def hostile(_chunk: str) -> None:
        raise RuntimeError("the UI blew up")

    shell = PersistentShell(cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})  # type: ignore[arg-type]
    try:
        output, code = await shell.run("printf 'a\\nb\\n'", timeout=30.0, on_output=hostile)
    finally:
        await shell.close()
    assert code == 0
    assert output.splitlines() == ["a", "b"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
