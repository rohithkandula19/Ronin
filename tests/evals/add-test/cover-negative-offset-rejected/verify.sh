#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import hashlib
import os
import subprocess
import sys

RUNNER = '\nimport importlib.util\nimport sys\nimport traceback\n\npath = sys.argv[1]\nspec = importlib.util.spec_from_file_location("agent_tests", path)\nif spec is None or spec.loader is None:\n    print("IMPORTERROR")\n    raise SystemExit(3)\nmod = importlib.util.module_from_spec(spec)\nsys.modules["agent_tests"] = mod\ntry:\n    spec.loader.exec_module(mod)\nexcept BaseException:\n    traceback.print_exc()\n    print("IMPORTERROR")\n    raise SystemExit(3)\nnames = sorted(k for k in vars(mod) if k.startswith("test_") and callable(vars(mod)[k]))\nfailed = []\nfor name in names:\n    try:\n        vars(mod)[name]()\n    except BaseException:\n        failed.append(name)\n        traceback.print_exc()\nprint("NAMES " + " ".join(names))\nprint("FAILED " + " ".join(failed))\n'
TEST_FILE = 'tests/test_window.py'
IMPL = 'slice_/window.py'
REQUIRED = 'test_a_negative_offset_is_rejected'
IMPL_SHA = '13a357e869c2417a7df262194e6ccd44b6dd97c9d4420156aae2fb4b5493e567'
MUTANT = '"""Take a window out of a list."""\n\nfrom __future__ import annotations\n\n\ndef window(items: list[str], offset: int, size: int) -> list[str]:\n    if size < 0:\n        raise ValueError("size must not be negative, got %d" % size)\n    return items[offset : offset + size]\n'


def run():
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER, TEST_FILE], capture_output=True, text=True
    )
    names, failed = [], []
    for line in proc.stdout.splitlines():
        if line.startswith("NAMES "):
            names = line[6:].split()
        elif line.startswith("FAILED "):
            failed = line[7:].split()
    return proc, names, failed


if not os.path.exists(TEST_FILE):
    raise SystemExit("%s does not exist" % TEST_FILE)
original = open(IMPL, "rb").read()
if hashlib.sha256(original).hexdigest() != IMPL_SHA:
    raise SystemExit("%s was modified; this task asks only for a new test" % IMPL)

proc, names, failed = run()
if "IMPORTERROR" in proc.stdout:
    raise SystemExit("%s does not import:\n%s" % (TEST_FILE, proc.stderr))
if REQUIRED not in names:
    raise SystemExit(
        "%s does not define %s (found: %s)" % (TEST_FILE, REQUIRED, ", ".join(names) or "nothing")
    )
if failed:
    raise SystemExit(
        "these tests fail against the correct implementation: %s\n%s"
        % (", ".join(failed), proc.stderr)
    )

open(IMPL, "wb").write(MUTANT.encode("utf-8"))
try:
    proc2, names2, failed2 = run()
    if "IMPORTERROR" in proc2.stdout:
        raise SystemExit(
            "%s cannot even import against the reverted implementation, so it proves nothing"
            % TEST_FILE
        )
    if REQUIRED not in failed2:
        raise SystemExit(
            "%s still passes with the fix reverted, so it does not exercise the behaviour"
            % REQUIRED
        )
finally:
    open(IMPL, "wb").write(original)
PYCHECK
[ $? -eq 0 ] || fail "the new test does not pin the behaviour"
exit 0
