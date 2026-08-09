#!/usr/bin/env bash
# Verifies the shard plan prints, that the chunk count is right for both exact
# and ragged splits, and that the IndexError was not merely caught or skipped.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' . 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the chunk count to be corrected, but found exception suppression: $HIT"
  exit 1
fi
# 'break'/'continue' guarding the out-of-range read is the same symptom fix.
HIT=$(grep -rnE '^[[:space:]]*(break|continue)[[:space:]]*$' --include='*.py' shard 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: the loop bound should be correct rather than bailing out mid-loop: $HIT"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "8 row(s) in 2 chunk(s) of 4\n"
    "chunk 0 [from A-1001] 4 row(s)\n"
    "chunk 1 [from A-1005] 4 row(s)\n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run([sys.executable, "shard_orders.py"], capture_output=True, text=True)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 shard_orders.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(f"shard plan is wrong; expected {EXPECTED!r} got {proc.stdout!r}")

sys.path.insert(0, ".")
from shard.batching import iter_chunks  # noqa: E402
from shard.sizing import chunk_count  # noqa: E402

for total, size, expected in ((8, 4, 2), (7, 4, 2), (9, 4, 3), (1, 4, 1), (0, 4, 0), (12, 3, 4)):
    got = chunk_count(total, size)
    if got != expected:
        die(f"chunk_count({total}, {size}) should be {expected}, got {got}")

# Every row must still land in exactly one chunk, ragged tail included.
rows = [{"order_id": f"A-{1000 + n}"} for n in range(1, 8)]
chunks = iter_chunks(rows, 4)
flattened = [row for chunk in chunks for row in chunk.rows]
if flattened != rows:
    die(f"chunking 7 rows dropped or duplicated rows; got {[c.rows for c in chunks]!r}")
if [len(chunk.rows) for chunk in chunks] != [4, 3]:
    die(f"7 rows at size 4 should split 4+3, got {[len(c.rows) for c in chunks]!r}")
if [chunk.label for chunk in chunks] != ["from A-1001", "from A-1005"]:
    die(f"chunk labels should name each chunk's first order, got {[c.label for c in chunks]!r}")
PY
