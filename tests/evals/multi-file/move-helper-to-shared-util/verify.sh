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
import os

assert os.path.exists("text/util.py"), "text/util.py was not created"
from text.util import normalise_ws
from text.parse import parse_headline
from text.clean import strip_footers

clean_src = open("text/clean.py", encoding="utf-8").read()
assert "def normalise_ws" not in clean_src, "text/clean.py still defines normalise_ws"
parse_src = open("text/parse.py", encoding="utf-8").read()
assert "text.clean" not in parse_src, "text/parse.py still imports from text.clean"

assert normalise_ws("  a   b \n c ") == "a b c"
assert parse_headline("  Big   News \nbody") == "Big News"
assert strip_footers("a\n--\nb") == "a"
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
