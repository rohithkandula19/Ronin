"""The spine's own invariants: derived paths, pinned tokens, notes, teardown.

Everything here is about a value the rest of ``cli`` reads and cannot check for
itself. ``Paths`` is worth testing because four subsystems each export their own
relative path constant and the failure of composing them wrongly is a session that
writes a transcript where its own resume cannot find it — silent, and only visible
a day later when ``--resume`` comes up empty.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin.cli.spine import (
    Loaded,
    Note,
    Paths,
    notes_from_settings,
    sandbox_note,
    sequence_note,
)
from ronin.context.memory import Memory, MemoryFile, MemoryScope
from ronin.context.repomap import FileEntry, RepoMap, Signature, SignatureKind
from ronin.core.types import Mode
from ronin.safety.sandbox import NoSandbox, Unavailable
from ronin.safety.settings import LayerError, Settings


def workspace(tmp_path: Path) -> Paths:
    """A workspace and a home that are *siblings*.

    Nesting home inside the workspace is what a real machine never does, and it makes
    "the user layer is not under the workspace" vacuously false.
    """
    return Paths(
        workspace_root=tmp_path / "repo", home=tmp_path / "home", cwd=tmp_path / "repo"
    )


def settings_for(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(
        workspace_root=tmp_path / "repo",
        home=tmp_path / "home",
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def test_every_derived_path_lives_under_the_one_ronin_directory(tmp_path: Path) -> None:
    """The whole point of composing the constants in one place."""
    paths = workspace(tmp_path)
    for derived in (
        paths.sessions_dir,
        paths.checkpoints_dir,
        paths.agents_dir,
        paths.commands_dir,
        paths.cache_dir,
        paths.mcp_config,
        paths.hooks_config,
        paths.project_settings,
        paths.local_settings,
    ):
        assert paths.ronin_dir == derived or paths.ronin_dir in derived.parents, derived


def test_the_user_settings_layer_is_under_home_not_the_workspace(tmp_path: Path) -> None:
    """A user layer read from the workspace would make every repo its own user."""
    paths = workspace(tmp_path)
    assert paths.home in paths.user_settings.parents
    assert paths.workspace_root not in paths.user_settings.parents


def test_discover_falls_back_to_cwd_when_there_is_no_repo(tmp_path: Path) -> None:
    """`ronin` in a scratch folder must work; the fallback is the answer."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    paths = Paths.discover(scratch, home=tmp_path / "home")
    assert paths.workspace_root == scratch.resolve()


def test_discover_finds_the_repo_root_from_a_subdirectory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    paths = Paths.discover(deep, home=tmp_path / "home")
    assert paths.workspace_root == tmp_path.resolve()
    assert paths.cwd == deep.resolve(), "cwd is where the user stands, not the root"


def test_ensure_never_creates_the_user_layer(tmp_path: Path) -> None:
    """A first run that materializes a directory in someone's home is a surprise."""
    paths = workspace(tmp_path)
    paths.ensure()
    assert paths.sessions_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert not (paths.home / ".ronin").exists()


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    paths.ensure()
    paths.ensure()
    assert paths.ronin_dir.is_dir()


def test_describe_marks_what_is_absent(tmp_path: Path) -> None:
    rows = workspace(tmp_path).describe()
    assert any("(absent)" in row for row in rows)
    assert any(row.startswith("workspace") for row in rows)


# --------------------------------------------------------------------------- #
# The pinned prefix — the join whose default of 0 is a bug
# --------------------------------------------------------------------------- #


def loaded_with(tmp_path: Path, *, memory: Memory, repo_map: RepoMap) -> Loaded:
    return Loaded(
        paths=workspace(tmp_path),
        settings=settings_for(tmp_path),
        memory=memory,
        repo_map=repo_map,
    )


def a_memory() -> Memory:
    return Memory(
        files=(
            MemoryFile(
                path="RONIN.md",
                scope=MemoryScope.PROJECT,
                text="run tests with `pytest -q`\n",
                depth=0,
            ),
        ),
        root=".",
    )


def a_map(*, entries: int) -> RepoMap:
    return RepoMap(
        entries=tuple(
            FileEntry(
                path=f"src/mod{index}.py",
                rank=0.5,
                inbound=1,
                signatures=(
                    Signature(
                        kind=SignatureKind.FUNCTION,
                        name=f"f{index}",
                        text=f"def f{index}()",
                        line=1,
                    ),
                ),
            )
            for index in range(entries)
        ),
        token_estimate=120 * entries,
    )


def test_pinned_prefix_counts_both_memory_and_the_repo_map(tmp_path: Path) -> None:
    """Passing 0 makes compaction believe the window is emptier than it is by
    exactly the size of the part of the prompt that never shrinks."""
    loaded = loaded_with(tmp_path, memory=a_memory(), repo_map=a_map(entries=2))
    both = loaded.pinned_prefix_tokens()
    map_only = loaded_with(
        tmp_path, memory=Memory(), repo_map=a_map(entries=2)
    ).pinned_prefix_tokens()
    memory_only = loaded_with(
        tmp_path, memory=a_memory(), repo_map=RepoMap()
    ).pinned_prefix_tokens()

    assert map_only > 0 and memory_only > 0
    assert both == map_only + memory_only


def test_an_empty_workspace_pins_nothing(tmp_path: Path) -> None:
    loaded = loaded_with(tmp_path, memory=Memory(), repo_map=RepoMap())
    assert loaded.pinned_prefix_tokens() == 0


def test_an_empty_repo_map_is_dropped_from_the_system_suffix(tmp_path: Path) -> None:
    """`RepoMap.render()` always emits its two header lines, so an empty workspace
    would otherwise pay for "# repo map — 0 of 0 file(s)" on every request."""
    empty = RepoMap()
    assert empty.render(), "guard: render() is non-empty even with no entries"
    loaded = loaded_with(tmp_path, memory=Memory(), repo_map=empty)
    assert loaded.system_suffix() == ""


def test_a_populated_repo_map_does_reach_the_system_suffix(tmp_path: Path) -> None:
    loaded = loaded_with(tmp_path, memory=a_memory(), repo_map=a_map(entries=1))
    suffix = loaded.system_suffix()
    assert "src/mod0.py" in suffix
    assert "pytest -q" in suffix
    assert suffix.index("pytest -q") < suffix.index("src/mod0.py"), (
        "memory comes before the map, so the most human-authored guidance is read first"
    )


def test_the_compaction_policy_is_built_in_one_place(tmp_path: Path) -> None:
    loaded = loaded_with(tmp_path, memory=Memory(), repo_map=RepoMap())
    policy = loaded.compaction_policy(context_window=1000)
    assert policy.context_window == 1000
    assert policy.trigger_tokens == 800, "the work order's 80% trigger"


def test_mode_is_read_from_settings_and_not_stored_twice(tmp_path: Path) -> None:
    loaded = Loaded(
        paths=workspace(tmp_path),
        settings=settings_for(tmp_path, mode=Mode.AUTO_EDIT),
        memory=Memory(),
        repo_map=RepoMap(),
    )
    assert loaded.mode is Mode.AUTO_EDIT


# --------------------------------------------------------------------------- #
# Notes — a run that degrades must say so
# --------------------------------------------------------------------------- #


def test_a_note_must_say_what_happens_instead(tmp_path: Path) -> None:
    """A note that names a problem without a consequence is a log line."""
    with pytest.raises(ValueError, match="detail is required"):
        Note(subject="sandbox", detail="")
    with pytest.raises(ValueError, match="subject is required"):
        Note(subject="", detail="something")


def test_an_unparseable_settings_layer_becomes_a_note(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        errors=(
            LayerError(layer="local", path=tmp_path / ".ronin/settings.local.json", message="bad"),
        ),
    )
    notes = notes_from_settings(settings)
    assert len(notes) == 1
    assert "local" in notes[0].subject
    assert "not in effect" in notes[0].detail, "the consequence, not just the cause"


def test_a_healthy_settings_object_produces_no_notes(tmp_path: Path) -> None:
    assert notes_from_settings(settings_for(tmp_path)) == ()


def test_an_unavailable_sandbox_is_a_note_and_a_working_one_is_not() -> None:
    assert sandbox_note(NoSandbox()) is None
    note = sandbox_note(Unavailable(reason="no backend found", looked_for=("bwrap",)))
    assert note is not None
    assert "unsandboxed" in note.detail, "the user must know commands are not contained"


def test_sequence_note_is_none_when_nothing_was_skipped() -> None:
    assert sequence_note("mcp", (), detail="skipped") is None
    note = sequence_note("mcp", ("a", "b"), detail="servers skipped")
    assert note is not None
    assert "a, b" in note.detail


def test_healthy_is_false_only_for_a_fatal_note_or_a_broken_layer(tmp_path: Path) -> None:
    base = loaded_with(tmp_path, memory=Memory(), repo_map=RepoMap())
    assert base.healthy

    survivable = base.with_notes(Note(subject="tree-sitter", detail="python-only map"))
    assert survivable.healthy, "a degradation that still works is not unhealthy"

    fatal = base.with_notes(Note(subject="x", detail="y", fatal=True))
    assert not fatal.healthy


def test_with_notes_appends_rather_than_replaces(tmp_path: Path) -> None:
    base = loaded_with(tmp_path, memory=Memory(), repo_map=RepoMap())
    once = base.with_notes(Note(subject="a", detail="a happened"))
    twice = once.with_notes(Note(subject="b", detail="b happened"))
    assert [note.subject for note in twice.notes] == ["a", "b"]
    assert base.notes == (), "frozen: the original is untouched"


def test_render_shows_the_notes_a_human_needs(tmp_path: Path) -> None:
    loaded = loaded_with(tmp_path, memory=a_memory(), repo_map=a_map(entries=1)).with_notes(
        Note(subject="sandbox", detail="no backend; commands run unsandboxed")
    )
    rendered = loaded.render()
    assert "sandbox" in rendered
    assert "unsandboxed" in rendered
    assert "RONIN.md" in rendered
