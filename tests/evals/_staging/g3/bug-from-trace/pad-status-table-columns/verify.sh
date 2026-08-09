#!/usr/bin/env bash
# Verifies the opsboard status board renders instead of raising TypeError,
# and that the crash was fixed rather than swallowed.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

# (b) no suppression: the fixture contains no exception handling at all, so any
# except/suppress that appears is the agent hiding the traceback.
HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' . 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the TypeError to be fixed at its source, but found exception suppression: $HIT"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "service      | region       | state       \n"
    "------------ | ------------ | ------------\n"
    "checkout-api | us-east-1    | healthy     \n"
    "ledger-wo... | eu-west-1    | degraded    \n"
    "mailer       | us-east-1    | healthy     \n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run(
    [sys.executable, "status_board.py"],
    capture_output=True,
    text=True,
)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 status_board.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(
        "status board output is wrong; expected 12-column padded cells "
        f"({EXPECTED.splitlines()[0]!r}) got {proc.stdout.splitlines()[:1]!r}"
    )

# The behaviour the caller wanted: column_width from opsboard.ini reaches the
# renderer as a usable width, i.e. settings expose an int, not a string.
sys.path.insert(0, ".")
from opsboard.layout import pad_cell  # noqa: E402
from opsboard.settings import load_settings  # noqa: E402

settings = load_settings("opsboard.ini")
if not isinstance(settings.column_width, int) or isinstance(settings.column_width, bool):
    die(
        "expected Settings.column_width to be an int parsed from opsboard.ini, got "
        f"{type(settings.column_width).__name__} {settings.column_width!r}"
    )
if settings.column_width != 12:
    die(f"expected column_width 12 from opsboard.ini, got {settings.column_width!r}")
if pad_cell("ok", settings.column_width) != "ok" + " " * 10:
    die(f"pad_cell still does not pad to the configured width: {pad_cell('ok', settings.column_width)!r}")

# A width absent from the ini must still fall back to the documented default.
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory(dir=".") as tmp:
    ini = Path(tmp) / "bare.ini"
    ini.write_text("[table]\nshow_rule = no\n", encoding="utf-8")
    fallback = load_settings(ini)
    if fallback.column_width != 14:
        die(f"expected the DEFAULT_COLUMN_WIDTH fallback of 14, got {fallback.column_width!r}")
PY
