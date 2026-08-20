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

assert os.path.exists("signup/validate.py"), "signup/validate.py was not created"
from signup.validate import is_valid_address
from signup.web import accept_form
from signup.importer import import_rows

web_src = open("signup/web.py", encoding="utf-8").read()
imp_src = open("signup/importer.py", encoding="utf-8").read()
assert "def _check" not in web_src, "signup/web.py still has its private copy"
assert "_looks_like_email" not in imp_src, "signup/importer.py still has its private copy"

good = ["a@b.co", "x.y@sub.example.org"]
bad = ["a@b", "@b.co", "a@@b.co", "ab.co", "a@.co", "a@b.", ""]
for value in good:
    assert is_valid_address(value) is True, value
    assert accept_form(value) == "accepted", value
for value in bad:
    assert is_valid_address(value) is False, value
    assert accept_form(value) == "rejected", value
assert import_rows(good + bad) == good
PYCHECK
[ $? -eq 0 ] || fail "the refactor is incomplete or broke behaviour"
exit 0
