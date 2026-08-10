"""``ronin eval`` / ``ronin duel`` end to end: argv → selection → run → report → exit.

Lives in ``tests/evalsuite/`` rather than ``tests/cli/`` because ``evals_harness`` is
here and pytest's per-directory sys.path insertion does not reach across test
directories — importing it from ``tests/cli`` raises ``ModuleNotFoundError``. The
alternative was a second copy of the scripted agent, and two fakes for one seam drift.

The model is always scripted. Two things are therefore actually verified rather than
assumed: that ``--dry-run`` reaches a task list with no provider in the picture at all,
and that the *running* path — the one that matters — produces a report, writes the
files it was asked for, and returns the exit code a script would branch on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals_harness import CONDITIONAL_VERIFY, Step, scripted_factory, write_manifest, write_task

from ronin.cli.bench import (
    BenchOptions,
    Failed,
    default_suite,
    manifest_note,
    render_dry_run,
    router_for,
    run_duel_command,
    run_eval,
    runner_config,
    select,
)
from ronin.cli.spine import Paths

BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"


def build_suite(root: Path, *, manifest: bool = True) -> None:
    """Two solvable tasks, one of them in the regression gate."""
    write_task(
        root,
        "fix-add",
        category="single-file",
        prompt="make add return the sum",
        fixture={"app.py": BROKEN},
        solution={"app.py": FIXED},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
        extra_toml='regression_gate = true\ntags = ["quick"]\n',
    )
    write_task(
        root,
        "also-add",
        category="multi-file",
        prompt="make add return the sum here too",
        fixture={"app.py": BROKEN},
        solution={"app.py": FIXED},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
    )
    if manifest:
        write_manifest(root, ["fix-add", "also-add"])


def options(suite: Path, **kwargs: object) -> BenchOptions:
    return BenchOptions(suite=suite, **kwargs)  # type: ignore[arg-type]


def fixer() -> object:
    """A scripted model that writes the correct answer. Passes both tasks."""
    return scripted_factory([Step(tool="edit", path="app.py", content=FIXED)])


def misser() -> object:
    """A scripted model that edits the wrong file. Fails both, and is classifiable."""
    return scripted_factory([Step(tool="edit", path="notes.md", content="hi\n")])


# --------------------------------------------------------------------------- #
# Selecting
# --------------------------------------------------------------------------- #


def test_the_default_suite_is_relative_to_the_workspace_not_the_process(
    tmp_path: Path,
) -> None:
    """A hard-coded ``tests/evals`` would be wrong the moment ``--cwd`` is passed."""
    (tmp_path / ".git").mkdir()
    paths = Paths.discover(tmp_path)
    assert default_suite(paths) == tmp_path / "tests" / "evals"


def test_a_missing_suite_says_so_rather_than_reporting_zero_tasks(tmp_path: Path) -> None:
    """"0 tasks, 0 passed" over a nonexistent directory is a 100%-shaped lie."""
    outcome = select(options(tmp_path / "nope"))
    assert isinstance(outcome, Failed)
    assert "no eval suite at" in outcome.message


def test_an_empty_suite_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    outcome = select(options(suite))
    assert isinstance(outcome, Failed)
    assert "no task.toml" in outcome.message


def test_a_selection_that_matches_nothing_repeats_what_was_asked_for(
    tmp_path: Path,
) -> None:
    """The most common mistake is a typo'd id; echoing it is the whole diagnostic."""
    suite = tmp_path / "suite"
    build_suite(suite)
    outcome = select(options(suite, ids=frozenset({"single-file/typo"})))
    assert isinstance(outcome, Failed)
    assert "matched none of the 2 tasks" in outcome.message
    assert "single-file/typo" in outcome.message


def test_limit_truncates_rather_than_failing(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)
    outcome = select(options(suite, limit=1))
    assert not isinstance(outcome, Failed)
    assert len(outcome) == 1


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ["fix-add", "also-add"]),
        ({"categories": frozenset({"single-file"})}, ["fix-add"]),
        ({"tags": frozenset({"quick"})}, ["fix-add"]),
        ({"regression_gate": True}, ["fix-add"]),
        ({"ids": frozenset({"also-add"})}, ["also-add"]),
    ],
    ids=["all", "category", "tag", "gate", "id"],
)
def test_every_filter_reaches_the_loader(
    tmp_path: Path, kwargs: dict[str, object], expected: list[str]
) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)
    chosen = select(options(suite, **kwargs))
    assert not isinstance(chosen, Failed)
    assert sorted(task.id for task in chosen) == sorted(expected)


# --------------------------------------------------------------------------- #
# --dry-run: no model, no key, no network
# --------------------------------------------------------------------------- #


async def test_dry_run_lists_the_tasks_and_opens_nothing(tmp_path: Path) -> None:
    """The branch a reader with no API key can actually try.

    ``router=None`` and no ``factory``: if this path touched a provider it would raise
    rather than return, so the assertion that it returns 0 *is* the assertion that
    nothing was opened.
    """
    suite = tmp_path / "suite"
    build_suite(suite)
    code, out, err = await run_eval(
        options(suite, dry_run=True), Paths.discover(tmp_path), {}
    )
    assert code == 0
    assert "2 task(s) selected" in out
    assert "fix-add" in out and "also-add" in out
    assert "nothing ran" in out
    assert err == ""


async def test_dry_run_works_for_duel_too_without_two_models(tmp_path: Path) -> None:
    """Otherwise the flag would demand exactly the thing it exists to avoid needing."""
    suite = tmp_path / "suite"
    build_suite(suite)
    code, out, _ = await run_duel_command(
        options(suite, dry_run=True), Paths.discover(tmp_path), {}
    )
    assert code == 0
    assert "2 task(s) selected" in out


def test_the_dry_run_turn_ceiling_is_called_a_ceiling(tmp_path: Path) -> None:
    """It is the sum of ``max_turns``, which is not a prediction of anything.

    Printing it as an estimate would have someone budget against a number that is
    typically several times the real usage.
    """
    suite = tmp_path / "suite"
    build_suite(suite)
    chosen = select(options(suite))
    assert not isinstance(chosen, Failed)
    rendered = render_dry_run(chosen, options(suite))
    assert "ceiling" in rendered
    assert "not an estimate" in rendered


# --------------------------------------------------------------------------- #
# The running path
# --------------------------------------------------------------------------- #


async def test_a_passing_run_reports_and_exits_zero(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)
    code, out, _ = await run_eval(
        options(suite, models=("scripted",)),
        Paths.discover(tmp_path),
        {},
        factory=fixer(),  # type: ignore[arg-type]
    )
    assert code == 0
    assert "pass rate: 100.0%" in out


async def test_a_failing_run_still_exits_zero_because_a_measurement_succeeded(
    tmp_path: Path,
) -> None:
    """Exit 1 means "could not run the suite", never "the model scored badly".

    A CI step that treats a 40% pass rate as a command failure cannot tell a broken
    harness from a weak model, which is the exact distinction the taxonomy exists for.
    """
    suite = tmp_path / "suite"
    build_suite(suite)
    code, out, _ = await run_eval(
        options(suite, models=("scripted",)),
        Paths.discover(tmp_path),
        {},
        factory=misser(),  # type: ignore[arg-type]
    )
    assert code == 0
    assert "pass rate: 0.0%" in out


async def test_json_and_markdown_land_where_they_were_asked_to(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)
    json_out = tmp_path / "reports" / "run.json"
    md_out = tmp_path / "reports" / "run.md"
    code, _, err = await run_eval(
        options(suite, models=("scripted",), json_out=json_out, markdown_out=md_out),
        Paths.discover(tmp_path),
        {},
        factory=fixer(),  # type: ignore[arg-type]
    )
    assert code == 0
    # Parent directories are created: asking for a path in a fresh reports/ dir and
    # getting a traceback after a paid run would be a cruel way to lose the results.
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["label"] == "scripted"
    assert {entry["id"] for entry in payload["tasks"]} == {"fix-add", "also-add"}
    assert "pass rate" in md_out.read_text(encoding="utf-8")
    assert str(json_out) in err and str(md_out) in err


async def test_the_model_name_is_carried_into_the_report_as_a_caption(
    tmp_path: Path,
) -> None:
    """``--model`` labels the run; it never selects a model when a factory is injected.

    Which is the point of the split: a mislabelled run is a wrong caption, never a
    wrong measurement.
    """
    suite = tmp_path / "suite"
    build_suite(suite)
    config = runner_config(options(suite, models=("qwen-local",), parallel=4), "qwen-local")
    assert config.model == "qwen-local"
    assert config.label == "qwen-local"
    assert config.parallel == 4
    assert config.suite_root == str(suite)


async def test_two_models_are_refused_by_eval_with_a_pointer_to_duel(
    tmp_path: Path,
) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)
    code, _, err = await run_eval(
        options(suite, models=("a", "b")), Paths.discover(tmp_path), {}
    )
    assert code == 1
    assert "ronin duel" in err


async def test_a_duel_against_itself_is_refused(tmp_path: Path) -> None:
    """Same model both sides measures sampling noise, which is not what this reports."""
    suite = tmp_path / "suite"
    build_suite(suite)
    code, _, err = await run_duel_command(
        options(suite, models=("same", "same")), Paths.discover(tmp_path), {}
    )
    assert code == 1
    assert "against itself" in err


async def test_a_duel_runs_both_sides_and_renders_a_scoreboard(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    build_suite(suite)

    def duelists(name: str, seed: int) -> object:
        del seed
        return fixer() if name == "good" else misser()

    code, out, _ = await run_duel_command(
        options(suite, models=("good", "bad"), seed=7),
        Paths.discover(tmp_path),
        {},
        factory=duelists,  # type: ignore[arg-type]
    )
    assert code == 0
    assert "good" in out and "bad" in out


async def test_a_duel_writes_both_reports_into_one_json(tmp_path: Path) -> None:
    """One file, both sides, and the seed — so a duel is reproducible from its output."""
    suite = tmp_path / "suite"
    build_suite(suite)

    def duelists(name: str, seed: int) -> object:
        del seed
        return fixer() if name == "good" else misser()

    json_out = tmp_path / "duel.json"
    code, _, _ = await run_duel_command(
        options(suite, models=("good", "bad"), seed=7, json_out=json_out),
        Paths.discover(tmp_path),
        {},
        factory=duelists,  # type: ignore[arg-type]
    )
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["seed"] == 7
    assert payload["duelists"] == ["good", "bad"]
    assert len(payload["reports"]) == 2


# --------------------------------------------------------------------------- #
# The manifest note
# --------------------------------------------------------------------------- #


def test_a_manifest_that_agrees_with_the_tree_says_nothing(tmp_path: Path) -> None:
    """A note on every clean run is a note nobody reads."""
    suite = tmp_path / "suite"
    build_suite(suite)
    chosen = select(options(suite))
    assert not isinstance(chosen, Failed)
    assert manifest_note(options(suite), chosen) == ""


def test_a_task_missing_from_the_manifest_is_reported_but_not_fatal(
    tmp_path: Path,
) -> None:
    """Mid-migration a contributor must still be able to run what exists.

    Enforcing here would make the manifest a gate on experimentation instead of what
    it is: the thing that makes a *deleted* fixture detectable in CI.
    """
    suite = tmp_path / "suite"
    build_suite(suite)
    write_manifest(suite, ["fix-add"])  # drops also-add
    chosen = select(options(suite))
    assert not isinstance(chosen, Failed), "still runnable"
    note = manifest_note(options(suite), chosen)
    assert "also-add" in note
    assert "gen_eval_manifest" in note


def test_a_suite_with_no_manifest_at_all_still_runs_and_says_why(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    build_suite(suite, manifest=False)
    chosen = select(options(suite))
    assert not isinstance(chosen, Failed)
    assert "manifest.toml" in manifest_note(options(suite), chosen)


# --------------------------------------------------------------------------- #
# The router
# --------------------------------------------------------------------------- #


CONFIG = """\
[roles]
main = "alpha"

[models.alpha]
provider = "anthropic"
model = "alpha-1"
api_key_env = "ALPHA_KEY"

[models.beta]
provider = "anthropic"
model = "beta-1"
api_key_env = "BETA_KEY"
"""


def workspace_with_config(tmp_path: Path) -> Path:
    """A real workspace holding a real router config, written to disk.

    Written rather than injected: the branch under test is the one that *loads* a
    config, and a test that hands ``router_for`` a finished Router cannot reach it.
    """
    (tmp_path / ".git").mkdir()
    ronin = tmp_path / ".ronin"
    ronin.mkdir()
    (ronin / "models.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def test_an_unknown_model_name_lists_the_defined_ones(tmp_path: Path) -> None:
    """The message has to be actionable: "not found" alone sends someone reading code.

    ``--model`` names an entry in the config's ``[models]`` table, not a provider's
    model id — accepting an id would mean inventing a price for it, and an invented
    price is a wrong number in someone's cost report.
    """
    root = workspace_with_config(tmp_path)
    outcome = router_for(
        options(root), Paths.discover(root), {"ALPHA_KEY": "k"}, "claude-sonnet-4-6"
    )
    assert isinstance(outcome, Failed)
    assert "claude-sonnet-4-6" in outcome.message
    assert "alpha" in outcome.message and "beta" in outcome.message
    assert "not a provider's model id" in outcome.message


def test_a_known_model_name_is_promoted_to_the_main_role(tmp_path: Path) -> None:
    """The whole mechanism: ``--model beta`` makes ``beta`` the model that runs.

    Asserted on the built router rather than on the config, because a repointing that
    does not survive ``Router.__init__`` is a repointing that does nothing.
    """
    from ronin.providers.router import Role

    root = workspace_with_config(tmp_path)
    built = router_for(
        options(root), Paths.discover(root), {"ALPHA_KEY": "k", "BETA_KEY": "k"}, "beta"
    )
    assert not isinstance(built, Failed)
    assert built.spec_for(Role.MAIN).model == "beta-1"


def test_no_model_leaves_the_config_alone(tmp_path: Path) -> None:
    """``eval --dry-run`` aside, a run with no ``--model`` uses whatever main already is.

    Silently repointing to something else would make the report's caption disagree
    with the model that produced it.
    """
    from ronin.providers.router import Role

    root = workspace_with_config(tmp_path)
    built = router_for(options(root), Paths.discover(root), {"ALPHA_KEY": "k"}, "")
    assert not isinstance(built, Failed)
    assert built.spec_for(Role.MAIN).model == "alpha-1"


def test_an_injected_router_wins_over_the_config_on_disk(tmp_path: Path) -> None:
    """The test seam, asserted as such rather than dressed up as a name check."""
    from ronin.providers.router import ModelSpec, Role, Router, RouterConfig

    config = RouterConfig(
        roles={Role.MAIN: "injected"},
        models={"injected": ModelSpec(name="injected", provider="p", model="m-1")},
    )
    root = workspace_with_config(tmp_path)
    built = router_for(
        options(root, router=Router(config, env={})), Paths.discover(root), {}, "beta"
    )
    assert not isinstance(built, Failed)
    assert built.spec_for(Role.MAIN).model == "m-1"


def test_a_missing_router_config_is_reported_not_defaulted(tmp_path: Path) -> None:
    """Ronin ships no default provider: a made-up price is a wrong number in a ledger."""
    (tmp_path / ".git").mkdir()
    outcome = router_for(
        options(tmp_path), Paths.discover(tmp_path), {"HOME": str(tmp_path)}, "anything"
    )
    assert isinstance(outcome, Failed)
    assert "no model configuration found" in outcome.message
