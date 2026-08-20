#!/usr/bin/env bash
# Checks that JsonFileStore -> FileStore was renamed everywhere, including the
# `module:Attribute` strings in the alias table and in config/backends.json.
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
import tempfile

sys.path.insert(0, ".")
OLD, NEW = "JsonFileStore", "FileStore"
CONFIG = "config/backends.json"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def load(name):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001
        fail(f"`import {name}` raised {type(exc).__name__}: {exc}")
    return sys.modules[name]


storekit = load("storekit")
backends = load("storekit.backends")

store_cls = getattr(backends, NEW, None)
if store_cls is None:
    fail(f"storekit.backends does not export {NEW}; it exports {sorted(n for n in vars(backends) if n.endswith('Store'))}")
if hasattr(backends, OLD):
    fail(f"storekit.backends still exports the old name {OLD}")
if store_cls.__name__ != NEW:
    fail(f"storekit.backends.{NEW} is a class called {store_cls.__name__!r}, not {NEW!r} -- looks like an alias, not a rename")

for alias in ("file", "json"):
    try:
        got = storekit.resolve(alias)
    except Exception as exc:  # noqa: BLE001
        fail(f"resolve({alias!r}) raised {type(exc).__name__}: {exc} -- the alias table in storekit/registry.py still points at the old class name")
    if got is not store_cls:
        fail(f"resolve({alias!r}) returned {got!r}, expected the {NEW} class")

if storekit.resolve("memory").__name__ != "MemoryStore":
    fail("resolve('memory') no longer returns MemoryStore; only the file-backed store was supposed to change")

if storekit.alias_for(store_cls) not in {"file", "json"}:
    fail(f"alias_for({NEW}) returned {storekit.alias_for(store_cls)!r}; the reverse lookup no longer matches the alias table")

if not pathlib.Path(CONFIG).is_file():
    fail(f"{CONFIG} is missing")
try:
    cls, options = storekit.load_profile(CONFIG)
except Exception as exc:  # noqa: BLE001
    fail(f"load_profile({CONFIG!r}) raised {type(exc).__name__}: {exc} -- the target string in {CONFIG} still names the old class")
if cls is not store_cls:
    fail(f"the default profile in {CONFIG} resolved to {cls!r}, expected the {NEW} class")
if options != {"path": "var/store.json", "indent": 2}:
    fail(f"the default profile options changed to {options!r}; only the class name was supposed to change")

with tempfile.TemporaryDirectory() as tmp:
    try:
        store = storekit.open_profile(CONFIG, path=pathlib.Path(tmp) / "store.json")
    except Exception as exc:  # noqa: BLE001
        fail(f"open_profile({CONFIG!r}) raised {type(exc).__name__}: {exc}")
    store.put("token", "abc")
    if store.get("token") != "abc" or store.keys() != ["token"]:
        fail(f"the store from the default profile did not round-trip a value: get={store.get('token')!r} keys={store.keys()!r}")
    if store_cls(pathlib.Path(tmp) / "store.json").get("token") != "abc":
        fail("reopening the JSON document did not see the written value")

for path in sorted(pathlib.Path(".").rglob("*")):
    if path.suffix in {".py", ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml"} and path.is_file():
        if OLD in path.read_text(encoding="utf-8"):
            fail(f"{path} still mentions {OLD}")
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
