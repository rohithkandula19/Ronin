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
from api.transport import Transport
from api.session import Session
from api.client import Client

t = Transport()
c = Client(Session("http://h/", t))
assert c.get("/a") == "body of http://h/a"
assert t.calls[-1] == ("http://h/a", 30.0), t.calls
assert c.get("b", timeout=2.5) == "body of http://h/b"
assert t.calls[-1] == ("http://h/b", 2.5), t.calls

src = open("api/transport.py", encoding="utf-8").read()
assert "def send(self, url: str, timeout: float = 30.0) -> str:" in src, (
    "Transport.send was changed; the task said not to"
)
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
