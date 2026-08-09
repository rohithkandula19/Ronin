#!/usr/bin/env bash
# Verifies the dispatch plan renders, that an unregistered sink now raises
# instead of returning None, and that nothing was papered over.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' . 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the failed lookup to be fixed, but found exception suppression: $HIT"
  exit 1
fi

# The mixed-case sink name in the job file is the input the tool has to cope
# with; editing the data instead of the lookup is not a fix.
if ! grep -q '"Postgres"' jobs.json; then
  echo "verify: jobs.json no longer names the sink 'Postgres' - the job data was edited instead of the lookup"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "nightly-events: s3 -> s3://spool-archive/events/ (batches of 2000) => 3 write(s)\n"
    "warehouse-sync: postgres -> warehouse.public.events (batches of 500) => 3 write(s)\n"
    "ops-digest: smtp -> digest@example.invalid (batches of 50) => 2 write(s)\n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run([sys.executable, "spool_plan.py"], capture_output=True, text=True)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 spool_plan.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(f"dispatch plan is wrong; expected {EXPECTED!r} got {proc.stdout!r}")

sys.path.insert(0, ".")
from spool.handlers import POSTGRES  # noqa: E402
from spool.registry import UnknownHandler, find_handler  # noqa: E402

for spelling in ("postgres", "Postgres", "POSTGRES"):
    got = find_handler(spelling)
    if got is not POSTGRES:
        die(f"find_handler({spelling!r}) should resolve to the postgres handler, got {got!r}")

try:
    result = find_handler("kafka")
except UnknownHandler as exc:
    if "kafka" not in str(exc):
        die(f"UnknownHandler should name the sink it could not find, got {str(exc)!r}")
except Exception as exc:  # noqa: BLE001 - reporting the wrong exception type
    die(
        "find_handler('kafka') should raise UnknownHandler, got "
        f"{type(exc).__name__}: {exc}"
    )
else:
    die(
        "find_handler('kafka') still returns instead of raising UnknownHandler; got "
        f"{result!r}"
    )
PY
