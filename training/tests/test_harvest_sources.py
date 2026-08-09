"""Loading recorded runs, and the consent boundary around real user sessions.

The consent tests are written to fail if the code ever *could* read an un-named
directory, not merely if it does. That means asserting on the refusal happening before
any reader is called, and on the module source containing no default location at all —
a default that is currently unused is a default that gets used later.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from ronin_training.harvest import sources as sources_module
from ronin_training.harvest.provenance import SourceKind
from ronin_training.harvest.sources import (
    HarvestError,
    RunManifest,
    SessionHarvest,
    load_run,
    load_runs,
    load_sessions,
    read_transcript_events,
    steps_from_events,
)
from test_harvest_helpers import load_recorded, registry_tools, turn, write_transcript

_TOOLS = registry_tools("read_file", "write_file")


# --------------------------------------------------------------------------- #
# Consent: opt-in, and no path read without being passed
# --------------------------------------------------------------------------- #


def test_real_session_harvesting_is_off_by_default():
    config = SessionHarvest()
    assert config.enabled is False
    assert config.session_dir is None
    with pytest.raises(HarvestError, match="real-session harvesting is off"):
        config.resolve()


def test_no_transcript_is_read_when_harvesting_is_not_enabled(tmp_path):
    """The refusal must happen before the reader is touched, not after."""
    reads: list[Path] = []

    def recording_reader(path: Path) -> list[object]:
        reads.append(path)
        return []

    manifests = [
        RunManifest(task_id="t", run_id="r", transcript=tmp_path / "s.jsonl", prompt="p")
    ]
    with pytest.raises(HarvestError):
        load_sessions(SessionHarvest(), manifests, read=recording_reader)
    assert reads == []


def test_enabling_without_a_directory_is_refused_and_no_directory_is_guessed():
    with pytest.raises(HarvestError, match="no session_dir was given"):
        SessionHarvest(enabled=True, scrub=str).resolve()


def test_enabling_without_a_scrubber_is_refused(tmp_path):
    with pytest.raises(HarvestError, match="needs a scrub callable"):
        SessionHarvest(enabled=True, session_dir=tmp_path).resolve()


def test_the_module_hardcodes_no_default_session_location():
    """A default pointing at ~/.ronin (or cwd, or an env var) is the failure mode this
    guards: harmless while unused, and the whole consent story once someone uses it."""
    source = Path(sources_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("expanduser", "Path.home()", ".ronin/sessions", "os.environ", "getenv"):
        assert forbidden not in source, f"{forbidden!r} appears in sources.py"


def test_an_opted_in_session_is_read_scrubbed_and_labelled(tmp_path):
    events = turn(
        index=0,
        text="Mailing ada@example.com about it.",
        calls=[("c0", "read_file", {"path": "a.py"})],
        results=[("c0", "read_file", True, "contact: ada@example.com", "")],
    )
    path = write_transcript(tmp_path, "session-1", events)
    config = SessionHarvest(
        enabled=True,
        session_dir=tmp_path,
        scrub=lambda text: text.replace("ada@example.com", "[EMAIL]"),
    )
    manifests = [
        RunManifest(
            task_id="t",
            run_id="session-1",
            transcript=path,
            prompt="check a.py for ada@example.com",
            tools=_TOOLS,
            verified=True,
        )
    ]
    (traj,) = load_sessions(config, manifests, read=read_transcript_events)
    assert traj.source is SourceKind.SESSION
    assert "ada@example.com" not in traj.prompt
    assert "ada@example.com" not in traj.steps[0].text
    assert "ada@example.com" not in traj.steps[0].results[0].content
    assert "[EMAIL]" in traj.steps[0].results[0].content


def test_a_transcript_outside_the_opted_in_directory_is_refused(tmp_path):
    inside = tmp_path / "allowed"
    outside = tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    config = SessionHarvest(enabled=True, session_dir=inside, scrub=str)
    manifests = [
        RunManifest(
            task_id="t", run_id="r", transcript=outside / "s.jsonl", prompt="p", tools=_TOOLS
        )
    ]
    with pytest.raises(HarvestError, match="outside the directory that was opted in"):
        load_sessions(config, manifests, read=read_transcript_events)


def test_the_scrubber_can_be_the_projects_own_redactor(tmp_path):
    """Proves the wiring works with the real ``ronin_training.redaction`` module, so the
    documented usage is not aspirational."""
    from ronin_training.redaction import redact

    events = turn(index=0, text="key sk-abcdefghijklmnop and mail bob@corp.io")
    path = write_transcript(tmp_path, "session-2", events)
    config = SessionHarvest(
        enabled=True, session_dir=tmp_path, scrub=lambda text: redact(text).text
    )
    (traj,) = load_sessions(
        config,
        [RunManifest(task_id="t", run_id="session-2", transcript=path, prompt="hi", tools=_TOOLS)],
        read=read_transcript_events,
    )
    assert "bob@corp.io" not in traj.steps[0].text
    assert "sk-abcdefghijklmnop" not in traj.steps[0].text


# --------------------------------------------------------------------------- #
# Folding a recorded event stream into turns
# --------------------------------------------------------------------------- #


def test_a_recorded_transcript_folds_into_one_step_per_turn(tmp_path):
    events = [
        *turn(index=0, text="first", calls=[("c0", "read_file", {"path": "a.py"})],
              results=[("c0", "read_file", True, "A", "")]),
        *turn(index=1, text="second"),
    ]
    traj = load_recorded(tmp_path, "run-2", events, task_id="t", prompt="p", tools=_TOOLS)
    assert [s.index for s in traj.steps] == [0, 1]
    assert traj.steps[0].text == "first"
    assert traj.steps[0].calls[0].name == "read_file"
    assert traj.steps[1].calls == ()


def test_a_restreamed_turn_discards_the_text_that_was_replaced():
    """StreamReset means the provider re-streamed; keeping the abandoned text would
    train a turn the model never finally said."""
    from ronin.core.types import StreamReset, TextDelta, TurnEnd, TurnStart, TurnState

    events = [
        TurnStart(turn_index=0),
        TextDelta(text="wrong start"),
        StreamReset(reason="provider retry"),
        TextDelta(text="the real answer"),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    ]
    (step,) = steps_from_events(events)
    assert step.text == "the real answer"


def test_thinking_text_is_not_folded_into_the_visible_turn():
    from ronin.core.types import TextDelta, TurnEnd, TurnStart, TurnState

    events = [
        TurnStart(turn_index=0),
        TextDelta(text="hmm, maybe", thinking=True),
        TextDelta(text="here it is"),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    ]
    (step,) = steps_from_events(events)
    assert step.text == "here it is"


def test_an_unterminated_final_turn_is_still_recovered():
    """A crash before TurnEnd must not lose the turn — that is the run most worth
    mining, since an aborted run is usually a failing one."""
    from ronin.core.types import TextDelta, TurnStart

    events = [TurnStart(turn_index=0), TextDelta(text="mid-turn crash")]
    (step,) = steps_from_events(events)
    assert step.text == "mid-turn crash"


def test_a_run_with_no_recoverable_prompt_is_refused(tmp_path):
    path = write_transcript(tmp_path, "run-3", turn(index=0, text="hello"))
    with pytest.raises(HarvestError, match="no task prompt"):
        load_run(
            RunManifest(task_id="t", run_id="run-3", transcript=path, tools=_TOOLS),
            read=read_transcript_events,
        )


def test_a_run_that_did_not_record_a_verify_outcome_is_treated_as_unverified(tmp_path):
    """Unknown means dropped: the harvester never runs verification itself, so an
    unrecorded outcome cannot be assumed green."""
    traj = load_recorded(tmp_path, "run-4", turn(index=0, text="x"), task_id="t",
                         prompt="p", tools=_TOOLS)
    assert traj.verified is False


def test_load_runs_loads_a_whole_sweep(tmp_path):
    manifests = []
    for i in range(3):
        path = write_transcript(
            tmp_path,
            f"run-{i}",
            turn(index=0, calls=[("c0", "read_file", {"path": "a.py"})],
                 results=[("c0", "read_file", True, "A", "")]),
        )
        manifests.append(
            RunManifest(
                task_id="t", run_id=f"run-{i}", transcript=path, prompt="p",
                tools=_TOOLS, verified=True,
            )
        )
    trajectories = load_runs(manifests, read=read_transcript_events)
    assert [t.run_id for t in trajectories] == ["run-0", "run-1", "run-2"]
    assert all(t.verified for t in trajectories)


def test_the_default_reader_is_the_real_persistence_reader(tmp_path):
    """Exercised through the real module rather than a fake, so a change in the
    persisted format shows up here."""
    path = write_transcript(
        tmp_path,
        "run-5",
        turn(index=0, calls=[("c0", "read_file", {"path": "a.py"})],
             results=[("c0", "read_file", True, "A", "")]),
    )
    traj = load_run(
        RunManifest(task_id="t", run_id="run-5", transcript=path, prompt="p", tools=_TOOLS)
    )
    assert traj.steps[0].results[0].content == "A"


def _agent_tree_imports(tree: ast.AST) -> list[str]:
    """Every ``ronin.*`` import in ``tree``. ``ronin_dialect``/``ronin_training`` are
    this package's own siblings and are not the agent tree."""
    names: list[str] = []
    def is_agent_tree(name: str) -> bool:
        return name == "ronin" or name.startswith("ronin.")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names if is_agent_tree(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and is_agent_tree(node.module):
            names.append(node.module)
    return names


def test_the_agent_tree_is_never_imported_at_module_scope():
    """The training package installs on a bare Colab runtime where only ``training/``
    is importable, so every ``ronin.*`` import must be inside the function that needs it."""
    package = Path(sources_module.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level = [
            name
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in _agent_tree_imports(node)
        ]
        assert top_level == [], f"{path.name} imports {top_level} at module scope"


def test_the_pure_pipeline_modules_never_import_the_agent_tree_at_all():
    """These modules must run with no ``ronin`` package present, not merely import."""
    package = Path(sources_module.__file__).parent
    for module in ("trajectory", "prompt", "filters", "augment", "sft", "negatives", "dpo"):
        tree = ast.parse((package / f"{module}.py").read_text(encoding="utf-8"))
        assert _agent_tree_imports(tree) == [], module


# --------------------------------------------------------------------------- #
# The eval-suite seam
# --------------------------------------------------------------------------- #


def test_a_manifest_is_built_from_the_eval_suites_real_run_record(tmp_path):
    """Against agent B's actual ``RunRecord``, so the verify gate is read from the
    eval runner's own ``verify.sh`` result rather than recomputed here."""
    from ronin_training.harvest.sources import manifest_from_run_record

    from ronin.evals.taxonomy import AgentOutcome, RunRecord, VerifyProbe

    record = RunRecord(
        task_id="bump-version",
        category="single-file",
        prompt="set VERSION to 2",
        outcome=AgentOutcome(turns_used=3),
        verify_after=VerifyProbe(ran=True, exit_code=0, output="ok"),
    )
    transcript = tmp_path / "run.jsonl"
    manifest = manifest_from_run_record(
        record, transcript, run_id="run-1", tools=_TOOLS, model="fixture-model"
    )
    assert manifest.task_id == "bump-version"
    assert manifest.prompt == "set VERSION to 2"
    assert manifest.verified is True
    assert manifest.tools == _TOOLS
    assert manifest.extra["turns_used"] == 3


def test_a_run_whose_verify_failed_or_never_ran_is_not_marked_verified(tmp_path):
    from ronin_training.harvest.sources import manifest_from_run_record

    from ronin.evals.taxonomy import RunRecord, VerifyProbe

    failed = RunRecord(task_id="t", prompt="p", verify_after=VerifyProbe(ran=True, exit_code=1))
    absent = RunRecord(task_id="t", prompt="p")
    for record in (failed, absent):
        manifest = manifest_from_run_record(record, tmp_path / "r.jsonl", run_id="r")
        assert manifest.verified is False


def test_the_seam_accepts_openai_shaped_tool_dicts_too(tmp_path):
    """The runner records ``registered_tools`` as names only, so a caller supplies the
    schemas from whichever registry the run used — either shape."""
    from ronin_training.harvest.sources import manifest_from_run_record

    from ronin.evals.taxonomy import RunRecord

    manifest = manifest_from_run_record(
        RunRecord(task_id="t", prompt="p"),
        tmp_path / "r.jsonl",
        run_id="r",
        tools=[{"name": "read_file", "description": "read a file", "parameters": {}}],
    )
    assert manifest.tools[0].name == "read_file"
