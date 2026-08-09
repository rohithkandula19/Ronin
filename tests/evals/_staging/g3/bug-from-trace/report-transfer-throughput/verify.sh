#!/usr/bin/env bash
# Verifies the throughput report is produced with the aborted run excluded at
# the input boundary, and that the division was not guarded or the error caught.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' . 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the bad row to be rejected where it is read, but found exception suppression: $HIT"
  exit 1
fi

# The aborted run is the input under test; deleting or editing it in runs.csv
# hides the bug instead of fixing it.
if ! grep -q '^index-rebuild,0,$' runs.csv; then
  echo "verify: runs.csv no longer contains the aborted 'index-rebuild,0,' row - the input data was edited instead of the loader"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "export-nightly     11.76 MB/s\n"
    "thumbnailer         3.08 MB/s\n"
    "mailer-digest       2.50 MB/s\n"
    "overall            11.00 MB/s\n"
    "skipped: index-rebuild\n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run([sys.executable, "throughput.py"], capture_output=True, text=True)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 throughput.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(f"throughput report is wrong; expected {EXPECTED!r} got {proc.stdout!r}")

sys.path.insert(0, ".")
from pathlib import Path  # noqa: E402

from thru.metrics import rate_mb_s  # noqa: E402
from thru.records import Sample, load_samples  # noqa: E402

result = load_samples(Path("runs.csv"))
if [sample.job for sample in result.samples] != [
    "export-nightly",
    "thumbnailer",
    "mailer-digest",
]:
    die(f"loader should hand on only the completed runs, got {[s.job for s in result.samples]!r}")
if result.skipped != ["index-rebuild"]:
    die(f"loader should report ['index-rebuild'] as skipped, got {result.skipped!r}")
if any(sample.seconds <= 0 for sample in result.samples):
    die(f"a sample with a non-positive duration reached the metrics: {result.samples!r}")

# The invariant belongs upstream: a zero-duration sample must still be refused
# by rate_mb_s (raising is fine), never turned into a number.
try:
    value = rate_mb_s(Sample(job="never-ran", bytes_out=1024, seconds=0.0))
except Exception:  # noqa: BLE001 - any refusal is acceptable here
    pass
else:
    die(
        "rate_mb_s now invents a throughput for a run with no duration instead of the "
        f"loader rejecting the row; it returned {value!r}"
    )
PY
