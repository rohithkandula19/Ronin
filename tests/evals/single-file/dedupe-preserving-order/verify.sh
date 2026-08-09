#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import os
import subprocess
import sys

from pipeline.collect import unique_hosts

HOSTS = [
    "web-07", "db-02", "web-01", "cache-03", "web-07", "edge-11",
    "db-02", "api-05", "edge-11", "store-09", "api-05", "web-01",
]
WANT = ["web-07", "db-02", "web-01", "cache-03", "edge-11", "api-05", "store-09"]

got = unique_hosts(list(HOSTS))
assert got == WANT, got
assert unique_hosts([]) == []
assert unique_hosts(["x"]) == ["x"]
assert unique_hosts(["x", "x", "x"]) == ["x"]

# Order must not depend on hash randomisation, which is the whole point.
code = "from pipeline.collect import unique_hosts; print(unique_hosts(%r))" % (HOSTS,)
seen = set()
for seed in ("0", "1", "12345"):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, proc.stderr
    seen.add(proc.stdout.strip())
assert len(seen) == 1, "output changes with PYTHONHASHSEED: %r" % sorted(seen)
assert seen == {str(WANT)}, sorted(seen)
PYCHECK
[ $? -eq 0 ] || fail "pipeline/collect.py does not behave as the task asked"
exit 0
