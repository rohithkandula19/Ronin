"""``python -m ronin.cli.demo`` — seven subsystems behaving like one program.

Offline, self-contained, in a temporary directory, with a scripted model. Nothing
here reaches the network and nothing is mocked above the provider seam: the
workspace loader, the settings layers, the repo map, the policy engine, the gated
registry, the real file tools, the shadow-git checkpoint store, the compaction
folder, the verify planner, the repair loop, the transcript writer and the replay
reader are all the shipped code.

What it walks through, in the order a session does it:

1. a workspace **loaded** off disk, with every degradation named as a note;
2. a **gated** tool call — a real ``write``, refused until the file has been read,
   then decided by the policy engine, with the decision, its reason and the mode
   relaxation that let it through all in the audit trail rather than implicit;
3. a **checkpoint** taken *before* the first mutating call, in ``.ronin/checkpoints``,
   leaving the user's own git index untouched;
4. a **verify failure fed back as a tool result** — not as a user message — and a
   **repair attempt** that gives up honestly when the failure signature stops moving;
5. a **compaction** that folds the middle, keeps the most recent tool result per file
   path in full, and says so where the user can see it;
6. the whole session **written to a transcript and replayed** with no unpaired tool
   calls.

Every section prints what it proves, and the exit code is non-zero if any of those
claims turns out to be false — a demo that cannot fail is a screenshot.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.types import (
    Event,
    Message,
    Mode,
    Role,
    Text,
    ToolEnd,
    ToolUse,
    unpaired_tool_uses,
)
from ..persistence.resume import resume_session
from ..persistence.transcript import list_sessions
from ..providers.base import ModelClient as ProviderClient
from ..providers.router import ModelSpec, Router, RouterConfig
from ..providers.router import Role as ModelRole
from ..providers.types import (
    Capabilities,
    Completed,
    ModelDelta,
    ModelRequest,
    TextDelta,
    Usage,
)
from ..safety.policy import Answer, Outcome
from ..verify.runner import CommandOutcome
from .spine import Paths
from .stream import CHECKPOINT_STEP, VERIFY_STEP, Conversation
from .wire import build_runtime, load_workspace

RONIN_MD = """\
# widget

A tiny library that rounds numbers.

## Commands
- test: `pytest -q`

## Conventions
- every public function has a docstring
"""

WIDGET = '''\
"""Rounding."""


def widget(x: float) -> float:
    """Round to two places."""
    return round(x, 2)
'''

TEST_WIDGET = """\
from src.widget import widget


def test_widget_rounds() -> None:
    assert widget(1.005) == 1.01
"""

PYPROJECT = """\
[project]
name = "widget"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
"""

#: A realistic failing pytest run. Reused below with cosmetic differences only —
#: a moved line number and a different duration — to show the repair loop reading
#: them as the same failure instead of as progress.
PYTEST_FAILURE = """\
============================= test session starts ==============================
platform linux -- Python 3.11.9, pytest-8.3.2
rootdir: /tmp/pytest-of-demo/pytest-4/widget
collected 1 item

tests/test_widget.py F                                                   [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/test_widget.py:5: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/test_widget.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
========================= 1 failed in 0.11s ====================================
"""

PYTEST_FAILURE_AGAIN = PYTEST_FAILURE.replace("test_widget.py:5", "test_widget.py:7").replace(
    "in 0.11s", "in 0.09s"
)

NATIVE = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=False,
    max_context=200_000,
    vision=False,
)


def _heading(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 74 - len(title)))


def _bullet(text: str) -> None:
    print(f"  {text}")


# --------------------------------------------------------------------------- #
# The two doubles: a scripted provider, and a human who says yes
# --------------------------------------------------------------------------- #


class ScriptedProvider:
    """A provider client with a script. The only fake above the filesystem.

    Repeats its last response once the script is exhausted, on purpose: the exact
    number of turns a capped repair loop takes is the repair module's business, and a
    demo that dies when that number changes by one is a demo nobody keeps running.
    """

    def __init__(self, responses: Sequence[Sequence[ModelDelta]]) -> None:
        self._responses = [tuple(deltas) for deltas in responses]
        self.calls = 0

    def capabilities(self) -> Capabilities:
        return NATIVE

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        del req
        chosen = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return self._replay(chosen)

    async def _replay(self, deltas: Sequence[ModelDelta]) -> AsyncIterator[ModelDelta]:
        for delta in deltas:
            await asyncio.sleep(0)
            yield delta


def says(text: str) -> tuple[ModelDelta, ...]:
    return (
        TextDelta(text),
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            usage=Usage(input_tokens=20, output_tokens=8),
        ),
    )


def calls(name: str, arguments: Mapping[str, Any], *, use_id: str) -> tuple[ModelDelta, ...]:
    return (
        Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(ToolUse(id=use_id, name=name, arguments=dict(arguments)),),
            ),
            usage=Usage(input_tokens=20, output_tokens=8),
        ),
    )


def scripted_router(responses: Sequence[Sequence[ModelDelta]]) -> Router:
    """A real router — retry ladder, ``LoopClient``, prefix assembly — over the fake."""
    spec = ModelSpec(name="scripted", provider="demo", model="demo-1")
    config = RouterConfig(roles={ModelRole.MAIN: "scripted"}, models={"scripted": spec})
    provider = ScriptedProvider(responses)

    def build(_spec: ModelSpec, **_kwargs: object) -> ProviderClient:
        return provider

    return Router(config, build=build)


class YesAsker:
    """A human who approves. Named, so it is obvious where consent came from."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, request: Any) -> Answer:
        self.asked.append(request.rendered)
        return Answer(outcome=Outcome.YES_ONCE, feedback="approved in the demo")


class ScriptedCommands:
    """The subprocess seam. Two failing pytest runs, then whatever the last one was."""

    def __init__(self, outputs: Sequence[tuple[int, str]]) -> None:
        self._outputs = list(outputs)
        self.argv_log: list[tuple[str, ...]] = []

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        self.argv_log.append(tuple(argv))
        code, output = self._outputs[min(len(self.argv_log) - 1, len(self._outputs) - 1)]
        return CommandOutcome(argv=tuple(argv), exit_code=code, output=output)


async def summarize(prompt: str) -> str:
    """The compaction summarizer, on the fast role in a real session. Scripted here."""
    del prompt
    return (
        "## Goal\nadd a helper to widget.py and keep the suite green\n\n"
        "## Files touched\n- src/widget.py — read, then rewritten with a helper\n\n"
        "## Learned\nthe rounding test asserts banker's rounding\n\n"
        "## Failed\nthe first two edits did not change the failure at all\n\n"
        "## Remaining\nfind out why round() disagrees with the assertion\n"
    )


# --------------------------------------------------------------------------- #
# The workspace
# --------------------------------------------------------------------------- #


def build_workspace(root: Path) -> bool:
    """Write a small but real project. Returns whether git is available."""
    (root / "RONIN.md").write_text(RONIN_MD, encoding="utf-8", newline="\n")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8", newline="\n")
    (root / "src").mkdir()
    (root / "src" / "widget.py").write_text(WIDGET, encoding="utf-8", newline="\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_widget.py").write_text(TEST_WIDGET, encoding="utf-8", newline="\n")
    try:
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def script() -> list[tuple[ModelDelta, ...]]:
    """The whole session, as the model's side of it.

    ``write`` is refused on a file that has not been read this session, so the first
    thing the script does is read — exactly what a real model has to do, and the
    guard that prevents most destructive edits.
    """
    edit = WIDGET.replace(
        "    return round(x, 2)\n",
        '    return round(x, 2)\n\n\ndef helper() -> int:\n    """Help."""\n    return 1\n',
    )
    return [
        calls("read", {"path": "src/widget.py"}, use_id="d1"),
        calls("write", {"path": "src/widget.py", "content": edit}, use_id="d2"),
        says("added a helper to src/widget.py."),
        calls(
            "write",
            {"path": "src/widget.py", "content": edit.replace("return 1", "return 2")},
            use_id="d3",
        ),
        says("tried changing the return value."),
        calls(
            "write",
            {"path": "src/widget.py", "content": edit.replace("return 1", "return 3")},
            use_id="d4",
        ),
        says("tried again."),
        says("src/widget.py rounds with round(), which is banker's rounding."),
    ]


# --------------------------------------------------------------------------- #
# The sections
# --------------------------------------------------------------------------- #


async def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        home = root / "home"
        home.mkdir()
        has_git = build_workspace(root)

        # ------------------------------------------------------------- loading
        _heading("1. the workspace, loaded")
        paths = Paths(workspace_root=root, home=home, cwd=root)
        loaded = load_workspace(paths, flags={"mode": Mode.AUTO_EDIT.value}, environ={})
        _bullet(f"mode              {loaded.mode.value}")
        _bullet(
            f"memory            {len(loaded.memory.files)} file(s): "
            f"{', '.join(loaded.memory.paths()) or 'none'}"
        )
        _bullet(
            f"repo map          {loaded.repo_map.marker() or 'empty'} "
            f"({len(loaded.repo_map.entries)} file(s), parser "
            f"{loaded.repo_map.parser_name or 'none'})"
        )
        detected = ", ".join(kind.value for kind in loaded.verify.available)
        _bullet(f"verify            {detected or 'nothing detected'}")
        _bullet(
            f"pinned prefix     ~{loaded.pinned_prefix_tokens():,} tokens "
            f"(RONIN.md + repo map; the part of the window that never shrinks)"
        )
        if loaded.notes:
            print()
            _bullet("notes — every degradation named, none of them fatal:")
            for note in loaded.notes:
                _bullet(f"    {note.line()}")
        else:
            _bullet("notes             none: everything loaded")
        if loaded.verify.test is None:
            print("  FAILED: pytest was not detected from pyproject.toml")
            failures += 1

        # ------------------------------------------------------------ assembly
        asker = YesAsker()
        commands = ScriptedCommands([(1, PYTEST_FAILURE), (1, PYTEST_FAILURE_AGAIN)])
        router = scripted_router(script())
        runtime = await build_runtime(
            loaded,
            router,
            session_id="demo",
            asker=asker,
            connect_mcp=False,
            # Small enough that a handful of turns reaches the trigger. A real
            # session uses the model's own window.
            context_window=2_500,
        )
        conversation = Conversation(command=commands)

        try:
            # ------------------------------------------- the mutating turn
            _heading("2. one mutating turn: gate, checkpoint, verify, repair")
            events = await _run(conversation, runtime, "add a helper to widget.py")

            gated = [entry for entry in runtime.policy.audit if entry.tool == "write"]
            if gated:
                entry = gated[0]
                subject = entry.subject.replace("\n", " ")
                _bullet(
                    f"gate              write → {entry.decision.value}, approved={entry.approved}"
                )
                _bullet(f"                  subject: {subject[:70]}…")
                _bullet(f"                  because: {entry.reason}")
                for line in entry.trace[:3]:
                    _bullet(f"                    {line}")
                _bullet(
                    "                  write declares requires_approval=True; auto_edit "
                    "mode relaxed the ask to"
                )
                _bullet(
                    "                  allow, and the relaxation is *recorded* rather "
                    "than silent. In ask mode"
                )
                _bullet(
                    f"                  the same call reaches the asker (asked so far: "
                    f"{len(asker.asked)})."
                )
            else:
                print("  FAILED: the write never went through the policy engine")
                failures += 1
            refusals = [entry for entry in runtime.policy.audit if not entry.approved]
            if refusals:
                _bullet(
                    f"                  {len(refusals)} call(s) were refused outright: "
                    f"{refusals[0].reason[:70]}"
                )

            checkpoints = [e for e in _ends(events) if e.name == CHECKPOINT_STEP]
            if checkpoints and conversation.checkpoints:
                _bullet(f"checkpoint        {checkpoints[0].result.content}")
                _bullet(
                    f"                  shadow repo at "
                    f"{runtime.checkpoints.git_dir.relative_to(root)}; the user's own "
                    f".git/index was never opened"
                )
            elif has_git:
                print("  FAILED: a mutating turn took no checkpoint")
                failures += 1
            else:
                _bullet(
                    "checkpoint        git is not installed, so checkpoints degraded to "
                    "a note (see below)"
                )

            verifies = [e for e in _ends(events) if e.name == VERIFY_STEP]
            if verifies and not verifies[0].result.ok:
                _bullet(
                    f"verify            ran {' '.join(commands.argv_log[0])} — scoped to "
                    f"the test that covers the changed file"
                )
                _bullet(
                    "                  it FAILED, and the failure went back to the model "
                    "as a ToolResult"
                )
            else:
                print("  FAILED: the failing verify pass did not reach the model as a failure")
                failures += 1

            carrying = [
                message
                for message in conversation.messages
                if any(
                    "verify failed" in getattr(block, "content", "")
                    for block in message.content_blocks
                )
            ]
            roles = {message.role for message in carrying}
            if roles == {Role.TOOL}:
                _bullet(
                    "                  on a TOOL-role message, never a USER one: a user "
                    "turn reads as a new"
                )
                _bullet(
                    "                  instruction and the model re-scopes to explaining "
                    "the traceback"
                )
            else:
                print(f"  FAILED: the verify failure arrived on {roles or 'nothing'}")
                failures += 1

            report = conversation.last_repair
            if report is not None and not report.fixed:
                _bullet(
                    f"repair            {len(report.attempts)} attempt(s), verdict "
                    f"{report.verdict.value}, then it stopped"
                )
                _bullet(
                    f"                  the signature was identical "
                    f"{report.identical_seen}x across cosmetically different output"
                )
                print()
                for line in report.render().splitlines():
                    print(f"      {line}")
            else:
                print("  FAILED: the repair loop did not end with an honest report")
                failures += 1

            # ---------------------------------------------------- compaction
            _heading("3. compaction: fold the middle, keep the diffs")
            # A long tail of prior turns, so the trigger is reached on the next one.
            conversation.messages = (
                *conversation.messages,
                *(
                    Message(
                        role=Role.USER,
                        content_blocks=(Text("and what about the rest of the tree? " * 30),),
                    )
                    for _ in range(6)
                ),
            )
            before = len(conversation.messages)
            await _run(conversation, runtime, "what is going on with the rounding?")
            result = conversation.last_compaction
            if result is not None and result.compacted:
                _bullet(f"messages          {before} → {len(conversation.messages)}")
                _bullet(
                    f"tokens (est.)     ~{result.token_estimate_before:,} → "
                    f"~{result.token_estimate_after:,}"
                )
                _bullet(f"kept in full      {', '.join(result.retained_paths) or 'nothing'}")
                _bullet("marker            " + result.marker)
                if unpaired_tool_uses(conversation.messages):
                    print("  FAILED: compaction left an unpaired tool call")
                    failures += 1
                else:
                    _bullet(
                        "pairing           every tool call still has its result — the "
                        "transcript is sendable"
                    )
            else:
                print("  FAILED: compaction did not fire")
                failures += 1

            # ------------------------------------------------ notes so far
            if conversation.notes:
                _heading("4. what the session degraded on")
                for degradation in conversation.notes:
                    for index, line in enumerate(degradation.splitlines()[:3]):
                        _bullet(("note: " if index == 0 else "      ") + line)
        finally:
            await runtime.aclose()

        # ------------------------------------------------ transcript + replay
        _heading("5. the transcript, written and replayed")
        if runtime.transcript is None:
            print("  FAILED: no transcript was opened")
            failures += 1
        else:
            replayed = resume_session(loaded.paths.sessions_dir, "demo")
            _bullet(
                f"file              "
                f"{runtime.transcript.path.relative_to(root)} "
                f"({runtime.transcript.path.stat().st_size:,} bytes)"
            )
            _bullet(
                f"events            {len(replayed.events)} recorded, "
                f"{len(replayed.replay.turns)} turn(s) folded"
            )
            _bullet(
                f"messages          {len(replayed.state.messages)} rebuilt from events "
                f"alone ({replayed.replay.source.value})"
            )
            # From the *sidecar*, not the header: the header line carries the
            # session's immutable identity and is written before anything happens,
            # so it cannot know what the session went on to touch.
            rows = [
                meta
                for meta in list_sessions(loaded.paths.sessions_dir)
                if meta.session_id == "demo"
            ]
            touched = ", ".join(rows[0].files_touched) if rows else ""
            _bullet(f"files touched     {touched or 'none recorded'}")
            _bullet(
                f"turns / cost      {rows[0].turns} turn(s), ${rows[0].cost_usd:.4f}"
                if rows
                else "turns / cost      unknown"
            )
            if replayed.state.pairing_errors:
                print(
                    f"  FAILED: the replayed state is unpairable: "
                    f"{list(replayed.state.pairing_errors)}"
                )
                failures += 1
            else:
                _bullet(
                    "pairing           clean: the replayed state can be sent to a provider as-is"
                )
            if replayed.skipped:
                for skipped in replayed.skipped:
                    _bullet(f"skipped           {skipped}")

        # ------------------------------------------------------------- verdict
        _heading("what this proves")
        for line in (
            "the workspace loads and *says* what it could not load, instead of quietly",
            "doing less; a write is refused until the file has been read, and the gate's",
            "decision — including the mode relaxation that let it through without a prompt —",
            "is recorded rather than silent; the checkpoint is taken before the first byte",
            "changes, in a shadow repo the user's own git never sees; the loop does not end",
            "because the model said done — a scoped verify pass runs, its failure comes back",
            "to the model as a tool result rather than as a new instruction, and the repair",
            "loop stops at its cap and says 'i could not fix this' with everything it tried;",
            "compaction folds the middle while keeping the most recent tool result per file",
            "path in full, and shows the user that it happened; and every event is on disk,",
            "replayable, with no unpaired tool call anywhere.",
        ):
            _bullet(line)

        print()
        if failures:
            print(f"{failures} claim(s) above did not hold. This demo is a test that prints.")
            return 1
        print("every claim above held.")
        return 0


async def _run(conversation: Conversation, runtime: Any, prompt: str) -> list[Event]:
    return [event async for event in conversation.run_prompt(runtime, prompt, summarizer=summarize)]


def _ends(events: Sequence[Event]) -> list[ToolEnd]:
    return [event for event in events if isinstance(event, ToolEnd)]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
