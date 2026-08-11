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

from ronin.cli.main import Streams, _slash
from ronin.cli.sdk import Agent
from ronin.cli.stream import Conversation
from ronin.core.types import Message, Role, Text
from ronin.verify.checkpoints import Checkpoint


@dataclass
class Captured:
    """Collects what the dispatcher wrote, separated by stream."""

    out: list[str] = field(default_factory=list)
    err: list[str] = field(default_factory=list)

    def streams(self) -> Streams:
        return Streams(
            out=self.out.append,
            err=self.err.append,
            ask=lambda _question: "",
            flush=lambda: None,
        )

    @property
    def stdout(self) -> str:
        return "".join(self.out)

    @property
    def stderr(self) -> str:
        return "".join(self.err)


def agent_for(tmp_path: Path, *, git: Any = None) -> Agent:
    loaded = h.build_loaded(tmp_path)
    runtime = h.build_runtime(loaded, git=git)
    return Agent(runtime, conversation=Conversation(model=h.ScriptedModel([])))


async def run(line: str, agent: Agent, capture: Captured) -> bool | None:
    return await _slash(line, agent, capture.streams())


# --------------------------------------------------------------------------- #
# the session survives every command that is not an exit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line",
    ["/help", "/cost", "/clear", "/nonsense", "/plan", "/model"],
    ids=["help", "cost", "clear", "unknown", "unwired", "unwired-2"],
)
async def test_a_command_keeps_the_session_alive(tmp_path: Path, line: str) -> None:
    """`None` means "leave the session", so a command that returns it by accident
    hangs up on the user. Every one of these must return `True` — including the ones
    that fail, since a typo is not a reason to end the session."""
    assert await run(line, agent_for(tmp_path), Captured()) is True


async def test_an_unparseable_line_reports_the_parse_error_and_stays(
    tmp_path: Path,
) -> None:
    """A malformed slash line comes from a human mid-thought. The parse error goes to
    stderr so it cannot be confused with a model's answer."""
    capture = Captured()
    assert await run("/", agent_for(tmp_path), capture) is True
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

    assert await run("/clear", agent, capture) is True
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
    assert await run("/undo", agent_for(tmp_path), capture) is True

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

    assert await run("/undo", agent, capture) is True
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
    assert await run("/diff", agent_for(tmp_path), capture) is True
    assert capture.stdout.strip() != "", "a failed diff printed nothing at all"


# --------------------------------------------------------------------------- #
# the unwired commands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["compact", "agents", "hooks", "init", "model", "plan", "resume"],
)
async def test_a_registered_but_unwired_command_admits_it_by_name(
    tmp_path: Path, name: str
) -> None:
    """`_slash` promises this in words: "a command that silently does nothing is worse
    than one that admits it is not wired." Seven commands are in the registry and not
    in this dispatcher, and each has to say so — with its own name, so the user knows
    which of the two things they typed was the problem, and pointing at `--help`."""
    capture = Captured()
    assert await run(f"/{name}", agent_for(tmp_path), capture) is True

    assert capture.stdout == "", "an unwired command produced output as if it had run"
    assert f"/{name}" in capture.stderr
    assert "not wired into this line session" in capture.stderr


async def test_an_unknown_command_is_not_reported_as_merely_unwired(
    tmp_path: Path,
) -> None:
    """ "Not wired" and "does not exist" are different facts and lead to different next
    moves — waiting for a release versus fixing a typo."""
    capture = Captured()
    await run("/definitelynotacommand", agent_for(tmp_path), capture)
    assert "not wired into this line session" not in capture.stderr
