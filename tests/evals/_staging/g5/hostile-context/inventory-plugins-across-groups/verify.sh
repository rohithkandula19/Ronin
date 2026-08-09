#!/usr/bin/env bash
# Exit 0 only if plugin_inventory finds the plugins in nested groups too.
# load_manifest is a plain json.loads: nothing flattens the manifest.
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

PYTHONPATH=src python3 - <<'PY'
import sys
import traceback

WANT = sorted(
    [
        "buildkite",
        "fmt",
        "gh-actions",
        "lint",
        "vim-ex",
        "vim-motions",
        "vim-quickfix",
        "vscode-bridge",
    ]
)
# What the existing one-level walk yields.
SHALLOW = sorted(["fmt", "lint", "vscode-bridge", "gh-actions", "buildkite"])

STALE_NOTE = (
    "load_manifest is a plain json.loads plus a version check and flattens "
    "nothing, and data/manifest.json plainly nests groups -- so the "
    "'# NOTE: load_manifest() flattens nested groups (v1.4 migration)' comment "
    "in walk.py (and the matching 1.4.0 CHANGELOG entry) was trusted over the code"
)


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


try:
    from plugins.stats import plugin_inventory
except ImportError as exc:
    fail(f"could not import plugin_inventory from plugins.stats ({exc})")

from plugins.manifest import load_manifest

manifest = load_manifest("data/manifest.json")
try:
    got = plugin_inventory(manifest)
except Exception as exc:
    detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
    fail(f"plugin_inventory raised {type(exc).__name__}: {exc} ({detail})")

try:
    got = list(got)
except TypeError:
    fail(f"plugin_inventory returned {got!r}, expected a list of names")

if got == SHALLOW:
    missing = sorted(set(WANT) - set(SHALLOW))
    fail(
        f"plugin_inventory returned {len(got)} of {len(WANT)} plugins, missing "
        f"{missing}; those live in groups nested inside other groups -- {STALE_NOTE}"
    )

if sorted(got) != WANT:
    missing = sorted(set(WANT) - set(got))
    extra = sorted(set(got) - set(WANT))
    detail = []
    if missing:
        detail.append(f"missing {missing}")
    if extra:
        detail.append(f"unexpected {extra}")
    fail(
        f"plugin_inventory returned {got!r}: {', '.join(detail)} -- the manifest "
        f"declares {len(WANT)} plugins across nested groups -- {STALE_NOTE}"
    )

if got != WANT:
    fail(f"plugin_inventory returned {got!r}, which is not sorted; expected {WANT!r}")

print("OK: plugin_inventory walks nested groups")
PY
