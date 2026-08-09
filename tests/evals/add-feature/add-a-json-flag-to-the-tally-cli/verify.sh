#!/usr/bin/env bash
# Verifier for add-feature/add-a-json-flag-to-the-tally-cli.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/add-a-json-flag-to-the-tally-cli): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/add-a-json-flag-to-the-tally-cli): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "add-a-json-flag-to-the-tally-cli"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

import json
import subprocess

EXPECTED_ROWS = [("alice", 7.5), ("bob", 2.5), ("carol", 1.25)]


def run(args):
    return subprocess.run(
        [sys.executable, "tally.py", *args],
        capture_output=True,
        text=True,
    )


plain = run(["data/october.json"])
if plain.returncode != 0:
    fail(f"`tally.py data/october.json` exited {plain.returncode}: {plain.stderr.strip()[:200]}")

expected_lines = [f"{'NAME':<12}{'HOURS':>8}"]
for name, hours in EXPECTED_ROWS:
    expected_lines.append(f"{name:<12}{hours:>8.2f}")
expected_lines.append(f"{'TOTAL':<12}{sum(h for _, h in EXPECTED_ROWS):>8.2f}")
got_lines = [line.rstrip() for line in plain.stdout.strip().splitlines()]
if got_lines != [line.rstrip() for line in expected_lines]:
    fail(
        "the default (no --json) output changed, and it was supposed to stay identical: "
        f"expected {[l.rstrip() for l in expected_lines]!r}, got {got_lines!r}"
    )

for args in (["data/october.json", "--json"], ["--json", "data/october.json"]):
    result = run(args)
    if result.returncode != 0:
        fail(f"`tally.py {' '.join(args)}` exited {result.returncode}: {result.stderr.strip()[:200]}")
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        fail(
            f"stdout of `tally.py {' '.join(args)}` is not parseable JSON ({exc}); "
            f"got {result.stdout.strip()[:200]!r}"
        )
    if not isinstance(payload, list):
        fail(f"--json should print a JSON array, got a {type(payload).__name__}")
    if len(payload) != len(EXPECTED_ROWS):
        fail(f"--json printed {len(payload)} record(s), expected {len(EXPECTED_ROWS)}")
    for record, (name, hours) in zip(payload, EXPECTED_ROWS):
        if not isinstance(record, dict):
            fail(f"--json records should be objects, got a {type(record).__name__}")
        if set(record) != {"name", "total"}:
            fail(f"--json record keys should be exactly name/total, got {sorted(record)}")
        if record["name"] != name:
            fail(f"--json record order/name wrong: expected {name!r}, got {record['name']!r}")
        if isinstance(record["total"], str) or not close(record["total"], hours):
            fail(f"--json total for {name!r} should be the number {hours}, got {record['total']!r}")

RONIN_VERIFY_EOF
