#!/usr/bin/env bash
# Verifier for add-feature/add-a-median-to-the-latency-summary.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/add-a-median-to-the-latency-summary): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/add-a-median-to-the-latency-summary): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "add-a-median-to-the-latency-summary"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

try:
    from metrics.summary import summarize
except Exception as exc:
    fail(f"could not import metrics.summary: {type(exc).__name__}: {exc}")

# 1. the empty case named in the request, checked on its own
try:
    empty = summarize([])
except Exception as exc:
    fail(
        "the empty-input case stated in the request is not handled: summarize([]) raised "
        f"{type(exc).__name__}: {exc}"
    )
if not isinstance(empty, dict):
    fail(f"summarize([]) should return a dict, got a {type(empty).__name__}")
if empty.get("count") != 0:
    fail(f"summarize([]) should report count 0, got {empty.get('count')!r}")
if "median" not in empty:
    fail("summarize([]) has no 'median' key: the empty case must carry it too")
for key in ("min", "max", "mean", "median"):
    if empty.get(key, "MISSING") is not None:
        fail(f"summarize([])[{key!r}] should be None for the empty case, got {empty.get(key, 'MISSING')!r}")

# 2. odd count, unsorted input
odd = summarize([50, 10, 90, 30, 70])
if "median" not in odd:
    fail(f"summarize() does not report a 'median' key: got {sorted(odd)}")
if not close(odd["median"], 50):
    fail(f"median of [50, 10, 90, 30, 70] should be 50, got {odd['median']!r}")

# 3. even count: the average of the two middle values
even = summarize([1, 2, 3, 4])
if not close(even["median"], 2.5):
    fail(f"median of [1, 2, 3, 4] should be 2.5 (mean of the two middle values), got {even['median']!r}")
even_unsorted = summarize([4, 1, 3, 2])
if not close(even_unsorted["median"], 2.5):
    fail(f"median of the same four samples unsorted should still be 2.5, got {even_unsorted['median']!r}")

# 4. one sample
single = summarize([7])
if not close(single["median"], 7):
    fail(f"median of a single sample [7] should be 7, got {single['median']!r}")
if single["count"] != 1:
    fail(f"summarize([7]) should report count 1, got {single['count']!r}")

# 5. the statistics that were already there
normal = summarize([10, 20, 30])
for key, want in (("count", 3), ("min", 10), ("max", 30), ("mean", 20)):
    if not close(normal[key], want):
        fail(f"summarize([10, 20, 30])[{key!r}] should still be {want}, got {normal[key]!r}")

RONIN_VERIFY_EOF
