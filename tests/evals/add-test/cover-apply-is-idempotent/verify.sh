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

RUNNER = chr(10).join([
    '',
    'import importlib.util',
    'import sys',
    'import traceback',
    '',
    'path = sys.argv[1]',
    'spec = importlib.util.spec_from_file_location("agent_tests", path)',
    'if spec is None or spec.loader is None:',
    '    print("IMPORTERROR")',
    '    raise SystemExit(3)',
    'mod = importlib.util.module_from_spec(spec)',
    'sys.modules["agent_tests"] = mod',
    'try:',
    '    spec.loader.exec_module(mod)',
    'except BaseException:',
    '    traceback.print_exc()',
    '    print("IMPORTERROR")',
    '    raise SystemExit(3)',
    'names = sorted(k for k in vars(mod) if k.startswith("test_") and callable(vars(mod)[k]))',
    'failed = []',
    'for name in names:',
    '    try:',
    '        vars(mod)[name]()',
    '    except BaseException:',
    '        failed.append(name)',
    '        traceback.print_exc()',
    'print("NAMES " + " ".join(names))',
    'print("FAILED " + " ".join(failed))',
    '',
])

#: The implementation with the fix reverted. A new test that still passes
#: against this proves nothing, so verify.sh rejects it.
MUTANT = chr(10).join([
    '"""Prepend a licence header, at most once."""',
    '',
    'from __future__ import annotations',
    '',
    'HEADER = "# SPDX-License-Identifier: MIT\\n"',
    '',
    '',
    'def apply(text: str) -> str:',
    '    """Ensure ``text`` starts with ``HEADER``. Idempotent by design."""',
    '    return HEADER + text',
    '',
])

TEST_FILE = 'tests/test_apply.py'
IMPL = 'header/apply.py'
REQUIRED = 'test_applying_the_header_twice_changes_nothing'
IMPL_SHA = 'b578fb3fc30ee42a0fe0ff9a1b7eced47c5d14ee4169d2e22b75ff3073f8f129'


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
