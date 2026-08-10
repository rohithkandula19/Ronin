"""Run `ronin.verify.demo`, the sixth demo module this suite has taken from 0%.

This one is unusual and worth running for a reason beyond coverage: it *checks
itself*. Each section counts its own failures and `main()` returns non-zero if any
claim did not hold, printing `UNEXPECTED:` at the point of divergence. So the
strongest assertion available is not on the text at all — it is that the demo's
own verdict is zero, with no `UNEXPECTED` anywhere in the output.

Section 4 is the one that matters most here. It prints whether the user's
`.git/index` bytes and `git status` are identical across a checkpoint — the same
promise `test_verify_isolation.py` pins down, shown rather than asserted. A demo
that printed `True` there while the code staged into the user's index would be the
most damaging output in this package, which is exactly why it gets executed by a
test instead of being trusted.
"""

from __future__ import annotations

import pytest

from ronin.verify import demo


async def test_the_demo_runs_offline_and_every_claim_holds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`main()` returning 0 *is* the assertion — it is the demo's own verdict."""
    assert await demo.main() == 0
    out = capsys.readouterr().out
    assert "UNEXPECTED" not in out, "a section did not do what this package claims"
    assert "every section did what it claims" in out


async def test_the_demo_reaches_all_five_sections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pinned to the headings the demo actually prints. A sibling demo test once
    guessed at a heading and failed, which is how a test ends up asserting the
    tester's idea of the code rather than the code."""
    await demo.main()
    out = capsys.readouterr().out
    for heading in (
        "1. detecting what 'verified' means here",
        "2. the narrowed plan for one changed file",
        "3. a repair loop that gives up honestly",
        "4. checkpoint, restore, session diff — without touching the user's index",
        "5. one self-critique, one extra iteration, then done",
        "result",
    ):
        assert heading in out, f"section missing: {heading}"


async def test_the_checkpoint_section_reports_the_users_repo_untouched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The claim in the section title, printed as a measurement.

    Both lines must read `True`. Asserting on the rendered booleans means a
    regression in checkpoint isolation fails *here* too, not only in the
    dedicated isolation tests — the demo is the version a person actually reads.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "the user's own repo, before and after:" in out
    assert ".git/index bytes identical: True" in out
    assert "git status --porcelain identical: True" in out


async def test_the_repair_section_shows_defeat_rather_than_a_fabricated_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repair loop that always claims victory is the failure mode worth
    demonstrating against, so the demo has to be seen giving up."""
    await demo.main()
    out = capsys.readouterr().out
    assert "3. a repair loop that gives up honestly" in out
    assert "ToolResult.ok = False" in out


async def test_the_critique_gate_is_capped_at_two_rounds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cap is the feature: one extra iteration is worth it, an unbounded
    self-critique loop is how a turn never ends."""
    await demo.main()
    out = capsys.readouterr().out
    assert "capped at two rounds, always" in out
