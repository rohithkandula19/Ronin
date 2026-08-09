"""``/doctor``: does it find the real problems, and does every finding carry a fix.

The two assertions that matter most here are structural rather than about any one
check: a non-``ok`` :class:`~ronin.cli.doctor.Check` cannot be constructed without a
remedy, and the exit code is non-zero only when something is genuinely broken. A
diagnostic that fails on the normal case gets ``|| true``'d and then nobody reads it.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from wire_harness import (
    fake_checkpoint_store,
    fake_router,
    full_workspace,
    git_repo,
    keyed_router,
    mcp_config,
    no_binaries,
    which_for,
    workspace,
    write,
)

from ronin.cli.doctor import (
    GITIGNORE_PATCH,
    Check,
    CheckStatus,
    DoctorReport,
    gitignore_patch,
    gitignore_report,
    hidden_by,
    run_doctor,
)
from ronin.cli.wire import build_runtime, load_workspace
from ronin.context.repomap import GitIgnore

# The rule this repo shipped, and the one every project copies from a template.
BLANKET = "# ronin local config — holds plaintext API keys; NEVER commit it\n.ronin/\n"

GOOD = GITIGNORE_PATCH


def test_a_failing_check_cannot_exist_without_a_remedy() -> None:
    with pytest.raises(ValueError, match="no remedy"):
        Check(name="x", status=CheckStatus.FAIL, detail="broken")
    with pytest.raises(ValueError, match="no remedy"):
        Check(name="x", status=CheckStatus.WARN, detail="degraded")
    # An `ok` check needs none, because there is nothing to do.
    assert Check(name="x", status=CheckStatus.OK, detail="fine").remedy == ""


# --------------------------------------------------------------------------- #
# the .gitignore finding
# --------------------------------------------------------------------------- #


def test_a_blanket_ronin_rule_hides_the_config_a_team_is_supposed_to_share() -> None:
    finding = gitignore_report(BLANKET)

    assert ".ronin/settings.json" in finding.hidden_shared
    assert ".ronin/mcp.json" in finding.hidden_shared
    assert ".ronin/agents/reviewer.md" in finding.hidden_shared
    assert ".ronin/commands/ship.md" in finding.hidden_shared
    # The half it gets right: the plaintext-key file *is* ignored.
    assert finding.secret_tracked is False
    assert not finding.clean


def test_negating_individual_files_under_an_excluded_directory_does_not_work() -> None:
    # The fix everybody reaches for first. git will not re-include a file whose parent
    # directory is excluded, so this reads as correct and is not.
    text = BLANKET + "!.ronin/settings.json\n!.ronin/mcp.json\n"

    finding = gitignore_report(text)

    assert ".ronin/settings.json" in finding.hidden_shared
    assert ".ronin/mcp.json" in finding.hidden_shared


def test_the_recommended_patch_ignores_the_secret_and_shares_the_rest() -> None:
    finding = gitignore_report(GOOD)

    assert finding.hidden_shared == ()
    assert finding.secret_tracked is False
    assert finding.generated_tracked == ()
    assert finding.clean


def test_no_gitignore_at_all_leaves_the_plaintext_key_file_tracked() -> None:
    finding = gitignore_report("")

    assert finding.secret_tracked is True
    assert not finding.clean


def test_hidden_by_walks_ancestor_directories() -> None:
    ignore = GitIgnore.parse("build/\n")

    # `GitIgnore.ignores` answers about one path; the walker prunes directories, so
    # only the ancestor walk sees a file beneath an excluded one.
    assert ignore.ignores("build/out.js") is False
    assert hidden_by(ignore, "build/out.js") is True
    assert hidden_by(ignore, "src/out.js") is False


def test_the_patch_names_the_file_it_applies_to(tmp_path: Path) -> None:
    patch = gitignore_patch(tmp_path)

    assert str(tmp_path / ".gitignore") in patch
    assert ".ronin/settings.local.json" in patch


# --------------------------------------------------------------------------- #
# run_doctor
# --------------------------------------------------------------------------- #


async def test_a_healthy_workspace_exits_zero_and_says_so(tmp_path: Path) -> None:
    paths = full_workspace(tmp_path)
    write(tmp_path, ".gitignore", GOOD)
    loaded = load_workspace(paths, which=which_for("docs-server"))

    report = await run_doctor(
        loaded,
        router=fake_router(),
        environ={},
        which=which_for("docs-server"),
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    assert report.failures == ()
    assert report.exit_code() == 0
    assert report.healthy
    body = report.render()
    assert "ronin doctor" in body
    assert "checks" in body


async def test_an_unparseable_settings_layer_is_a_failure_that_names_the_file(
    tmp_path: Path,
) -> None:
    paths = full_workspace(tmp_path)
    write(tmp_path, ".ronin/settings.local.json", "{oops\n")
    loaded = load_workspace(paths)

    report = await run_doctor(
        loaded, router=fake_router(), checkpoints=fake_checkpoint_store(tmp_path)
    )

    failed = report.check("settings:local")
    assert failed is not None
    assert failed.status is CheckStatus.FAIL
    assert str(paths.local_settings) in failed.remedy
    assert report.exit_code() == 1


async def test_a_directory_that_is_not_a_git_repo_is_a_warning_with_git_init(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(tmp_path))

    report = await run_doctor(
        loaded, router=fake_router(), checkpoints=fake_checkpoint_store(tmp_path)
    )

    git = report.check("git")
    assert git is not None
    assert git.status is CheckStatus.WARN
    assert "git init" in git.remedy
    # A scratch directory is a legitimate workspace, so this must not fail the run.
    assert report.exit_code() == 0


async def test_an_unusable_checkpoint_store_reports_the_stores_own_remedy(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))

    report = await run_doctor(
        loaded,
        router=fake_router(),
        checkpoints=fake_checkpoint_store(tmp_path, ok=False),
    )

    check = report.check("checkpoints")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert "git is not installed" in check.remedy
    assert report.exit_code() == 0


async def test_a_usable_checkpoint_store_is_ok(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))

    report = await run_doctor(
        loaded, router=fake_router(), checkpoints=fake_checkpoint_store(tmp_path)
    )

    check = report.check("checkpoints")
    assert check is not None
    assert check.status is CheckStatus.OK
    assert ".ronin/checkpoints" in check.detail


async def test_an_mcp_command_that_is_not_on_path_fails_and_says_what_to_fix(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    mcp_config(tmp_path, "docs")
    loaded = load_workspace(paths)

    report = await run_doctor(
        loaded,
        router=fake_router(),
        which=no_binaries,
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    check = report.check("mcp:docs")
    assert check is not None
    assert check.status is CheckStatus.FAIL
    assert "docs-server" in check.detail
    assert str(paths.mcp_config) in check.remedy
    assert report.exit_code() == 1


async def test_a_remote_mcp_server_is_reported_without_being_contacted(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    write(
        tmp_path,
        ".ronin/mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "remote": {"transport": "http", "url": "https://mcp.internal/rpc"}
                }
            }
        ),
    )
    loaded = load_workspace(paths)

    report = await run_doctor(
        loaded,
        router=fake_router(),
        which=no_binaries,
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    check = report.check("mcp:remote")
    assert check is not None
    assert check.status is CheckStatus.OK
    assert "not contacted" in check.detail


async def test_a_missing_api_key_for_a_configured_role_is_a_failure(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))

    missing = await run_doctor(
        loaded,
        router=keyed_router("RONIN_TEST_KEY"),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
    )
    present = await run_doctor(
        loaded,
        router=keyed_router("RONIN_TEST_KEY"),
        environ={"RONIN_TEST_KEY": "sk-x"},
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    broken = missing.check("models")
    assert broken is not None
    assert broken.status is CheckStatus.FAIL
    assert "RONIN_TEST_KEY" in broken.detail
    assert missing.exit_code() == 1
    # With the variable set, the only remaining complaint is the unmapped roles.
    fixed = present.check("models")
    assert fixed is not None
    assert fixed.status is CheckStatus.WARN
    assert "plan, fast" in fixed.detail


async def test_an_unmapped_role_falls_back_to_main_and_is_only_a_warning(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))

    report = await run_doctor(
        loaded,
        router=fake_router(),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    check = report.check("models")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert "plan" in check.detail
    assert "main model's price" in check.remedy
    assert report.exit_code() == 0


async def test_without_a_router_the_model_check_says_it_was_not_run(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))

    report = await run_doctor(loaded, checkpoints=fake_checkpoint_store(tmp_path))

    check = report.check("models")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert "not checked" in check.detail


async def test_the_report_carries_the_loaded_notes_rather_than_re_deriving_them(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(tmp_path))

    report = await run_doctor(
        loaded, router=fake_router(), checkpoints=fake_checkpoint_store(tmp_path)
    )

    assert report.loaded is loaded
    body = report.render()
    for note in loaded.notes:
        assert note.line() in body


async def test_the_gitignore_check_prints_the_patch_for_this_repos_own_rule(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    write(tmp_path, ".gitignore", BLANKET)
    loaded = load_workspace(paths)

    report = await run_doctor(
        loaded, router=fake_router(), checkpoints=fake_checkpoint_store(tmp_path)
    )

    check = report.check("gitignore")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert ".ronin/settings.json" in check.detail
    assert ".ronin/settings.local.json" in check.remedy
    assert ".ronin/*" in check.remedy
    assert "!.ronin/settings.json" in check.remedy
    assert report.exit_code() == 0


def test_the_summary_distinguishes_broken_from_degraded() -> None:
    loaded_stub = object()
    healthy = DoctorReport(
        loaded=loaded_stub,  # type: ignore[arg-type]
        checks=(Check(name="a", status=CheckStatus.OK, detail="fine"),),
    )
    warned = DoctorReport(
        loaded=loaded_stub,  # type: ignore[arg-type]
        checks=(Check(name="a", status=CheckStatus.WARN, detail="meh", remedy="do x"),),
    )
    broken = DoctorReport(
        loaded=loaded_stub,  # type: ignore[arg-type]
        checks=(Check(name="a", status=CheckStatus.FAIL, detail="bad", remedy="do y"),),
    )

    assert healthy.summary() == "everything checked out"
    assert "1 warning(s)" in warned.summary()
    assert warned.exit_code() == 0
    assert "1 problem(s)" in broken.summary()
    assert broken.exit_code() == 1


async def test_a_live_runtime_supplies_its_own_router_and_checkpoint_store(
    tmp_path: Path,
) -> None:
    paths = full_workspace(tmp_path)
    write(tmp_path, ".gitignore", GOOD)
    loaded = load_workspace(paths)
    live = await build_runtime(loaded, fake_router(), record=False, connect_mcp=False)
    # The store is swapped for one whose git calls are answered in-process; what is
    # being asserted is that /doctor uses the *runtime's* store and router rather than
    # constructing its own, so it cannot disagree with the session about /undo.
    runtime = replace(live, checkpoints=fake_checkpoint_store(tmp_path))
    try:
        report = await run_doctor(
            loaded, runtime=runtime, environ={}, which=which_for("docs-server")
        )
    finally:
        await live.aclose()

    models = report.check("models")
    assert models is not None
    assert "claude-x" in models.detail
    checkpoints = report.check("checkpoints")
    assert checkpoints is not None
    assert str(runtime.checkpoints.git_dir) in checkpoints.detail
    assert report.exit_code() == 0
