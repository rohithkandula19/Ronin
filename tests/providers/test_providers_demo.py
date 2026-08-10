"""Run `ronin.providers.demo`, the third demo module this suite has taken from 0%.

`docs/STACK.md` measured eleven `demo.py` modules at exactly 0% and called the naive
coverage gate "a tripwire that fires on the next demo module somebody adds". Running them
inverts that: `ronin.core.demo` and `ronin.tools.demo` are now covered by tests that
execute them, and this is the third.

This demo has a property the other two do not, and it is the reason it is worth running:
**every byte it prints is replayed from `tests/providers/fixtures`** — the same golden
fixtures `test_golden_streams.py` asserts on. So the demo cannot drift from what the suite
verifies, and a fixture rename breaks both together rather than leaving the demo quietly
printing a stale shape.
"""

from __future__ import annotations

import pytest

from ronin.providers import demo


async def test_the_demo_runs_offline_with_no_network_and_no_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await demo.main() == 0
    out = capsys.readouterr().out
    assert "No network, no keys, no spend" in out
    assert "done" in out


async def test_the_demo_replays_the_same_fixtures_the_suite_asserts_on(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The claim in its closing line, checked rather than trusted.

    If this ever fails, the demo has started generating its own output — at which point
    it can disagree with `test_golden_streams.py` and neither will say so.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "tests/providers/fixtures" in out
    assert demo.FIXTURES.is_dir(), "the fixture directory the demo replays must exist"


async def test_the_demo_covers_a_provider_shape_the_shim_the_router_and_accounting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Four scenes, four subsystems. A demo of the provider layer that skipped the
    router or the ledger would be showing the easy half.

    Asserted against the headings the demo actually prints — an earlier draft of this
    test guessed at the word "router" and failed, because scene 3 is phrased as
    "Roles map to models in config". Guessing at output is how a test ends up
    asserting the tester's idea of the code.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "Three providers, three wire formats, one ToolUse" in out
    assert "The format shim" in out
    assert "Roles map to models in config" in out
    assert "Cache-aware assembly, then what it cost" in out


async def test_argv_is_accepted_and_ignored(capsys: pytest.CaptureFixture[str]) -> None:
    """`main` takes argv so it can be wired to a console script later; today it takes
    no options, and passing some must not change the run."""
    assert await demo.main(["--whatever"]) == 0
    assert "done" in capsys.readouterr().out
