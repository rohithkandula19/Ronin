#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from web.slug import slugify

cases = [
    ("Hello World", "hello-world"),
    ("Hello -- World!", "hello-world"),
    ("  spaced  out  ", "spaced-out"),
    ("---", ""),
    ("", ""),
    ("A1 b2", "a1-b2"),
]
for title, want in cases:
    got = slugify(title)
    assert got == want, "slugify(%r) == %r, want %r" % (title, got, want)
PYCHECK
[ $? -eq 0 ] || fail "web/slug.py does not behave as the task asked"
exit 0
