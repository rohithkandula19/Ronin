#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import os


def sources():
    for base, dirs, names in os.walk("."):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        for name in sorted(names):
            if name.endswith(".py"):
                path = os.path.join(base, name)
                yield path, open(path, encoding="utf-8").read()
from billing.totals import compute_total
from billing.invoice import invoice_total
from billing.report import grand_total

for path, text in sources():
    assert "calc_total" not in text, "%s still mentions calc_total" % path

assert compute_total([(100, 2), (50, 1)]) == 250
assert invoice_total([(100, 2)], 10) == 220
assert grand_total([[(100, 1)], [(200, 2)]]) == 500
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
