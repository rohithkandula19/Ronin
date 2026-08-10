"""The first-run wizard: plan says exactly what would happen, apply does exactly that.

The property under test throughout is that the two halves agree and that neither is
destructive: ``plan_first_run`` touches nothing, ``apply_plan`` writes only what the
plan said it would write, and an existing file is never overwritten — a first run that
clobbers somebody's RONIN.md is data loss with a friendly banner.
"""

from __future__ import annotations

import json
from pathlib import Path

from wire_harness import full_workspace, workspace, write

from ronin.cli.wire import load_workspace
from ronin.cli.wizard import STARTER_PROJECT_SETTINGS, apply_plan, plan_first_run


def test_planning_writes_nothing(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())

    plan = plan_first_run(paths)

    assert plan.writes
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not paths.ronin_dir.exists()


def test_an_empty_workspace_gets_a_drafted_ronin_md_and_a_project_settings_file(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)

    plan = plan_first_run(paths)
    written = apply_plan(plan)

    assert written == (paths.project_settings, tmp_path / "RONIN.md")
    assert paths.ronin_dir.is_dir()
    assert paths.sessions_dir.is_dir()
    assert paths.cache_dir.is_dir()
    body = (tmp_path / "RONIN.md").read_text(encoding="utf-8")
    assert body.startswith("# RONIN.md")
    # Nothing invented: an absent command is reported as absent, per section.
    assert "none found: no test command appears in" in body
    assert json.loads(paths.project_settings.read_text(encoding="utf-8")) == {
        "mode": "ask",
        "rules": [],
    }


def test_the_draft_carries_the_commands_it_actually_found_with_their_evidence(
    tmp_path: Path,
) -> None:
    write(tmp_path, "Makefile", "test:\n\tpytest -q\n\nlint:\n\truff check .\n")
    paths = workspace(tmp_path)

    plan = plan_first_run(paths)
    apply_plan(plan)

    body = (tmp_path / "RONIN.md").read_text(encoding="utf-8")
    assert "`make test`" in body
    assert "found in Makefile target 'test'" in body
    assert "`make lint`" in body
    # ...and the plan said as much before anything was written.
    memory = plan.files[0]
    assert "drafted from 2 command(s)" in memory.reason


def test_an_existing_ronin_md_is_reported_as_present_and_never_overwritten(
    tmp_path: Path,
) -> None:
    paths = full_workspace(tmp_path)
    original = (tmp_path / "RONIN.md").read_bytes()

    plan = plan_first_run(paths)
    written = apply_plan(plan)

    memory = next(f for f in plan.files if f.path.name == "RONIN.md")
    assert memory.exists
    assert not memory.will_write
    assert "already exists" in memory.line()
    assert (tmp_path / "RONIN.md").read_bytes() == original
    assert (tmp_path / "RONIN.md") not in written


def test_re_running_the_wizard_on_a_finished_workspace_is_a_no_op(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    apply_plan(plan_first_run(paths))

    second = plan_first_run(paths)

    assert second.empty
    assert apply_plan(second) == ()
    assert "nothing to do" in second.render()


def test_the_home_layer_is_only_created_on_request(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    user_dir = paths.home / ".ronin"

    apply_plan(plan_first_run(paths))
    assert not user_dir.exists()

    plan = plan_first_run(paths, include_user_layer=True)
    assert user_dir in plan.directories
    apply_plan(plan)
    assert user_dir.is_dir()


def test_the_plan_says_which_lines_to_add_to_gitignore_and_does_not_add_them(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)

    plan = plan_first_run(paths)
    apply_plan(plan)

    assert not (tmp_path / ".gitignore").exists()
    joined = "\n".join(plan.notes)
    assert ".ronin/settings.local.json" in joined
    assert "does not edit .gitignore" in joined


def test_either_half_can_be_turned_off(tmp_path: Path) -> None:
    paths = workspace(tmp_path)

    memory_only = plan_first_run(paths, project_settings=False)
    settings_only = plan_first_run(paths, memory=False)

    assert [f.path.name for f in memory_only.files] == ["RONIN.md"]
    assert [f.path.name for f in settings_only.files] == ["settings.json"]


def test_what_the_wizard_writes_loads_back_as_a_clean_workspace(tmp_path: Path) -> None:
    """The point of the wizard: the files it creates are the files the loader reads."""
    paths = workspace(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.ruff]\nline-length = 100\n")

    apply_plan(plan_first_run(paths))
    loaded = load_workspace(paths)

    assert loaded.settings.healthy
    assert loaded.settings.source_of("mode") == "project"
    assert not loaded.memory.is_empty
    assert "memory" not in [note.subject for note in loaded.notes]
    assert loaded.verify.lint is not None


def test_files_are_written_with_lf_endings(tmp_path: Path) -> None:
    paths = workspace(tmp_path)

    apply_plan(plan_first_run(paths))

    # Read as bytes: `read_text` would translate CRLF away and hide the regression.
    assert b"\r\n" not in (tmp_path / "RONIN.md").read_bytes()
    assert b"\r\n" not in paths.project_settings.read_bytes()


def test_the_starter_settings_file_is_the_builtin_default_and_a_place_to_edit() -> None:
    body = json.loads(STARTER_PROJECT_SETTINGS)

    # Writing anything opinionated here would be a policy nobody chose; the file exists
    # so a team has somewhere attributable to put a rule.
    assert body == {"mode": "ask", "rules": []}
    assert STARTER_PROJECT_SETTINGS.endswith("\n")
