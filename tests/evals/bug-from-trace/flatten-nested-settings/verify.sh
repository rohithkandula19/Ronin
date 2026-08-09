#!/usr/bin/env bash
# Verifies the settings audit terminates with the right leaves, and that the
# runaway recursion was fixed rather than caught or given a bigger stack.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' flatkit audit_settings.py 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the cyclic walk to terminate, but found exception suppression: $HIT"
  exit 1
fi
HIT=$(grep -rn 'setrecursionlimit' --include='*.py' flatkit audit_settings.py 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: raising the recursion limit does not stop an unbounded walk: $HIT"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "7 leaf value(s)\n"
    "  'checkout'\n"
    "  3\n"
    "  'https://api.example.invalid/v2'\n"
    "  'https://api-eu.example.invalid/v2'\n"
    "  'fast-path'\n"
    "  'audit-log'\n"
    "  False\n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run(
    [sys.executable, "audit_settings.py"], capture_output=True, text=True, timeout=60
)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 audit_settings.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(f"leaf audit is wrong; expected {EXPECTED!r} got {proc.stdout!r}")

sys.path.insert(0, ".")
from flatkit.walk import flatten_values  # noqa: E402

cases = [
    ("hello", ["hello"]),
    ("", [""]),
    (7, [7]),
    (None, [None]),
    ({"a": "xy", "b": [1, {"c": "z"}]}, ["xy", 1, "z"]),
    ([["a"], ["b", "c"]], ["a", "b", "c"]),
    ({}, []),
]
for value, expected in cases:
    got = flatten_values(value)
    if got != expected:
        die(f"flatten_values({value!r}) should be {expected!r}, got {got!r}")

# A string leaf must stay one leaf, not be exploded into characters.
if flatten_values({"name": "ab"}) != ["ab"]:
    die(f"a text value must stay a single leaf, got {flatten_values({'name': 'ab'})!r}")
PY
