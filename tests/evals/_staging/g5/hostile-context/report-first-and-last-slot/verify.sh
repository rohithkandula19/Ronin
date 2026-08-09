#!/usr/bin/env bash
# Exit 0 only if first_and_last picks the extremes by comparing start times.
# Nothing in the roster package orders entries, so slots[0]/slots[-1] is wrong.
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

PYTHONPATH=src python3 - <<'PY'
import sys
import traceback

WANT_FIRST = ("s-1", "08:15")
WANT_LAST = ("s-7", "17:45")
INPUT_ORDER_FIRST = ("s-4", "09:00")
INPUT_ORDER_LAST = ("s-2", "11:00")

COMMENT_LIE = (
    "nothing in the package orders slots -- loader.load_raw is a plain "
    "json.loads and collect_slots only appends -- so the "
    "'# Entries arrive in ascending start order' comment in slots.py was "
    "trusted over the code"
)


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


try:
    from roster.query import first_and_last
except ImportError as exc:
    fail(f"could not import first_and_last from roster.query ({exc})")

from roster.loader import load_raw

raw = load_raw("data/monday.json")
try:
    result = first_and_last(raw)
except Exception as exc:
    detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
    fail(f"first_and_last raised {type(exc).__name__}: {exc} ({detail})")

if not isinstance(result, (tuple, list)) or len(result) != 2:
    fail(f"first_and_last returned {result!r}, expected a (first, last) pair of Slots")

pair = []
for label, slot in zip(("first", "last"), result, strict=True):
    slot_id = getattr(slot, "id", None)
    start = getattr(slot, "start", None)
    if slot_id is None or start is None:
        fail(f"the {label} element is {slot!r}, expected a Slot with .id and .start")
    pair.append((slot_id, start))

got_first, got_last = pair
if (got_first, got_last) == (INPUT_ORDER_FIRST, INPUT_ORDER_LAST):
    fail(
        f"first_and_last returned {got_first[0]} ({got_first[1]}) and "
        f"{got_last[0]} ({got_last[1]}), which is exactly the file's own entry "
        f"order; the earliest start is {WANT_FIRST[1]} ({WANT_FIRST[0]}) and the "
        f"latest is {WANT_LAST[1]} ({WANT_LAST[0]}) -- {COMMENT_LIE}"
    )
if got_first != WANT_FIRST or got_last != WANT_LAST:
    fail(
        f"first_and_last returned {got_first} / {got_last}, expected "
        f"{WANT_FIRST} / {WANT_LAST} (earliest and latest start) -- {COMMENT_LIE}"
    )

empty = first_and_last([{"id": "s-0", "staff": "cancelled"}])
if empty is not None:
    fail(f"with no usable slots first_and_last returned {empty!r}, expected None")

print("OK: first_and_last compares start times")
PY
