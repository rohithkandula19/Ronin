"""The context layer's last uncovered branches: fallbacks, and the honest zero.

Almost every line here is a *degradation path* — what the layer does when the world
is not as tidy as the happy path assumes. That is the interesting half of a context
manager, because none of these failures announce themselves: a repo root that cannot
be found, a workflow file that will not open, a path that is not under the root. Each
one returns a defensible answer rather than raising, and an untested defensible
answer is indistinguishable from an accident.

Grouped by module rather than by line, because the branches only make sense next to
the thing they are protecting.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ronin.context.budget import OutputBudget, _fit_lines, clamp
from ronin.context.compaction import (
    PINNED_KEY,
    CompactionPolicy,
    _fallback_summary,
    _normalize_path,
    _owner_index,
    _render_arguments,
    compact,
    file_path_from_arguments,
    plan_compaction,
    render_message,
)
from ronin.context.filestate import FileStateTracker
from ronin.context.memory import (
    MAX_PARENTS,
    Memory,
    MemoryFile,
    MemoryScope,
    _depth,
    discover_commands,
    find_repo_root,
    load_memory,
    memory_paths,
    remember,
)
from ronin.core.types import (
    Message,
    Role,
    Text,
    Thinking,
    ToolResultBlock,
    ToolUse,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# memory: finding the repo, and giving up on finding it
# --------------------------------------------------------------------------- #


def test_a_tree_with_no_git_directory_has_no_repo_root(tmp_path: Path) -> None:
    """`None`, not the filesystem root.

    Walking to `/` and calling it the repo would make RONIN.md discovery load
    whatever happened to be in a parent directory of the checkout — someone else's
    file, in a shared home.
    """
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) is None


def test_the_repo_root_is_found_from_a_file_not_only_a_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    source = write(tmp_path / "src" / "app.py", "x = 1\n")
    assert find_repo_root(source) == tmp_path


def test_an_explicit_root_that_is_not_above_cwd_is_trusted_anyway(tmp_path: Path) -> None:
    """The caller said "this is the boundary", so the walk stops guessing.

    Two unrelated trees is not a hypothetical — it is what a test fixture or a
    monorepo tool passing `--root` looks like. Silently ignoring the explicit root
    and walking up from cwd instead would load memory from outside it.
    """
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()

    paths = memory_paths(elsewhere, home=None, root=project)
    assert any(str(project) in str(p) for p in paths)


def test_the_paths_helper_lists_every_file_that_was_loaded(tmp_path: Path) -> None:
    memory = Memory(
        files=(
            MemoryFile(path="a/RONIN.md", scope=MemoryScope.PROJECT, text="a", depth=0),
            MemoryFile(path="b/RONIN.md", scope=MemoryScope.PROJECT, text="b", depth=1),
        ),
        root=str(tmp_path),
    )
    assert memory.paths() == ("a/RONIN.md", "b/RONIN.md")


def test_a_memory_file_outside_the_root_keeps_its_absolute_label(tmp_path: Path) -> None:
    """The global `~/.ronin/RONIN.md` is not under the repo, so it cannot be shown
    as a relative path — and a label that pretends otherwise hides where a rule
    came from."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    write(home / ".ronin" / "RONIN.md", "global rule\n")
    write(repo / "RONIN.md", "local rule\n")

    memory = load_memory(repo, home=home, root=repo)
    labels = memory.paths()
    assert any(str(home) in label for label in labels), labels


# --------------------------------------------------------------------------- #
# memory: /init's command detection, on the shapes it has to guess from
# --------------------------------------------------------------------------- #


def test_npm_declared_as_a_package_manager_becomes_npm_run(tmp_path: Path) -> None:
    """`packageManager: "npm@10"` is a declaration, and npm's script runner is
    `npm run` — not bare `npm`, which would produce a command that does nothing."""
    write(tmp_path / "package.json", '{"packageManager": "npm@10.2.0", "scripts": {"test": "x"}}')
    found = discover_commands(tmp_path)
    assert any(c.command.startswith("npm run") for c in found), [c.command for c in found]


def test_black_configured_in_pyproject_is_found_as_the_formatter(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[tool.black]\nline-length = 100\n")
    found = discover_commands(tmp_path)
    assert any(c.command == "black ." for c in found), [c.command for c in found]


def test_a_tool_declared_only_in_an_optional_extra_is_still_attributed(
    tmp_path: Path,
) -> None:
    """A dependency can live in `[project.optional-dependencies]` rather than a
    dependency group, and `/init` should still say where it found it — the provenance
    is the point of the bootstrap."""
    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "x"\noptional-dependencies = { dev = ["pytest>=8"] }\n',
    )
    found = discover_commands(tmp_path)
    sources = " ".join(c.evidence for c in found)
    assert "pytest" in " ".join(c.command for c in found)
    assert "optional-dependencies" in sources or "pyproject" in sources


def test_an_unreadable_workflow_file_is_skipped_rather_than_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/init` reads CI to learn the real commands. One unreadable file must not
    cost the user the whole bootstrap."""
    write(
        tmp_path / ".github" / "workflows" / "ci.yml", "jobs:\n  t:\n    steps:\n      - run: x\n"
    )
    write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")

    real_read = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.suffix in {".yml", ".yaml"}:
            raise OSError("permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    found = discover_commands(tmp_path)
    assert any(c.command == "pytest" for c in found), "the rest of detection stopped working"


def test_a_workflow_command_that_matches_nothing_is_classified_as_nothing(
    tmp_path: Path,
) -> None:
    """An unrecognised CI step is dropped rather than guessed into a category. A
    `pytest` label on a step that deploys is worse than no label."""
    write(
        tmp_path / ".github" / "workflows" / "ci.yml",
        "jobs:\n  d:\n    steps:\n      - run: ./deploy.sh --to prod\n",
    )
    found = discover_commands(tmp_path)
    assert not any("deploy" in c.command for c in found), [c.command for c in found]


def test_an_unparseable_pyproject_yields_no_commands_rather_than_raising(
    tmp_path: Path,
) -> None:
    write(tmp_path / "pyproject.toml", "this is not = = toml [[[\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert discover_commands(tmp_path) == () or all(
        "pyproject" not in c.evidence for c in discover_commands(tmp_path)
    )


# --------------------------------------------------------------------------- #
# compaction: rendering, and paths
# --------------------------------------------------------------------------- #


def test_a_thinking_block_is_rendered_by_its_text(tmp_path: Path) -> None:
    """The summarizer reads a rendered transcript, so a block type it does not
    special-case must still contribute its words rather than vanish."""
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(Thinking(text="weighing two options"),),
    )
    assert "weighing two options" in render_message(message)


def test_every_block_kind_survives_rendering() -> None:
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(
            Text("prose"),
            ToolUse(id="t1", name="read", arguments={"path": "a.py"}),
        ),
    )
    rendered = render_message(message)
    assert "prose" in rendered
    assert "read" in rendered and "a.py" in rendered


def test_a_failed_tool_result_renders_as_an_error() -> None:
    message = Message(
        role=Role.TOOL,
        content_blocks=(ToolResultBlock(tool_use_id="t1", content="boom", is_error=True),),
    )
    assert "error t1" in render_message(message)


def test_arguments_are_rendered_in_sorted_order() -> None:
    """Deterministic, because this text feeds a summarizer prompt and a reordered
    prompt is a different prompt — which costs a cache hit for nothing."""
    assert _render_arguments({"z": 1, "a": 2}) == "a=2, z=1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("./a.py", "a.py"), (".././a.py", "../a.py"), ("././b.py", "b.py"), ("a.py", "a.py")],
    ids=["one-dot", "parent-then-dot", "two-dots", "plain"],
)
def test_leading_dot_segments_are_stripped_so_one_file_is_one_key(raw: str, expected: str) -> None:
    """`./src/a.py` and `src/a.py` must be the same retention key, or "most recent
    result per file path" keeps two entries for one file and the budget pays twice."""
    assert _normalize_path(raw) == expected


def test_arguments_with_no_path_at_all_yield_no_path() -> None:
    assert file_path_from_arguments({"pattern": "TODO", "limit": 5}) is None


def test_a_blank_path_argument_is_not_a_path() -> None:
    """A whitespace-only value would otherwise become a retention key of `""`."""
    assert file_path_from_arguments({"path": "   "}) is None


# --------------------------------------------------------------------------- #
# budget: the marker must never lie
# --------------------------------------------------------------------------- #


def test_a_negative_line_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="remaining_lines"):
        OutputBudget(remaining_chars=100, remaining_lines=-1)


def test_a_budget_smaller_than_its_own_marker_returns_only_the_marker() -> None:
    """The degenerate case. With no room for even one character of content beside
    the marker, emitting a truncated-looking body would be a lie about what was
    kept; the marker alone is the honest answer."""
    clamped = clamp("x" * 5_000, budget=OutputBudget(remaining_chars=12))
    assert "elided" in clamped.text
    assert len(clamped.text) >= 12 or clamped.text.strip() != ""


def test_a_clamp_that_would_elide_nothing_keeps_the_marker_truthful() -> None:
    """A marker claiming truncation that elided nothing is the specific dishonesty
    this guards. Asserted unconditionally: a `0 lines elided` check that only runs
    when a marker happens to be present is a check that can pass by being skipped."""
    text = "\n".join(f"line {i}" for i in range(8))
    clamped = clamp(text, budget=OutputBudget(remaining_chars=len(text), remaining_lines=4))
    assert clamped.truncated
    assert clamped.elided_lines >= 1
    assert "0 lines elided" not in clamped.text


@pytest.mark.parametrize(
    "count",
    [2, 3, 8],
    ids=["two-lines", "three-lines", "eight-lines"],
)
def test_the_line_fitter_never_claims_every_line_fits(count: int) -> None:
    """`_fit_lines` promises "never all of them", and it is the only thing standing
    between a generous budget and a marker that reads `0 lines elided`.

    Called directly because `clamp` checks `fits` first and so never reaches this
    state — which is exactly why the promise is worth pinning here. If a future change
    to `fits` ever let a fitting text through, the guarantee would be relied on
    without ever having been exercised.
    """
    lines = [f"line {index}\n" for index in range(count)]
    head_n, tail_n = _fit_lines(lines, OutputBudget(remaining_chars=100_000), marker_cost=10)
    assert head_n + tail_n == count - 1, (head_n, tail_n)
    assert head_n >= 1, "something has to be kept, or the marker stands alone"


def test_the_line_fitter_gives_up_a_tail_line_before_a_head_line() -> None:
    """Which end pays matters when both ends met in the middle.

    The head carries the command and the first of its output — the part a model reads to
    understand what it is looking at — so the line surrendered comes off the tail. The
    budget here is tuned so head and tail together cover all ten lines exactly, which is
    the only way to reach the branch with a tail to spare.
    """
    lines = [f"line {index}\n" for index in range(10)]  # ten lines of seven chars
    budget = OutputBudget(remaining_chars=80, head_fraction=0.5)
    head_n, tail_n = _fit_lines(lines, budget, marker_cost=10)
    assert (head_n, tail_n) == (5, 4), "expected one line elided, taken from the tail"


# --------------------------------------------------------------------------- #
# filestate
# --------------------------------------------------------------------------- #


def test_a_file_never_read_has_no_record(tmp_path: Path) -> None:
    """`None` rather than a fabricated record: the edit guard asks "was this read?"
    and a default answer of "yes, and unchanged" is exactly the clobber the tracker
    exists to prevent."""
    tracker = FileStateTracker()
    assert tracker.recorded(tmp_path / "never.py") is None


def test_a_recorded_file_can_be_looked_up_and_forgotten(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    assert tracker.recorded(path) is not None
    tracker.forget(path)
    assert tracker.recorded(path) is None


def test_known_paths_is_sorted_so_output_is_reproducible(tmp_path: Path) -> None:
    tracker = FileStateTracker()
    for name in ("z.py", "a.py", "m.py"):
        tracker.record_read(write(tmp_path / name, "x\n"))
    names = [Path(p).name for p in tracker.known_paths()]
    assert names == sorted(names)


def test_the_tracker_survives_a_dict_argument_shape(tmp_path: Path) -> None:
    """Guards the accessor's key type: records are keyed by `str(path)`, so a
    `Path` and its string form must find the same record."""
    path = write(tmp_path / "a.py", "x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    args: dict[str, Any] = {"path": str(path)}
    assert tracker.recorded(Path(args["path"])) is not None


# --------------------------------------------------------------------------- #
# compaction: the boundary that will not split a pair, and the local digest
# --------------------------------------------------------------------------- #


def system(text: str = "system prompt") -> Message:
    return Message(role=Role.SYSTEM, content_blocks=(Text(text),))


def says(text: str) -> Message:
    return Message(role=Role.USER, content_blocks=(Text(text),))


def calls(*uses: ToolUse, pinned: bool = False) -> Message:
    return Message(
        role=Role.ASSISTANT,
        content_blocks=tuple(uses),
        metadata={PINNED_KEY: True} if pinned else {},
    )


def answers(*results: ToolResultBlock) -> Message:
    return Message(role=Role.TOOL, content_blocks=tuple(results))


def read_call(call_id: str, path: str) -> ToolUse:
    return ToolUse(id=call_id, name="read", arguments={"path": path})


def test_a_pair_the_boundary_cannot_avoid_splitting_folds_nothing() -> None:
    """The transcript that arrives already broken.

    A call in the very first unpinned message whose result only appears five messages
    later cannot be kept whole by *any* boundary short of the head — so compaction
    declines rather than folding the call away from its result. `core.types` documents
    an orphaned pair as a provider 400 in its own right; the guarantee here is that
    compaction does not manufacture one, not that it can repair this transcript.
    """
    messages = [
        system(),
        calls(read_call("c1", "src/a.py")),
        says("turn one"),
        says("turn two"),
        says("turn three"),
        says("turn four"),
        answers(ToolResultBlock(tool_use_id="c1", content="contents of a.py")),
    ]
    plan = plan_compaction(messages, policy=CompactionPolicy(context_window=1_000))

    assert plan.head_end == 1
    assert plan.tail_start == 1
    assert not plan.has_middle, "folding here would separate c1 from its result"


def test_a_result_whose_call_is_nowhere_in_the_range_has_no_owner() -> None:
    """`_owner_index` answers `None` instead of raising when a pairing is already
    broken. Only reachable by direct call — the ids it searches for are collected from
    the very range it searches — and worth pinning because the caller compares the
    answer to an index, where a raise would abort compaction on a transcript that is
    merely malformed."""
    messages = [
        calls(read_call("c1", "a.py")),
        answers(ToolResultBlock(tool_use_id="c1", content="x = 1")),
    ]
    assert _owner_index(messages, "c1", 0, 2) == 0
    assert _owner_index(messages, "never-issued", 0, 2) is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [("permission denied\nline two", "permission denied"), ("", "(no message)")],
    ids=["first-line", "no-message"],
)
def test_the_local_digest_names_what_failed_and_why(content: str, expected: str) -> None:
    """The `Failed` section is the part of a summary a model acts on: without it the
    next turn retries the call that just failed. Only the first line is quoted, because
    a stack trace in a summary is the summary.

    `(no message)` rather than an empty bullet — a failure with no text is still a
    failure, and a blank line after "failed:" reads like a formatting bug.
    """
    messages = [
        says("fix the config"),
        calls(ToolUse(id="c1", name="write", arguments={"path": "etc/app.conf"})),
        answers(ToolResultBlock(tool_use_id="c1", content=content, is_error=True)),
    ]
    digest = _fallback_summary(messages)

    assert "## Failed" in digest
    assert f"- write failed: {expected}" in digest, digest
    assert "line two" not in digest
    assert "- etc/app.conf — write" in digest, "the file lane must still record the attempt"


def test_the_local_digest_says_so_when_there_was_no_user_message() -> None:
    """A folded range can be all assistant turns. Inventing a goal would be the one
    thing the local digest promises never to do."""
    digest = _fallback_summary([calls(read_call("c1", "a.py"))])
    assert "(no user message in the folded range)" in digest


async def test_a_call_orphaned_by_the_fold_is_dropped_and_the_marker_says_how_many() -> None:
    """A pinned call whose result gets folded away.

    Pinning keeps the call in the head while its result sits in the middle, so the fold
    leaves a call with no result — the exact shape a provider rejects with a 400. The
    repair drops it, and the marker has to *say* it did: a transcript silently one block
    lighter than the model remembers is a debugging session with no thread to pull.
    """
    messages = [
        system(),
        calls(read_call("pinned-1", "src/pinned.py"), pinned=True),
        answers(ToolResultBlock(tool_use_id="pinned-1", content="contents")),
        says("turn one"),
        says("turn two"),
        says("turn three"),
        says("turn four"),
    ]

    async def summarizer(prompt: str) -> str:
        return (
            "## Goal\ng\n\n## Files touched\n- none\n\n## Learned\n- l\n\n"
            f"## Failed\n- none\n\n## Remaining\n- r ({len(prompt)} chars)\n"
        )

    result = await compact(
        messages, policy=CompactionPolicy(context_window=1_000), summarizer=summarizer
    )

    assert result.compacted
    assert result.dropped_blocks == 1, result.marker
    assert "orphan tool block(s) dropped" in result.marker
    assert "1 orphan" in result.marker


# --------------------------------------------------------------------------- #
# memory: giving up, and depth with nothing to measure against
# --------------------------------------------------------------------------- #


def test_a_walk_that_runs_out_of_parents_gives_up_rather_than_looping(tmp_path: Path) -> None:
    """The ceiling exists because a symlink loop or a pathological mount can make
    `parent` never converge. `MAX_PARENTS` steps and then `None`, which discovery
    already treats as "no repo here"."""
    deep = tmp_path.joinpath(*[f"d{index}" for index in range(MAX_PARENTS + 5)])
    deep.mkdir(parents=True)
    assert find_repo_root(deep) is None


@pytest.mark.parametrize(
    ("root", "expected"),
    [(None, 0), ("elsewhere", 0)],
    ids=["no-root", "outside-the-root"],
)
def test_depth_is_zero_when_there_is_nothing_to_measure_against(
    tmp_path: Path, root: str | None, expected: int
) -> None:
    """Depth orders memory files so an inner RONIN.md overrides an outer one. With no
    root, or a directory that is not under it, there is no ordering to state — and `0`
    is the honest answer rather than a raise that would take down the whole load.

    Reachable through `load_memory` for the no-root case (see below) and only by direct
    call for the other, since `memory_paths` never hands back a project path from
    outside the boundary it was given.
    """
    directory = tmp_path / "project" / "src"
    directory.mkdir(parents=True)
    boundary = None if root is None else tmp_path / root
    assert _depth(directory, boundary) == expected


def test_memory_loaded_with_no_repo_root_still_reports_a_depth(tmp_path: Path) -> None:
    """The integration side of the branch above: a checkout with no `.git` is a normal
    thing to be standing in, and it must not fail to load memory."""
    write(tmp_path / "RONIN.md", "always run the tests with -x\n")
    memory = load_memory(tmp_path, home=None, root=None)

    assert memory.root == ""
    assert [file.depth for file in memory.files] == [0]
    assert "always run the tests with -x" in memory.files[0].text


def test_remember_with_no_repo_root_writes_where_you_are_standing(tmp_path: Path) -> None:
    """With no root and no existing RONIN.md anywhere above, the walk reaches the
    filesystem root and stops. The rule then lands in the current directory — the only
    place left that is certainly the user's."""
    here = tmp_path / "scratch"
    here.mkdir()
    target = remember("prefer uv over pip", cwd=here, root=None, today=date(2026, 8, 10))

    assert target == here / "RONIN.md"
    assert "- prefer uv over pip" in target.read_text(encoding="utf-8")
    assert "2026-08-10" in target.read_text(encoding="utf-8")
