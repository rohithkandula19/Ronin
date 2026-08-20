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
from chunked.limits import CHUNK_BYTES
from chunked.reader import chunks
from chunked.writer import write_all
from chunked.copier import copy

assert CHUNK_BYTES == 4096, CHUNK_BYTES
for name in ("chunked/reader.py", "chunked/writer.py", "chunked/copier.py"):
    text = open(name, encoding="utf-8").read()
    assert "4096" not in text, "%s still hard-codes 4096" % name

data = b"x" * 10000
pieces = list(chunks(data))
assert [len(p) for p in pieces] == [4096, 4096, 1808], [len(p) for p in pieces]
sink = []
assert write_all(sink, data) == 10000
assert b"".join(sink) == data
sink2 = []
assert copy(data, sink2) == 10000
assert b"".join(sink2) == data
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
