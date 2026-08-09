#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from invsync.apply import apply
from invsync.diff import compute, index
from invsync.mapping import canonical_sku
from invsync.records import Record

assert canonical_sku(" ab-1 ") == "AB-1", canonical_sku(" ab-1 ")
assert canonical_sku("AB-1") == "AB-1"

lower = Record(sku="ab-1", name="Widget", qty=5)
upper = Record(sku="AB-1", name="Widget", qty=5)
assert lower.key == upper.key == "AB-1", (lower.key, upper.key)
assert lower.sku == "ab-1", "the stored sku must keep the feed's spelling"

diff = compute([lower], [upper])
assert diff.added == [] and diff.updated == [] and diff.removed == [], diff

changed = compute([lower], [Record(sku="AB-1", name="Widget", qty=9)])
assert [r.key for r in changed.updated] == ["AB-1"], changed
assert changed.added == [] and changed.removed == []

gone = compute([lower], [])
assert gone.removed == ["AB-1"], gone.removed

target = index([lower])
assert sorted(target) == ["AB-1"], sorted(target)
result = apply(target, compute([lower], [Record(sku="AB-1", name="Widget", qty=9)]))
assert sorted(result) == ["AB-1"], sorted(result)
assert result["AB-1"].qty == 9

emptied = apply(target, gone)
assert emptied == {}, emptied

# The existing tests must still pass.
first = compute([], [Record(sku="a", name="A", qty=1)])
assert [r.sku for r in first.added] == ["a"]
assert compute([Record(sku="a", name="A", qty=1)], []).removed == ["A"]
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
