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
from money.rounding import half_up
from money.tax import tax_cents
from money.split import per_person

assert half_up(2.5) == 3, half_up(2.5)
assert half_up(3.5) == 4, half_up(3.5)
assert half_up(-2.5) == -3, half_up(-2.5)
assert half_up(2.4) == 2
assert half_up(0.0) == 0

for name in ("money/tax.py", "money/split.py"):
    text = open(name, encoding="utf-8").read()
    assert "round(" not in text, "%s still calls round(" % name

assert tax_cents(50, 0.05) == 3, tax_cents(50, 0.05)
assert per_person(5, 2) == 3, per_person(5, 2)
try:
    per_person(5, 0)
except ValueError:
    pass
else:
    raise AssertionError("per_person must still reject people<=0")
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
