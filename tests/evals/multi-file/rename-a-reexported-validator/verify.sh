#!/usr/bin/env bash
# Checks that check_feed_payload -> collect_payload_problems was renamed at EVERY
# call site, not just where it is defined.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

out=$("$PY" - <<'CHECK'
import pathlib
import sys

sys.path.insert(0, ".")
OLD, NEW = "check_feed_payload", "collect_payload_problems"
GOOD = {"id": "a1", "title": "Hello", "url": "https://example.invalid/a1"}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def load(name):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001
        fail(f"`import {name}` raised {type(exc).__name__}: {exc} -- a caller still uses the old name")
    return sys.modules[name]


feedkit = load("feedkit")
if not hasattr(feedkit, NEW):
    fail(f"expected `from feedkit import {NEW}` to work, but feedkit/__init__.py does not export it")
if hasattr(feedkit, OLD):
    fail(f"feedkit still exports the old name {OLD}; it was supposed to be removed")

validate = load("feedkit.validate")
if not hasattr(validate, NEW):
    fail(f"expected feedkit/validate.py to define {NEW}, found {sorted(n for n in vars(validate) if 'payload' in n)}")
if hasattr(validate, OLD):
    fail(f"feedkit/validate.py still defines the old name {OLD}")
if getattr(validate, NEW).__module__ != "feedkit.validate":
    fail(f"{NEW} should still be defined in feedkit.validate, not {getattr(validate, NEW).__module__}")

pipeline = load("feedkit.pipeline")
report = load("feedkit.report")
cli = load("feedkit.cli")

if getattr(cli, NEW, None) is not getattr(validate, NEW):
    fail(f"feedkit/cli.py does not import {NEW} from the package -- its import was not updated")

def call(label, func, *args):
    try:
        return func(*args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(f"{label} raised {type(exc).__name__}: {exc} -- it still calls the old name")


got = call("feedkit.pipeline.ingest", pipeline.ingest, [GOOD, {"id": "b2"}])
if got["accepted"] != [GOOD] or len(got["rejected"]) != 1:
    fail(f"feedkit.pipeline.ingest returned {got!r}; expected 1 accepted and 1 rejected payload")

line = call("feedkit.report.render_summary", report.render_summary, GOOD)
if line != "a1: ok":
    fail(f"feedkit.report.render_summary(good payload) returned {line!r}, expected 'a1: ok'")
line = call("feedkit.report.render_summary", report.render_summary, {"id": "b2"})
if not line.startswith("b2: 2 problem(s)"):
    fail(f"feedkit.report.render_summary(bad payload) returned {line!r}")

for path in sorted(pathlib.Path(".").rglob("*.py")):
    if OLD in path.read_text(encoding="utf-8"):
        fail(f"{path} still mentions {OLD}; the old name has to be gone from the repo")
CHECK
)
rc=$?
if [ "$rc" -ne 0 ]; then
  if printf '%s\n' "$out" | grep -q '^FAIL:'; then
    printf '%s\n' "$out" | grep '^FAIL:' | head -1
  else
    echo "FAIL: the checks crashed before finishing: $(printf '%s\n' "$out" | tail -1)"
  fi
  exit 1
fi

if [ ! -f tests/test_validate.py ]; then
  echo "FAIL: tests/test_validate.py is missing -- it was supposed to be updated, not deleted"
  exit 1
fi

out=$("$PY" -m unittest tests.test_validate 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|FAILED|ERROR)' | head -1)
  echo "FAIL: the package's own tests do not pass after the rename (${reason:-see unittest output})"
  exit 1
fi
ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)
if [ "${ran:-0}" -lt 7 ]; then
  echo "FAIL: expected the 7 existing tests in tests/test_validate.py to still run, unittest ran ${ran:-0}"
  exit 1
fi
exit 0
