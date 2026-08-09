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
import dataclasses
from stats.summary import Summary, summarise
from stats.report import render
from stats.alerts import should_alert

assert dataclasses.is_dataclass(Summary), "Summary must be a dataclass"
names = [f.name for f in dataclasses.fields(Summary)]
assert names == ["count", "total", "mean"], names
assert dataclasses.fields(Summary) and Summary.__dataclass_params__.frozen, "must be frozen"

s = summarise([1.0, 2.0, 3.0])
assert (s.count, s.total, s.mean) == (3, 6.0, 2.0), s
empty = summarise([])
assert (empty.count, empty.total, empty.mean) == (0, 0.0, 0.0), empty
assert render([1.0, 2.0, 3.0]) == "n=3 total=6.0 mean=2.00", render([1.0, 2.0, 3.0])
assert should_alert([5.0, 5.0], 4.0) is True
assert should_alert([1.0], 4.0) is False
assert should_alert([], 0.0) is False
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
