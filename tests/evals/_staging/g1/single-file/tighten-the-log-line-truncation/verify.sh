#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import doctest
import sys

sys.path.insert(0, "src")


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


try:
    from logging_util import shorten
except Exception as exc:  # noqa: BLE001
    fail(f"could not import logging_util.shorten: {exc!r}")

truncate = shorten.truncate

# 1. Nothing comes back over budget any more.
long_text = "ronin evaluates tool calls"
for limit in range(1, len(long_text) + 4):
    got = truncate(long_text, limit)
    if len(got) > limit:
        fail(
            f"truncate({long_text!r}, {limit}) returned {got!r}, which is "
            f"{len(got)} characters - over the {limit}-character budget"
        )
got = truncate(long_text, 12)
if got != "ronin evalu…":
    fail(f"expected truncate({long_text!r}, 12) to be 'ronin evalu…', got {got!r}")

# 2. Text that already fits is returned untouched, and the marker is still there.
for text, limit in (("short", 12), ("exactlyten", 10), ("", 3)):
    if truncate(text, limit) != text:
        fail(f"expected truncate({text!r}, {limit}) to return it unchanged, got {truncate(text, limit)!r}")
if not truncate(long_text, 20).endswith("…"):
    fail(f"truncated text lost its ellipsis marker: {truncate(long_text, 20)!r}")
try:
    truncate("anything", 0)
except ValueError:
    pass
else:
    fail("expected truncate('anything', 0) to raise ValueError")

# 3. The field renderer follows.
got = shorten.summarise({"task": "rewrite the pagination helper", "id": "a1"}, 16)
if got != "task=rewrite the pag…, id=a1":
    fail(f"expected summarise(...) to be 'task=rewrite the pag…, id=a1', got {got!r}")

# 4. `make test` runs the doctests in this module: they must still pass, and must
#    still be there - the module's examples are its documentation.
finder = doctest.DocTestFinder()
tests = finder.find(shorten)
examples = sum(len(test.examples) for test in tests)
if examples < 5:
    fail(f"the module's doctest examples were deleted rather than updated: {examples} left, expected 5")
runner = doctest.DocTestRunner(verbose=False)
for test in tests:
    runner.run(test, out=lambda _line: None)
if runner.failures:
    fail(
        f"{runner.failures} of {runner.tries} doctests in src/logging_util/shorten.py now fail - "
        "the examples still show the old output"
    )
PYCODE
