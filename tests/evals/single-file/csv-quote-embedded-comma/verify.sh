#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import export.csvout as m

src = open("export/csvout.py", encoding="utf-8").read()
assert "import csv" not in src, "the task said not to import the csv module"

q = chr(34)
assert m.csv_row(["a", "b"]) == "a,b"
assert m.csv_row(["a,b", "c"]) == q + "a,b" + q + ",c", m.csv_row(["a,b", "c"])
quoted = q + "say " + q * 2 + "hi" + q * 2 + q
assert m.csv_row([q.join(["say ", "hi", ""])]) == quoted, m.csv_row(["say " + q + "hi" + q])
assert m.csv_row(["two\nlines"]) == q + "two\nlines" + q, m.csv_row(["two\nlines"])
assert m.csv_row([""]) == ""
PYCHECK
[ $? -eq 0 ] || fail "export/csvout.py does not behave as the task asked"
exit 0
