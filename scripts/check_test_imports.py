#!/usr/bin/env python3
"""Fail if a test file imports a network library. Runs as a pre-commit hook.

The standing rule is that tests are offline and every provider is mocked. A rule nobody
enforces is a rule that decays one convenient import at a time, so this is a gate rather
than a convention: the first test that reaches for ``httpx`` fails the commit.

Why an AST walk and not a grep
------------------------------
A grep for ``import httpx`` over this repository's own test suite reports a hit in
``tests/telemetry/test_ronin_telemetry_transport.py`` — a file that imports no such
thing. The string appears in its docstring, describing the very hazard it asserts
against. A grep-based version of this hook would have failed the build on a correct
file, and the obvious fix for whoever hit it would be to delete the hook.

So the check parses each file and inspects real ``Import``/``ImportFrom`` nodes. It
walks the whole tree rather than the module's top level, because ``import httpx``
nested inside a helper function is exactly the case that matters and is invisible to
anything that only reads module-scope statements.

Precision about ``urllib``
--------------------------
``urllib.request`` opens sockets; ``urllib.parse`` is string manipulation that half the
test suite legitimately uses. The denylist is therefore matched on dotted prefixes, not
on the top-level package name, so banning the former does not ban the latter.

No allowlist, deliberately
--------------------------
Zero test files violate this today, which is what makes a hard ban affordable. An
escape hatch added before anyone needs one is an escape hatch that becomes the norm; if
a genuine case ever appears, the fix is to argue for it in review and add it here
explicitly, where it is visible.

Usage::

    python scripts/check_test_imports.py                    # walk every test dir
    python scripts/check_test_imports.py path/to/test.py …  # what pre-commit passes
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Dotted module prefixes that open a socket. Matched as prefixes so ``http.client``
#: catches ``http.client.HTTPConnection`` while leaving ``http.cookies`` alone, and
#: ``urllib.request`` does not drag in ``urllib.parse``.
FORBIDDEN: tuple[str, ...] = (
    "aiohttp",
    "boto3",
    "botocore",
    "ftplib",
    "grpc",
    "http.client",
    "httpcore",
    "httpx",
    "imaplib",
    "paramiko",
    "poplib",
    "pycurl",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "telnetlib",
    "urllib.request",
    "urllib3",
    "websocket",
    "websockets",
)

#: Where tests live when the script is invoked with no arguments. The standalone walk is
#: the belt to pre-commit's braces — pre-commit only sees *staged* files, so a violation
#: already on disk would never be shown one.
TEST_ROOT = ROOT / "tests"

#: ``tests/evals/`` holds eval task *fixtures*: small repos the agent is pointed at, not
#: tests of ours. Several legitimately contain their own ``test_*.py``, and the "add a
#: test" category deliberately ships files that do not even parse. Policing another
#: project's code for our offline rule is meaningless, and the first run of this hook
#: duly reported two SyntaxErrors from a CSV-quoting fixture. ``pyproject.toml``'s
#: ``norecursedirs`` and ``tests/evals/conftest.py`` exclude this tree for the same
#: reason; this is the third mechanism because all three fail differently.
EXCLUDED = (ROOT / "tests" / "evals",)


def is_excluded(path: Path) -> bool:
    """True if ``path`` lies under a directory this hook has no business reading."""
    resolved = path if path.is_absolute() else (ROOT / path)
    resolved = resolved.resolve()
    return any(root in resolved.parents for root in EXCLUDED)


def offending_module(dotted: str) -> str | None:
    """Return the denylist entry ``dotted`` violates, or ``None`` if it is allowed."""
    for entry in FORBIDDEN:
        if dotted == entry or dotted.startswith(f"{entry}."):
            return entry
    return None


def imported_names(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield ``(dotted_module, lineno)`` for every import anywhere in ``tree``.

    ``ast.walk`` rather than iterating ``tree.body``: a lazy import inside a function
    is the case this hook exists to catch.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        # `from . import x` has no module; a relative import cannot be httpx.
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            yield node.module, node.lineno


def check_file(path: Path) -> list[str]:
    """Return one message per forbidden import in ``path``; empty means clean."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        # A file that will not parse is a problem, but not *this* hook's problem —
        # ruff and the test run both report it with a better message. Staying silent
        # here keeps one failure from being reported by three tools at once.
        print(f"  {path}: skipped, could not parse ({exc.__class__.__name__})")
        return []

    messages = []
    for dotted, lineno in sorted(imported_names(tree), key=lambda pair: pair[1]):
        entry = offending_module(dotted)
        if entry is not None:
            messages.append(
                f"  {_display(path)}:{lineno}: imports {dotted!r} (network library: {entry})"
            )
    return messages


def _display(path: Path) -> Path:
    """``path`` relative to the repo when it is inside it, else unchanged.

    ``relative_to`` *raises* for a path outside ``ROOT``, which made the hook crash on
    any absolute path from elsewhere — a temp directory, or a caller invoking it from
    another checkout. pre-commit passes repo-relative paths and never hit it; the tests
    did immediately. Shortening a path for display must not be able to fail.
    """
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def test_files(paths: Sequence[str]) -> Iterable[Path]:
    """The files to check: what was passed, or every test module under ``tests/``.

    ``EXCLUDED`` is applied in both modes on purpose. pre-commit passes whatever is
    staged, so editing a fixture would otherwise route another project's code through
    this hook — the same mistake the standalone walk made on its first run.
    """
    if paths:
        return [Path(p) for p in paths if p.endswith(".py") and not is_excluded(Path(p))]
    return sorted(
        p for p in TEST_ROOT.rglob("*.py") if "__pycache__" not in p.parts and not is_excluded(p)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="files to check (default: every test module)")
    args = parser.parse_args(argv)

    failures = [message for path in test_files(args.paths) for message in check_file(path)]
    if failures:
        print("Tests must not import a network library — they run offline with mocked providers:")
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
