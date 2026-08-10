#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import os
import shutil
import tempfile

from walker.scan import scan

root = tempfile.mkdtemp(dir=".")
try:
    os.makedirs(os.path.join(root, "sub"))
    for rel in ("a.py", "b.pyc", "keep.txt", "sub/c.py", "sub/d.pyc"):
        with open(os.path.join(root, rel), "w", encoding="utf-8") as handle:
            handle.write("x")
    assert scan(root) == ["a.py", "b.pyc", "keep.txt", "sub/c.py", "sub/d.pyc"], scan(root)

    with open(os.path.join(root, ".scanignore"), "w", encoding="utf-8") as handle:
        handle.write("# comment\n\n*.pyc\nkeep.txt\n")
    assert scan(root) == ["a.py", "sub/c.py"], scan(root)

    with open(os.path.join(root, ".scanignore"), "w", encoding="utf-8") as handle:
        handle.write("sub/*\n")
    assert scan(root) == ["a.py", "b.pyc", "keep.txt"], scan(root)
finally:
    shutil.rmtree(root)
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
