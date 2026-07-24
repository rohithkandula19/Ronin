#!/usr/bin/env python3
"""Single-source the README's headline numbers from the repo itself.

The README used to hand-maintain "3,355 tests", "seven … packages" and a game
count that disagreed with itself (31 vs 26). Hand-maintained numbers drift, and
for a project whose brand is honesty-about-evidence a stale number is a
credibility bomb. This script computes the real numbers and either rewrites the
README (default) or verifies it is current (``--check``, wired into CI so a
stale number fails the build).

Numbers owned here:
  * test count      — ``pytest --collect-only`` across packages + apps
  * total packages  — directories under ``packages/``
  * arcade games    — entries in ``ronin_cli.games.GAMES``

Prose (which packages, what they do) stays human-authored; only the digits are
generated, via idempotent, anchored substitutions.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def test_count() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "packages", "apps", "training"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout
    m = re.search(r"(\d+)\s+tests? collected", out)
    if not m:
        raise SystemExit("could not parse pytest collection output")
    return int(m.group(1))


def package_count() -> int:
    return sum(1 for p in (ROOT / "packages").iterdir() if p.is_dir())


def game_count() -> int:
    sys.path.insert(0, str(ROOT / "packages" / "cli" / "src"))
    from ronin_cli.games import GAMES  # noqa: E402
    return len(GAMES)


def _grouped(n: int) -> str:
    return f"{n:,}"


def apply(text: str, tests: int, pkgs: int, games: int) -> str:
    # test badge: tests-<n>%20passing
    text = re.sub(r"tests-\d[\d,]*%20passing", f"tests-{tests}%20passing", text)
    # "3,355 tests" / "3355 tests" -> "<n> tests" (any grouped/plain digits)
    text = re.sub(r"\b\d[\d,]*\s+tests\b", f"{_grouped(tests)} tests", text)
    # "N games" (arcade) -> "<games> games"
    text = re.sub(r"\b\d+\s+games\b", f"{games} games", text)
    # "N-game arcade" -> "<games>-game arcade"
    text = re.sub(r"\b\d+-game arcade\b", f"{games}-game arcade", text)
    # "N-package workspace" -> "<pkgs>-package workspace"
    text = re.sub(r"\b\d+-package workspace\b", f"{pkgs}-package workspace", text)
    # "the other N are platform packages" -> total minus the 7 core
    text = re.sub(r"the other \d+ are platform packages",
                  f"the other {pkgs - 7} are platform packages", text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the README is not already current")
    args = ap.parse_args()

    tests, pkgs, games = test_count(), package_count(), game_count()
    current = README.read_text()
    updated = apply(current, tests, pkgs, games)

    if args.check:
        if current != updated:
            sys.stderr.write(
                f"README stats are stale. Expected tests={tests}, "
                f"packages={pkgs}, games={games}. Run: "
                f"python scripts/generate_readme_stats.py\n"
            )
            return 1
        print(f"README stats current: tests={tests}, packages={pkgs}, games={games}")
        return 0

    README.write_text(updated)
    print(f"README updated: tests={tests}, packages={pkgs}, games={games}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
