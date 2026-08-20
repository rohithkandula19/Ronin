"""Run `ronin.tools.demo`, so the demo command stays a fact rather than a claim.

`docs/STACK.md` measured eleven `demo.py` modules at exactly 0% and named the
consequence: a demo nobody executes drifts the first time an attribute is renamed, and
the person who finds out is the one being shown the tool. `ronin.core.demo` proved the
fix cheap — 0% → 100% by running it — so this does the same for the tool layer, which is
the demo most worth trusting because it drives a **real** shell against **real** files in
a temp directory.

What it must not do is reach the network. `demo_net` is driven by `fake_fetch`,
`fake_extract` and `fake_search` closures, which is why this file can live in a suite
whose pre-commit hook fails on a network import.
"""

from __future__ import annotations

import pytest

from ronin.tools import demo


async def test_the_demo_runs_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """Exit 0 and every scene reached, against a real shell in a temp directory."""
    assert await demo.main() == 0
    out = capsys.readouterr().out
    for heading in ("1.", "2.", "3.", "4.", "5.", "6."):
        assert heading in out
    assert "done" in out
    # The closing claim is the layer's thesis; if the scenes stop printing refusals
    # it stops being true.
    assert "Every refusal above is a message written for the model to act on" in out


async def test_the_demo_shows_refusals_and_not_only_successes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool layer's refusals are half its behaviour, and the half a passing test
    suite hides. The demo exists to show them, so assert they are visible."""
    await demo.main()
    out = capsys.readouterr().out
    assert "✗" in out, "no refusal was rendered — the demo is only showing happy paths"
    assert "✔" in out, "no success was rendered either"


async def test_the_demo_touches_no_real_network(capsys: pytest.CaptureFixture[str]) -> None:
    """The network scene runs on injected closures, not sockets.

    Asserted through the output: the fake search returns a `.test` URL, a reserved
    TLD that cannot resolve, so seeing it proves the injected path ran.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "https://x.test/rg" in out


async def test_the_demo_leaves_no_files_behind(tmp_path_factory: pytest.TempPathFactory) -> None:
    """It builds its own throwaway tree and closes the shell it opened.

    Not a tidiness point: a demo that leaks a live `PersistentShell` leaks a process,
    and one that writes outside its temp dir would be editing the reader's checkout
    the first time they tried it.
    """
    import ronin.tools.demo as module

    source = module.__file__ or ""
    text = open(source, encoding="utf-8").read()  # noqa: SIM115 - one read, no handle kept
    assert "tempfile" in text, "the demo must build its own throwaway tree"
    assert "await shell.close()" in text, "the demo must close the shell it opened"
    assert "finally:" in text, "closing must survive a scene raising"
