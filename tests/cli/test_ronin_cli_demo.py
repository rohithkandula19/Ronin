"""Run `ronin.cli.demo` — the whole-stack demo, previously never executed by any test.

`docs/STACK.md` measured eleven `demo.py` modules at 0%. Eight were covered before this
file; `ronin.mcp.demo`, `ronin.ui.demo` and `ronin.session_demo` are still not run by
anything and are named here so the remaining gap is written down rather than implied.

This one came first because it is where the gap costs the most, for a reason its own code
states out loud at the bottom of `main()`:

    print(f"{failures} claim(s) above did not hold. This demo is a test that prints.")

It is a test — eleven claim checks, each printing `FAILED:` and incrementing a counter —
and it was the single module the test suite did not run. Nothing in CI would have
noticed it going stale, and this is the demo that assembles *every* layer: the one a
person runs to decide whether the project does what the README says.

Two properties, and the second matters more than the first:

* **It runs offline and every claim holds.** Asserted by the absence of any `FAILED:`
  line, not by the exit code — a claim that failed while some later branch still
  returned 0 would pass an exit-code check and print a lie.
* **The counter is real.** A demo whose failure path is dead prints "every claim above
  held" whichever way the code behaves, which is worse than no demo: it manufactures
  exactly the confidence it was written to earn. So one claim is deliberately broken
  here, and the demo has to notice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ronin.cli import demo
from ronin.cli.spine import Loaded, Paths
from ronin.cli.wire import load_workspace

#: Every scene heading, in order. The demo is a narrative and a missing scene is a
#: subsystem that stopped being demonstrated.
SCENES: tuple[str, ...] = (
    "1. the workspace, loaded",
    "2. one mutating turn: gate, checkpoint, verify, repair",
    "3. compaction: fold the middle, keep the diffs",
    "4. what the session degraded on",
    "5. the same session, served over MCP (`ronin2 mcp-serve`)",
    "6. the transcript, written and replayed",
    "what this proves",
)


async def run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = await demo.main()
    return code, capsys.readouterr().out


def broken_claims(out: str) -> list[str]:
    """The demo's claim-failure lines, which are `FAILED:` and not merely `FAILED`.

    The distinction is not pedantry. Scene 2 prints "it FAILED, and the failure went
    back to the model as a ToolResult" — a *successful* claim about a verify pass that
    was supposed to fail. Grepping for the bare word is the obvious way to check this
    demo and it reports a healthy run as broken, which is how a check like this gets
    deleted for being noisy.
    """
    return [line for line in out.splitlines() if line.strip().startswith("FAILED:")]


# --------------------------------------------------------------------------- #
# it runs, and every claim it prints is one it checked
# --------------------------------------------------------------------------- #


async def test_the_demo_runs_offline_and_every_claim_holds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No provider, no network, no shell of its own: it builds a real project in a
    temporary directory and drives the assembled stack through it."""
    code, out = await run(capsys)

    assert broken_claims(out) == []
    assert code == 0
    assert out.rstrip().endswith("every claim above held.")


async def test_the_demo_reaches_every_scene(capsys: pytest.CaptureFixture[str]) -> None:
    """Six scenes and a verdict. A demo that exits 0 having skipped compaction is
    still a demo that stopped covering compaction."""
    _, out = await run(capsys)
    for scene in SCENES:
        assert scene in out, f"scene missing: {scene}"


async def test_the_demo_writes_nothing_into_the_working_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It builds its own workspace in a temp directory, and that is load-bearing: a
    demo that wrote `.ronin/sessions/` into the caller's cwd would litter whatever
    checkout someone tried it in, which is the first thing they would judge it by."""
    monkeypatch.chdir(tmp_path)
    await run(capsys)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# the claims are checks, not captions
# --------------------------------------------------------------------------- #


async def test_a_broken_claim_is_reported_and_changes_the_exit_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test that makes every other assertion here worth making.

    One claim is sabotaged — `pytest` is removed from what the workspace loader
    detected, so the "the verify command was discovered from pyproject.toml" claim is
    false. The demo must say `FAILED`, must *not* print its verdict, and must exit
    non-zero. If it did any of those wrong, the eleven checks would be decoration and
    the demo's own description of itself as "a test that prints" would be false.
    """

    def without_pytest(paths: Paths, **kwargs: Any) -> Loaded:
        loaded = load_workspace(paths, **kwargs)
        return replace(loaded, verify=replace(loaded.verify, test=None))

    monkeypatch.setattr(demo, "load_workspace", without_pytest)
    code, out = await run(capsys)

    assert code == 1
    assert broken_claims(out), "the sabotaged claim was not reported"
    assert "FAILED: pytest was not detected" in out
    assert "claim(s) above did not hold" in out
    assert "every claim above held." not in out, "it printed success while a claim failed"


# --------------------------------------------------------------------------- #
# the specific joins this demo exists to show
# --------------------------------------------------------------------------- #


async def test_the_write_is_refused_until_the_file_has_been_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`cli/__init__` says the joins are the point, and this is the join a unit test
    cannot reach: the file-state tracker lives in `context`, the refusal lives in
    `tools`, and only the gate in `cli` connects them."""
    _, out = await run(capsys)
    lowered = out.lower()
    assert "read" in lowered and ("refus" in lowered or "denied" in lowered or "blocked" in lowered)


async def test_the_checkpoint_is_taken_in_a_shadow_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The claim a user is most entitled to disbelieve — that Ronin commits without
    touching their git. The demo shows the `.git/index` was never opened, so the scene
    has to actually be there."""
    _, out = await run(capsys)
    assert ".git/index" in out


async def test_the_verify_failure_reaches_the_model_as_a_tool_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Not as a new instruction. The distinction is the whole design of the verify
    loop: an instruction would let a failing test rewrite the user's request."""
    _, out = await run(capsys)
    assert "ToolResult" in out or "tool result" in out.lower()


async def test_the_repair_loop_admits_defeat_rather_than_looping(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repair loop with no cap is an infinite loop with a budget attached. The demo
    shows it stopping and reporting what it tried."""
    _, out = await run(capsys)
    assert "could not fix" in out.lower()


async def test_compaction_fires_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    """Compaction that happens silently is indistinguishable from a model that forgot,
    which is the single most confusing failure a long session has."""
    _, out = await run(capsys)
    assert "3. compaction: fold the middle, keep the diffs" in out
    assert "kept in full" in out


async def test_the_replayed_transcript_is_sendable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The end of the chain: every event on disk, folded back, with no unpaired tool
    call. An unpairable replay is a provider 400 on the first turn after a resume."""
    _, out = await run(capsys)
    assert "pairing" in out.lower()
    assert "clean" in out.lower()
