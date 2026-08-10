#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import os

src = open("legacy/shim.py", encoding="utf-8").read()
assert "def coerce_flag" in src, "coerce_flag is called from app/boot.py and must stay"
assert "def legacy_path" not in src, "legacy_path is the unused helper and must be deleted"

from app.boot import debug_enabled
from legacy.shim import coerce_flag

assert coerce_flag(" YES ") is True
assert coerce_flag("off") is False
assert debug_enabled("1") is True
assert debug_enabled("no") is False
assert not hasattr(__import__("legacy.shim", fromlist=["x"]), "legacy_path")
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
