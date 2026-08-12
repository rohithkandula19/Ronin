"""The interactive session's slash commands — the whole dispatcher, previously untested.

`_slash` is what a user reaches for when something has already gone wrong: `/diff` to see
what changed, `/undo` to put it back, `/clear` to start the conversation over, `/cost` to
find out what the last hour spent. None of it was covered.

Two of the thirteen matter more than the rest and neither had a test:

* **`/undo` writes to the user's files.** It restores the last checkpoint, which is the
  single most consequential thing the line session can do without the model's
  involvement. A `/undo` that silently does nothing after a bad edit is worse than one
  that errors, because the user walks away believing the edit is gone.
* **The unwired branch.** Seven of the thirteen registered commands are not implemented
  in the line session, and `_slash`'s docstring commits to saying so by name: "a command
  that silently does nothing is worse than one that admits it is not wired." That is a
  promise about output, and nothing checked it.

The dispatcher's return value is its control flow — `True` to keep the session, `None` to
leave it — so every test asserts it, not just the text. A `/help` that accidentally
returned `None` would end the session on the one command a confused user is most likely
to type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import stream_harness as h

from ronin.cli.main import Slash, Streams, _slash
from ronin.cli.sdk import Agent
from ronin.cli.stream import Conversation
from ronin.core.types import Message, Mode, Role, Text
from ronin.tools.base import ToolContext
from ronin.tools.registry import build_registry
from ronin.ui.commands import BUILTIN_COMMANDS
from ronin.verify.checkpoints import Checkpoint


@dataclass
class Captured:
    """Collects what the dispatcher wrote, separated by stream."""

    out: list[str] = field(default_factory=list)
    err: list[str] = field(default_factory=list)
    #: Scripted answers for a command that asks something — `/init` does. An empty list
    #: answers "" forever, which is what a closed stdin returns.
    answers: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def streams(self) -> Streams:
        def ask(question: str) -> str:
            self.questions.append(question)
            return self.answers.pop(0) if self.answers else ""

        return Streams(
            out=self.out.append,
            err=self.err.append,
            ask=ask,
            flush=lambda: None,
        )

    @property
    def stdout(self) -> str:
        return "".join(self.out)

    @property
    def stderr(self) -> str:
        return "".join(self.err)


def agent_for(tmp_path: Path, *, git: Any = None, tools: h.ScriptedTools | None = None) -> Agent:
    loaded = h.build_loaded(tmp_path)
    runtime = h.build_runtime(loaded, git=git, tools=tools)
    return Agent(runtime, conversation=Conversation(model=h.ScriptedModel([])))


async def run(line: str, agent: Agent, capture: Captured) -> Slash:
    return await _slash(line, agent, capture.streams())


# --------------------------------------------------------------------------- #
# the session survives every command that is not an exit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line",
    ["/help", "/cost", "/clear", "/nonsense", "/model", "/hooks"],
    ids=["help", "cost", "clear", "unknown", "model", "hooks"],
)
async def test_a_command_keeps_the_session_alive(tmp_path: Path, line: str) -> None:
    """`Slash.leave` ends the session, so a command that sets it by accident hangs up on
    the user. None of these may — including the ones that fail, since a typo is not a
    reason to end the session — and none is a prompt to run either."""
    outcome = await run(line, agent_for(tmp_path), Captured())
    assert outcome == Slash(), f"{line} asked the session to do something"


async def test_an_unparseable_line_reports_the_parse_error_and_stays(
    tmp_path: Path,
) -> None:
    """A malformed slash line comes from a human mid-thought. The parse error goes to
    stderr so it cannot be confused with a model's answer."""
    capture = Captured()
    assert await run("/", agent_for(tmp_path), capture) == Slash()
    assert capture.stderr != ""


# --------------------------------------------------------------------------- #
# /help
# --------------------------------------------------------------------------- #


async def test_help_lists_the_commands(tmp_path: Path) -> None:
    """The command a user types when they have forgotten the others, so it has to name
    them rather than describe the concept of commands."""
    capture = Captured()
    await run("/help", agent_for(tmp_path), capture)
    for name in ("undo", "diff", "cost", "clear"):
        assert name in capture.stdout, f"/help did not mention /{name}"


# --------------------------------------------------------------------------- #
# /clear
# --------------------------------------------------------------------------- #


async def test_clear_drops_the_transcript_and_says_what_it_kept(tmp_path: Path) -> None:
    """The distinction in the message is the whole point: `/clear` is not `/reset`. A
    user who thinks it also dropped their tools or reloaded their config will be
    surprised in the other direction later."""
    agent = agent_for(tmp_path)
    agent.conversation.messages = (Message(role=Role.USER, content_blocks=(Text("hi"),)),)
    before = agent.conversation
    capture = Captured()

    assert await run("/clear", agent, capture) == Slash()
    # `reset` swaps in a new Conversation rather than mutating the old one, so the
    # assertion has to read the agent again — checking the captured object would pass
    # on an implementation that dropped nothing.
    assert agent.conversation is not before
    remaining: tuple[Message, ...] = agent.conversation.messages
    assert remaining == ()
    assert "workspace and tools are unchanged" in capture.stdout


# --------------------------------------------------------------------------- #
# /cost
# --------------------------------------------------------------------------- #


async def test_cost_reports_tokens_dollars_and_wall_time(tmp_path: Path) -> None:
    """All three, because they fail differently: a cheap session can still be slow, and
    a fast one can still be expensive. One number invites the wrong conclusion."""
    capture = Captured()
    await run("/cost", agent_for(tmp_path), capture)

    assert "tokens" in capture.stdout
    assert "$" in capture.stdout
    assert "s" in capture.stdout


# --------------------------------------------------------------------------- #
# /undo — the command that writes
# --------------------------------------------------------------------------- #


async def test_undo_with_no_checkpoint_says_so_rather_than_pretending(
    tmp_path: Path,
) -> None:
    """The failure mode that matters. With nothing checkpointed there is nothing to
    restore, and a silent success here tells a user their bad edit is gone when it is
    still on disk. It goes to stderr: this is a refusal, not a result."""
    capture = Captured()
    assert await run("/undo", agent_for(tmp_path), capture) == Slash()

    assert capture.stdout == ""
    assert "nothing to undo" in capture.stderr


async def test_undo_restores_the_most_recent_checkpoint(tmp_path: Path) -> None:
    """Most recent, not the first: `/undo` means "put back what just happened". Taking
    `checkpoints[0]` would silently discard every change of the session on a command
    the user expects to step back once."""
    h.git_repo(tmp_path)
    git = h.FakeGit()
    agent = agent_for(tmp_path, git=git)
    agent.conversation.checkpoints.extend(
        [
            Checkpoint(id="a" * 40, label="the first edit"),
            Checkpoint(id="b" * 40, label="the edit to undo"),
        ]
    )
    capture = Captured()

    assert await run("/undo", agent, capture) == Slash()
    joined = " ".join(" ".join(call) for call in git.argv_log)
    assert "b" * 40 in joined, f"the wrong checkpoint was restored: {joined}"
    assert "a" * 40 not in joined


# --------------------------------------------------------------------------- #
# /diff
# --------------------------------------------------------------------------- #


async def test_diff_prints_the_detail_when_there_is_no_diff_to_show(
    tmp_path: Path,
) -> None:
    """`session_diff` returns a value rather than raising, and both halves of it are
    user-facing: the diff when it worked, the reason when it did not. Printing an empty
    string for the failure would look like "nothing changed"."""
    capture = Captured()
    assert await run("/diff", agent_for(tmp_path), capture) == Slash()
    assert capture.stdout.strip() != "", "a failed diff printed nothing at all"


# --------------------------------------------------------------------------- #
# the seven that used to say "not wired"
# --------------------------------------------------------------------------- #

#: Every command the dispatcher answers. This file used to hold the mirror image of this
#: list — seven names asserted to *refuse* — and the parametrised test that pinned them is
#: gone rather than weakened, because "admits it is not wired" is no longer true of any of
#: them. What replaces it is one test per command, below, plus this: nothing in the
#: registry may fall through to the not-wired branch again.
WIRED: tuple[str, ...] = (
    "help",
    "doctor",
    "clear",
    "cost",
    "diff",
    "undo",
    "compact",
    "model",
    "plan",
    "resume",
    "agents",
    "hooks",
    "init",
)


def test_every_builtin_command_is_wired() -> None:
    """The registry and the dispatcher must agree. A command declared and not handled is a
    command a user can type and be told does not work, which is the state this phase
    ended."""
    declared = {command.name for command in BUILTIN_COMMANDS}
    assert declared == set(WIRED), "the registry and this dispatcher disagree"


@pytest.mark.parametrize("name", WIRED)
async def test_no_command_reports_itself_as_unwired(tmp_path: Path, name: str) -> None:
    capture = Captured()
    await run(f"/{name}", agent_for(tmp_path), capture)
    assert "not wired" not in capture.stderr, f"/{name} still refuses"


async def test_compact_below_the_trigger_says_so_rather_than_pretending(
    tmp_path: Path,
) -> None:
    """An empty transcript has nothing to fold. Reporting a fold that did not happen would
    make the command useless as a way of finding out whether one is needed."""
    capture = Captured()
    await run("/compact", agent_for(tmp_path), capture)
    assert "nothing to compact" in capture.stdout


async def test_model_with_no_argument_shows_what_is_running_and_what_is_available(
    tmp_path: Path,
) -> None:
    capture = Captured()
    await run("/model", agent_for(tmp_path), capture)
    assert "main model:" in capture.stdout
    assert "configured:" in capture.stdout


async def test_model_with_an_unknown_name_lists_the_ones_that_exist(tmp_path: Path) -> None:
    """A refusal a user can act on. "no such model" alone leaves them guessing at spelling;
    naming the configured set answers the next question before it is asked."""
    capture = Captured()
    await run("/model definitely-not-a-model", agent_for(tmp_path), capture)
    assert "definitely-not-a-model" in capture.stderr
    assert "known:" in capture.stderr
    assert capture.stdout == "", "a failed switch must not report success"


async def test_model_switches_the_client_the_next_turn_uses(tmp_path: Path) -> None:
    """The switch is real: the conversation's model override changes, so the next turn is
    answered by the model that was named."""
    agent = agent_for(tmp_path)
    before = agent.conversation.model
    known = agent.runtime.session.router.model_names
    assert known, "the harness router configures at least one model"

    capture = Captured()
    await run(f"/model {known[0]}", agent, capture)

    assert agent.conversation.model is not before, "the client did not change"
    assert "next turn runs on" in capture.stdout
    assert "subagents and compaction are unchanged" in capture.stdout


async def test_plan_mode_takes_the_write_tools_away(tmp_path: Path) -> None:
    """Plan mode is a registry, not a prompt — the guarantee `--mode plan` gives at startup,
    now reachable mid-session. A model with no `write` in its registry cannot write."""
    loaded = h.build_loaded(tmp_path)
    # A real session registry, because plan mode narrows *that* — the whole point is which
    # tools the model is offered, and `ScriptedTools` is the gated layer above it.
    runtime = h.build_runtime(loaded, session_tools=build_registry(ToolContext(root=tmp_path)))
    agent = Agent(runtime, conversation=Conversation(model=h.ScriptedModel([])))
    capture = Captured()
    await run("/plan", agent, capture)

    names = {spec.name for spec in agent.runtime.registry.specs()}
    assert names, "plan mode left no tools at all"
    assert "read" in names, "it kept the tools planning needs"
    assert not {"write", "edit", "bash", "multi_edit"} & names, "a mutating tool survived"
    assert agent.runtime.policy.mode is Mode.PLAN
    assert "not reversible" in capture.stdout, "a one-way door has to say so"


async def test_a_session_with_nothing_to_read_cannot_plan_and_says_why(tmp_path: Path) -> None:
    """`plan_runtime` refuses to build an empty registry, and it is right to: a session with
    no read-only tool cannot plan. The command reports that rather than raising."""
    capture = Captured()
    await run("/plan", agent_for(tmp_path), capture)
    assert "cannot enter plan mode" in capture.stderr


async def test_resume_with_no_recorded_session_says_where_to_look(tmp_path: Path) -> None:
    capture = Captured()
    await run("/resume", agent_for(tmp_path), capture)
    assert "no recorded session" in capture.stderr
    assert "sessions" in capture.stderr, "it names the command that lists them"


async def test_agents_lists_what_can_be_delegated_to(tmp_path: Path) -> None:
    capture = Captured()
    await run("/agents", agent_for(tmp_path), capture)
    # The harness workspace defines none, so the honest answer is that plus how to add one.
    assert "subagent" in capture.stdout
    assert ".ronin/agents" in capture.stdout or "[" in capture.stdout


async def test_hooks_reports_the_configured_hooks(tmp_path: Path) -> None:
    capture = Captured()
    await run("/hooks", agent_for(tmp_path), capture)
    assert "hook" in capture.stdout
    assert ".ronin/hooks.json" in capture.stdout or ":" in capture.stdout


async def test_init_writes_the_workspace_files_after_asking(tmp_path: Path) -> None:
    """`/init` is the first-run wizard, reachable on purpose rather than only on a first
    run — someone who skipped it with `--no-wizard` has no other way back to it."""
    capture = Captured(answers=["y"])
    await run("/init", agent_for(tmp_path), capture)
    assert capture.questions, "it wrote without asking"
    assert "wrote" in capture.stdout or "nothing needed writing" in capture.stdout


async def test_an_unknown_command_is_a_typo_not_a_missing_feature(tmp_path: Path) -> None:
    """A name nobody declared is a typo, and the parser suggests the nearest real command
    rather than reporting a feature that does not exist."""
    capture = Captured()
    await run("/definitelynotacommand", agent_for(tmp_path), capture)
    assert "not wired" not in capture.stderr
    assert capture.stderr != ""


# --------------------------------------------------------------------------- #
# user-defined commands — the spec's acceptance test
# --------------------------------------------------------------------------- #


def with_command(tmp_path: Path, name: str, body: str) -> Agent:
    """A workspace with one user command on disk, loaded the way a real session loads it."""
    directory = tmp_path / ".ronin" / "commands"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(body, encoding="utf-8")
    return agent_for(tmp_path)


async def test_a_markdown_file_becomes_an_invokable_command(tmp_path: Path) -> None:
    """The spec's acceptance test, and it failed before this change for a reason no parser
    test could see: `.ronin/commands/*.md` has always been parsed, validated and loaded
    onto `Loaded.commands` at startup, and the dispatcher handed the parser
    `BUILTIN_REGISTRY` instead — so the one place a user could type their command answered
    "unknown command". Dispatch is now against the workspace's registry.
    """
    agent = with_command(tmp_path, "review", "Review $1 for $2 problems and report back.")
    capture = Captured()

    outcome = await run("/review src/main.py typing", agent, capture)

    assert outcome.prompt == "Review src/main.py for typing problems and report back."
    assert not outcome.leave
    assert "/review" in capture.stdout, "it says which file the command came from"
    assert ".ronin/commands/review.md" in capture.stdout.replace("\\", "/")


async def test_arguments_reach_the_body_by_name_and_in_bulk(tmp_path: Path) -> None:
    """`$ARGUMENTS` is everything, `$1..$n` are the words. Both, because a command like
    "explain $1" and one like "answer this: $ARGUMENTS" are both things people write."""
    agent = with_command(tmp_path, "ask", "About $1: $ARGUMENTS")
    capture = Captured()

    outcome = await run("/ask caching how does the prompt cache work", agent, capture)

    assert outcome.prompt == "About caching: caching how does the prompt cache work"


async def test_a_user_command_missing_an_argument_is_refused_not_blanked(
    tmp_path: Path,
) -> None:
    """A template with `$2` and one word given must not silently send "for  problems".
    The refusal names what is missing, and nothing runs."""
    agent = with_command(tmp_path, "review", "Review $1 for $2 problems.")
    capture = Captured()

    outcome = await run("/review src/main.py", agent, capture)

    assert outcome == Slash(), "an incomplete command must not run a turn"
    assert capture.stderr != ""
    assert capture.stdout == ""


async def test_a_user_command_cannot_shadow_a_builtin(tmp_path: Path) -> None:
    """Someone who drops in `clear.md` does not get to redefine `/clear`. The registry
    refuses the shadow at load time; this asserts the dispatcher still runs the builtin,
    which is the half a parser test cannot show."""
    agent = with_command(tmp_path, "clear", "this must not run as a prompt")
    capture = Captured()

    outcome = await run("/clear", agent, capture)

    assert outcome == Slash(), "the builtin ran, not the file"
    assert "transcript dropped" in capture.stdout


async def test_help_lists_the_user_commands_too(tmp_path: Path) -> None:
    """`/help` renders the workspace's registry, so a command someone added is discoverable
    without them remembering they added it."""
    agent = with_command(tmp_path, "review", "Review $1.")
    capture = Captured()
    await run("/help", agent, capture)
    assert "/review" in capture.stdout
