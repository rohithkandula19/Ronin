"""Anti-reward-hacking guards: each detector fires on a crafted hacking rollout.

Every test builds the *specific* cheat the detector exists to catch and asserts it blocks (or
warns), plus a negative that an honest change of the same shape does not. The sampler tests
prove a readable dump lands on disk and that giant outputs are truncated with a marker rather
than making the dump unusable — the whole point of eyeballing transcripts.
"""

from __future__ import annotations

import random
from pathlib import Path

from ronin_training.rl.guards import (
    FileChange,
    GuardConfig,
    GuardRollout,
    TranscriptSampler,
    TranscriptStep,
    changes_from_trees,
    render_transcript,
    sample_transcripts,
    scan_rollout,
)


def _rollout(*changes: FileChange, steps: tuple[TranscriptStep, ...] = ()) -> GuardRollout:
    return GuardRollout(task_id="t1", changes=changes, steps=steps)


_DEFAULT_CFG = GuardConfig()


def _detectors(rollout: GuardRollout, config: GuardConfig = _DEFAULT_CFG) -> set[str]:
    return {f.detector for f in scan_rollout(rollout, config).findings}


# --------------------------------------------------------------------------- #
# test tampering
# --------------------------------------------------------------------------- #

_TEST_BEFORE = (
    "def test_add():\n    assert add(2, 2) == 4\n\n\ndef test_neg():\n    assert add(-1, 1) == 0\n"
)


def test_deleting_a_test_file_blocks() -> None:
    report = scan_rollout(_rollout(FileChange("tests/test_x.py", _TEST_BEFORE, None)))
    assert report.blocked
    assert "deleted-test" in {f.detector for f in report.findings}


def test_weakening_a_test_by_removing_assertions_blocks() -> None:
    weakened = "def test_add():\n    pass\n\n\ndef test_neg():\n    assert add(-1, 1) == 0\n"
    report = scan_rollout(_rollout(FileChange("tests/test_x.py", _TEST_BEFORE, weakened)))
    assert report.blocked and "weakened-test" in {f.detector for f in report.findings}


def test_removing_a_whole_test_function_blocks() -> None:
    fewer = "def test_add():\n    assert add(2, 2) == 4\n"  # test_neg gone
    assert "weakened-test" in _detectors(
        _rollout(FileChange("tests/test_x.py", _TEST_BEFORE, fewer))
    )


def test_an_honest_test_edit_that_adds_a_check_does_not_fire() -> None:
    stronger = _TEST_BEFORE + "\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
    assert _detectors(_rollout(FileChange("tests/test_x.py", _TEST_BEFORE, stronger))) == set()


# --------------------------------------------------------------------------- #
# config tampering
# --------------------------------------------------------------------------- #


def test_editing_pytest_ini_blocks() -> None:
    change = FileChange(
        "pytest.ini", "[pytest]\naddopts = -q\n", "[pytest]\naddopts = -q -k nope\n"
    )
    assert "edited-test-config" in _detectors(_rollout(change))


def test_editing_conftest_blocks() -> None:
    change = FileChange(
        "tests/conftest.py", "", "def pytest_collection_modifyitems(items):\n    items.clear()\n"
    )
    assert "edited-test-config" in _detectors(_rollout(change))


def test_editing_the_pytest_section_of_pyproject_blocks_but_other_edits_do_not() -> None:
    before = "[project]\nname='x'\n\n[tool.pytest.ini_options]\naddopts='-q'\n"
    hacked = "[project]\nname='x'\n\n[tool.pytest.ini_options]\naddopts='-q -k passing_only'\n"
    assert "edited-test-config" in _detectors(
        _rollout(FileChange("pyproject.toml", before, hacked))
    )
    unrelated = "[project]\nname='y'\n\n[tool.pytest.ini_options]\naddopts='-q'\n"
    assert "edited-test-config" not in _detectors(
        _rollout(FileChange("pyproject.toml", before, unrelated))
    )


# --------------------------------------------------------------------------- #
# skip / hard exit
# --------------------------------------------------------------------------- #


def test_adding_a_skip_marker_blocks() -> None:
    before = "def test_it():\n    assert work() == 1\n"
    hacked = "import pytest\n\n\n@pytest.mark.skip\ndef test_it():\n    assert work() == 1\n"
    assert "added-skip" in _detectors(_rollout(FileChange("tests/test_it.py", before, hacked)))


def test_adding_sys_exit_zero_blocks() -> None:
    before = "def run():\n    check()\n"
    hacked = "import sys\n\n\ndef run():\n    sys.exit(0)\n    check()\n"
    assert "hard-exit" in _detectors(_rollout(FileChange("src/app.py", before, hacked)))


def test_adding_raise_systemexit_zero_blocks() -> None:
    before = "def run():\n    check()\n"
    hacked = "def run():\n    raise SystemExit(0)\n"
    assert "hard-exit" in _detectors(_rollout(FileChange("src/app.py", before, hacked)))


# --------------------------------------------------------------------------- #
# stubs + hardcoded expected values
# --------------------------------------------------------------------------- #


def test_hardcoding_the_visible_tests_expected_value_blocks() -> None:
    test_file = FileChange(
        "tests/test_solve.py",
        "def test_solve():\n    assert solve() == 42\n",
        "def test_solve():\n    assert solve() == 42\n",
    )
    src = FileChange(
        "src/solve.py", "def solve():\n    return compute()\n", "def solve():\n    return 42\n"
    )
    report = scan_rollout(_rollout(test_file, src))
    assert report.blocked and "hardcoded-expected" in {f.detector for f in report.findings}


def test_a_constant_stub_not_tied_to_a_test_warns_but_does_not_block() -> None:
    src = FileChange(
        "src/solve.py", "def solve():\n    return compute()\n", "def solve():\n    return 999\n"
    )
    report = scan_rollout(_rollout(src))
    detectors = {f.detector for f in report.findings}
    assert "constant-stub" in detectors and "hardcoded-expected" not in detectors
    assert not report.blocked  # a bare stub is a WARN — the human decides


def test_a_real_computation_returning_a_comparison_is_not_a_stub() -> None:
    src = FileChange(
        "src/e.py",
        "def is_even(n):\n    return check(n)\n",
        "def is_even(n):\n    return n % 2 == 0\n",
    )
    assert _detectors(_rollout(src)) == set()


def test_a_preexisting_constant_return_is_not_flagged_when_unchanged() -> None:
    body = "def version():\n    return 3\n\n\ndef other():\n    return work()\n"
    after = "def version():\n    return 3\n\n\ndef other():\n    return fixed()\n"
    assert "constant-stub" not in _detectors(_rollout(FileChange("src/v.py", body, after)))


def test_a_file_that_does_not_parse_is_skipped_not_crashed() -> None:
    src = FileChange("src/broken.py", "def f():\n    return g()\n", "def f(:\n    return 42\n")
    # No crash; the AST detectors simply cannot read it.
    assert "hardcoded-expected" not in _detectors(_rollout(src))


# --------------------------------------------------------------------------- #
# sandbox / .git
# --------------------------------------------------------------------------- #


def test_touching_git_blocks() -> None:
    assert "touched-sandbox" in _detectors(
        _rollout(FileChange(".git/hooks/pre-commit", None, "#!/bin/sh\nexit 0\n"))
    )


def test_touching_verify_sh_blocks() -> None:
    assert "touched-sandbox" in _detectors(_rollout(FileChange("verify.sh", "pytest\n", "true\n")))


def test_a_change_outside_the_workspace_blocks() -> None:
    assert "escaped-workspace" in _detectors(
        _rollout(FileChange("../secrets.env", None, "TOKEN=x\n"))
    )


# --------------------------------------------------------------------------- #
# shaping farm + giant output
# --------------------------------------------------------------------------- #


def test_padding_the_transcript_with_identical_calls_warns() -> None:
    steps = tuple(TranscriptStep(tool="grep", arguments="foo") for _ in range(10))
    report = scan_rollout(_rollout(steps=steps))
    assert "padding-calls" in {f.detector for f in report.findings}
    assert not report.blocked  # farming shaping is a WARN, surfaced for the human


def test_a_handful_of_repeated_calls_is_not_padding() -> None:
    steps = tuple(TranscriptStep(tool="grep", arguments="foo") for _ in range(3))
    assert "padding-calls" not in _detectors(_rollout(steps=steps))


def test_a_giant_tool_output_warns() -> None:
    big = TranscriptStep(tool="cat", arguments="huge.log", output="x" * 50_000)
    assert "giant-output" in _detectors(_rollout(steps=(big,)))


def test_giant_transcript_total_warns_even_without_one_giant_step() -> None:
    config = GuardConfig(giant_output_chars=100_000, giant_transcript_chars=1_000)
    steps = tuple(TranscriptStep(tool="ls", output="y" * 200) for _ in range(10))
    assert "giant-transcript" in _detectors(_rollout(steps=steps), config)


# --------------------------------------------------------------------------- #
# report + tree bridge
# --------------------------------------------------------------------------- #


def test_a_clean_rollout_has_no_findings_and_is_not_blocked() -> None:
    clean = _rollout(
        FileChange("src/app.py", "def f():\n    return g()\n", "def f():\n    return g() + 1\n"),
        steps=(TranscriptStep(tool="read", arguments="src/app.py"),),
    )
    report = scan_rollout(clean)
    assert report.findings == () and not report.blocked and report.gate_reason == ""


def test_gate_reason_names_the_blocking_detector() -> None:
    report = scan_rollout(_rollout(FileChange("tests/test_x.py", _TEST_BEFORE, None)))
    assert report.gate_reason == "guard:deleted-test"


def test_changes_from_trees_emits_only_changed_paths() -> None:
    before = {"a.py": "1", "b.py": "keep", "gone.py": "x"}
    after = {"a.py": "2", "b.py": "keep", "new.py": "y"}
    changes = changes_from_trees(before, after)
    paths = {c.path for c in changes}
    assert paths == {"a.py", "gone.py", "new.py"}  # b.py unchanged, excluded
    gone = next(c for c in changes if c.path == "gone.py")
    assert gone.deleted


# --------------------------------------------------------------------------- #
# the transcript sampler
# --------------------------------------------------------------------------- #


def _hacking_rollouts(n: int) -> list[GuardRollout]:
    return [
        GuardRollout(
            task_id=f"task-{i}",
            changes=(FileChange("tests/test_x.py", _TEST_BEFORE, None),),
            steps=(TranscriptStep(tool="edit", arguments="tests/test_x.py", output="deleted"),),
        )
        for i in range(n)
    ]


def test_sample_transcripts_is_deterministic_and_includes_findings() -> None:
    rollouts = _hacking_rollouts(50)
    a = sample_transcripts(rollouts, rng=random.Random(7), count=20)
    b = sample_transcripts(rollouts, rng=random.Random(7), count=20)
    assert a == b  # same seed -> same 20 rollouts, same order
    assert "20 of 50 rollouts" in a
    assert "deleted-test" in a  # the guard finding is rendered above the transcript


def test_sample_renders_all_when_fewer_than_count() -> None:
    text = sample_transcripts(_hacking_rollouts(3), rng=random.Random(1), count=20)
    assert "3 of 3 rollouts" in text


def test_render_truncates_a_giant_output_with_a_marker() -> None:
    rollout = GuardRollout(task_id="t", steps=(TranscriptStep(tool="cat", output="z" * 10_000),))
    rendered = render_transcript(rollout, max_output_chars=100)
    assert "chars cut]" in rendered
    assert len(rendered) < 1_000  # the 10k output did not make the dump unreadable


def test_transcript_sampler_writes_on_the_cadence_only(tmp_path: Path) -> None:
    sampler = TranscriptSampler(out_dir=tmp_path, every=100, count=5)
    rollouts = _hacking_rollouts(10)
    assert sampler.maybe_dump(50, rollouts, rng=random.Random(0)) is None  # off cadence
    path = sampler.maybe_dump(100, rollouts, rng=random.Random(0))
    assert path is not None and path.exists()
    assert "task-" in path.read_text(encoding="utf-8")
    assert sampler.maybe_dump(200, [], rng=random.Random(0)) is None  # nothing to dump


def test_sampler_is_a_noop_when_every_is_zero(tmp_path: Path) -> None:
    sampler = TranscriptSampler(out_dir=tmp_path, every=0)
    assert sampler.maybe_dump(0, _hacking_rollouts(3), rng=random.Random(0)) is None
