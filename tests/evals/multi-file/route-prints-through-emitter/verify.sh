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
from cli.emit import Emitter
from cli.scan import scan
from cli.fix import fix
from cli.summary import summary

for name in ("cli/scan.py", "cli/fix.py", "cli/summary.py"):
    text = open(name, encoding="utf-8").read()
    assert "print(" not in text, "%s still calls print(" % name

e = Emitter()
assert scan(["a", "b"], emitter=e) == 0
assert e.lines == ["scanning a", "scanning b", "scanned 2 path(s)"], e.lines

e = Emitter()
assert fix(["p"], emitter=e) == 0
assert e.lines == ["fixed p"], e.lines

e = Emitter()
assert fix(["p"], dry_run=True, emitter=e) == 0
assert e.lines == ["would fix p"], e.lines

e = Emitter()
assert summary({"b": 2, "a": 1}, emitter=e) == 0
assert e.lines == ["a: 1", "b: 2"], e.lines
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
