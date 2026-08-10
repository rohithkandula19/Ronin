"""Test-wide invariants that are easier to enforce than to remember.

Right now there is exactly one, and it exists because it was violated: a change that
added a first-run telemetry notice to the CLI entry point made the suite write
``~/.ronin/telemetry.json`` into the **real** home directory of whoever ran ``pytest``.

Two things were wrong with that, and only the second is obvious:

1. A test suite that writes outside its tmp_path is rude. It leaves state on a
   developer's machine and on a CI runner's cache.
2. Worse — the notice is *once-only*, so whichever test ran first consumed it and a
   later assertion about it became order-dependent. The failure showed up as a test that
   passed alone and failed in the suite, which is the most expensive kind to diagnose.

Modules under ``src/ronin`` take ``home`` as a parameter precisely so this cannot happen
(``Paths.discover(cwd, home=...)``, ``telemetry.for_home(home)``). But
:func:`ronin.cli.main.main` is the process entry point: resolving a real home is its
job, and a test that calls it gets the real one. So the environment is redirected here,
which fixes it for every present and future caller rather than for the one that was
caught.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

#: The real home directory, captured at import time — **before** any fixture redirects
#: the environment. Resolving it later is useless: ``Path.home()`` and
#: ``expanduser("~")`` both follow ``HOME``, so after the redirect they name the tmp
#: directory writes are supposed to go to. The first version of the guard below made
#: exactly that mistake and failed on correct behaviour.
REAL_HOME = Path(os.environ.get("HOME") or os.path.expanduser("~")).resolve()


@pytest.fixture(autouse=True)
def _hermetic_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point ``Path.home()`` at a per-test directory, for every test in ``tests/``.

    ``HOME`` covers POSIX and ``USERPROFILE`` covers Windows, because
    ``Path.home()`` goes through ``os.path.expanduser`` and consults a different
    variable on each. ``monkeypatch`` restores both afterwards.

    Autouse rather than opt-in: an opt-in guard protects the tests whose author
    remembered the problem, which is not the set that needs protecting.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Some code reads XDG paths rather than ~ directly; keep those inside too.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert Path.home() == home, "Path.home() did not follow the redirect"
    yield home


@pytest.fixture(autouse=True)
def _no_real_home_writes(_hermetic_home: Path) -> Iterator[None]:
    """Fail a test that somehow still reached the real ``~/.ronin``.

    The redirect above should make this unreachable. It is here anyway because the
    original bug was *silent* — nothing failed, a file simply appeared — and a guard
    that only works when someone thinks to look is not a guard. If this ever fires, the
    code under test resolved a home some way that ignores the environment.
    """
    marker = REAL_HOME / ".ronin" / "telemetry.json"
    before = marker.exists()
    yield
    if marker.exists() and not before:
        marker.unlink(missing_ok=True)
        pytest.fail(
            f"a test wrote {marker} in the real home directory. Pass `home=` explicitly "
            "(Paths.discover(cwd, home=...), telemetry.for_home(home)) instead of "
            "letting the code resolve Path.home()."
        )
