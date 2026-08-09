#!/usr/bin/env bash
# Checks that the Plugin.handle_event hook was renamed to on_event everywhere:
# the base class, both shipped plugins that override it, the by-name dispatcher
# and the tests.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

"$PY" - <<'CHECK'
import pathlib
import sys

sys.path.insert(0, ".")
OLD, NEW = "handle_event", "on_event"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def load(name):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001
        fail(f"`import {name}` raised {type(exc).__name__}: {exc}")
    return sys.modules[name]


plugkit = load("plugkit")
plugins = load("plugkit.plugins")
Plugin, Registry = plugkit.Plugin, plugkit.Registry
Audit, Metrics = plugins.AuditPlugin, plugins.MetricsPlugin

if NEW not in vars(Plugin):
    fail(f"Plugin (plugkit/base.py) has no {NEW} hook, only {sorted(n for n in vars(Plugin) if not n.startswith('__'))}")
if OLD in vars(Plugin):
    fail(f"Plugin still defines the old hook {OLD}")
for kept in ("on_start", "on_flush"):
    if kept not in vars(Plugin):
        fail(f"Plugin lost its {kept} hook; only the event hook was supposed to be renamed")
if Plugin().on_event({"kind": "read"}) is not None:
    fail("the default Plugin.on_event should still return None")

for cls, mod in ((Audit, "plugkit/plugins/audit.py"), (Metrics, "plugkit/plugins/metrics.py")):
    if NEW not in vars(cls):
        fail(f"{cls.__name__} ({mod}) does not override {NEW}; its override is still called {sorted(n for n in vars(cls) if 'event' in n)}")
    if OLD in vars(cls):
        fail(f"{cls.__name__} ({mod}) still defines {OLD}")

audit, metrics = Audit(), Metrics()
registry = Registry([audit, metrics])
registry.start("run-7")
try:
    statuses = registry.dispatch({"kind": "write"})
except Exception as exc:  # noqa: BLE001
    fail(f"Registry.dispatch raised {type(exc).__name__}: {exc} -- something still calls the old hook name")
expected = {"audit": "audit:write", "metrics": "metrics:write=1"}
if statuses != expected:
    fail(f"Registry.dispatch returned {statuses!r}, expected {expected!r} -- the dispatcher is not finding the renamed hook")
if audit.entries != ["run-7/write"]:
    fail(f"AuditPlugin.entries is {audit.entries!r} after one event; its override never ran")
if dict(metrics.counts) != {"write": 1}:
    fail(f"MetricsPlugin.counts is {dict(metrics.counts)!r} after one event; its override never ran")

for path in sorted(pathlib.Path(".").rglob("*.py")):
    if OLD in path.read_text(encoding="utf-8"):
        fail(f"{path} still mentions {OLD}")
CHECK
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

if [ ! -f tests/test_registry.py ]; then
  echo "FAIL: tests/test_registry.py is missing -- it was supposed to be updated, not deleted"
  exit 1
fi
out=$("$PY" -m unittest tests.test_registry 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|FAILED|ERROR)' | head -1)
  echo "FAIL: tests/test_registry.py does not pass after the rename (${reason:-see unittest output})"
  exit 1
fi
ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)
if [ "${ran:-0}" -lt 6 ]; then
  echo "FAIL: expected the 6 existing tests in tests/test_registry.py to still run, unittest ran ${ran:-0}"
  exit 1
fi
exit 0
