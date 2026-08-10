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

assert not os.path.exists("store/db.py"), "store/db.py still exists"
assert os.path.exists("store/reader.py"), "store/reader.py missing"
assert os.path.exists("store/writer.py"), "store/writer.py missing"
for path, text in sources():
    assert "store.db" not in text, "%s still references store.db" % path

from store.reader import Reader
from store.writer import Writer
from store.service import Service

rows = {}
Writer(rows).put("k", "v")
assert Reader(rows).get("k") == "v"
assert Reader(rows).get("nope") is None
assert Service().set_and_read("a", "b") == "b"
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
