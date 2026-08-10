#!/usr/bin/env bash
# Checks the checksum capability was added to every layer of the plugin system.
# Prints one FAIL line naming the layer that is missing.
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" || { echo "FAIL [harness] cannot enter workspace $ROOT"; exit 1; }
PY="${PYTHON:-python3}"

"$PY" - <<'PYCODE'
from __future__ import annotations

import json
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))


def fail(layer: str, message: str) -> None:
    print(f"FAIL [{layer}] {message}")
    raise SystemExit(1)


def expected(data: bytes) -> str:
    return f"crc32:{zlib.crc32(data) & 0xFFFFFFFF:08x}"


# --------------------------------------------------------------- catalog layer
try:
    from inspectorkit import capabilities as cap_module
except Exception as exc:  # noqa: BLE001
    fail("catalog", f"inspectorkit/capabilities.py does not import: {exc!r}")
catalog = dict(cap_module.CAPABILITIES)
if "checksum" not in catalog:
    fail(
        "catalog",
        "'checksum' is not in the capability catalog: inspectorkit/capabilities.py "
        f"declares {sorted(catalog)}",
    )
hook = getattr(catalog["checksum"], "hook", "")
if not isinstance(hook, str) or not hook:
    fail("catalog", f"the checksum capability names no hook method (hook={hook!r})")

# ------------------------------------------------------------ base class layer
try:
    from inspectorkit.blob import Blob
    from inspectorkit.errors import CapabilityNotSupported
    from inspectorkit.plugin import Plugin
except Exception as exc:  # noqa: BLE001
    fail("base", f"inspectorkit/plugin.py does not import: {exc!r}")
if not hasattr(Plugin, hook):
    fail(
        "base",
        f"the Plugin base class in inspectorkit/plugin.py defines no default {hook}() "
        "hook, so the registry cannot tell a provider of checksum from a non-provider",
    )
try:
    from inspectorkit.dispatch import run_one
    from inspectorkit.plugins.line_count import PLUGIN as LINE_COUNT
except Exception as exc:  # noqa: BLE001
    fail("base", f"could not load dispatch and the line_count plugin: {exc!r}")
blob = Blob(name="probe.txt", data=b"inspectorkit\n")
try:
    run_one(LINE_COUNT, "checksum", blob)
except CapabilityNotSupported:
    pass
except Exception as exc:  # noqa: BLE001
    fail(
        "base",
        "asking a plugin that does not provide checksum for one raised "
        f"{type(exc).__name__} instead of CapabilityNotSupported: {exc}",
    )
else:
    fail(
        "base",
        "line_count answered the checksum capability it does not declare; the default "
        "hook in inspectorkit/plugin.py must refuse",
    )

# -------------------------------------------------------------- registry layer
try:
    from inspectorkit.config import Settings
    from inspectorkit.loader import build_registry

    settings = Settings.load(ROOT / "config" / "plugins.json")
    registry = build_registry(settings)
except Exception as exc:  # noqa: BLE001
    fail(
        "registry",
        "building the registry from config/plugins.json failed, so the declaration and "
        f"the implementation disagree: {type(exc).__name__}: {exc}",
    )
providers = registry.providers_of("checksum")
if providers != ("bytes_stats",):
    fail(
        "registry",
        "the registry lists the providers of checksum as "
        f"{list(providers)}, expected exactly ['bytes_stats'] (a plugin must list a "
        "capability in `declares` to ever be asked for it)",
    )

# ---------------------------------------------------------------- plugin layer
try:
    result = run_one(registry.get("bytes_stats"), "checksum", blob)
except Exception as exc:  # noqa: BLE001
    fail("plugin", f"bytes_stats raised {type(exc).__name__} when asked for a checksum: {exc}")
if result.value != expected(blob.data):
    fail(
        "plugin",
        f"bytes_stats returned {result.value!r} for {blob.data!r}, expected "
        f"{expected(blob.data)!r} (the fingerprint format the tree scanner already uses)",
    )
empty = run_one(registry.get("bytes_stats"), "checksum", Blob(name="e", data=b"")).value
if empty != "crc32:00000000":
    fail("plugin", f"bytes_stats fingerprinted empty bytes as {empty!r}, expected 'crc32:00000000'")

# ---------------------------------------------------------------- report layer
sample = ROOT / "samples" / "notes.txt"
run = subprocess.run(
    [sys.executable, "inspect_file.py", "--path", str(sample.relative_to(ROOT)), "--all"],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    fail(
        "report",
        f"inspect_file.py --all exited {run.returncode}: "
        f"{(run.stderr or run.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
want = expected(sample.read_bytes())
if "checksum" not in run.stdout:
    order = json.loads((ROOT / "config" / "plugins.json").read_text(encoding="utf-8")).get(
        "report_order"
    )
    fail(
        "report",
        "the checksum never reaches the rendered report: report_order in "
        f"config/plugins.json is {order}",
    )
if want not in run.stdout:
    fail(
        "report",
        f"the report does not contain the fingerprint of samples/notes.txt ({want}); "
        f"report output was {run.stdout.strip()[-160:]!r}",
    )

run = subprocess.run(
    [
        sys.executable,
        "inspect_file.py",
        "--path",
        str(sample.relative_to(ROOT)),
        "--capability",
        "checksum",
    ],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0 or want not in run.stdout:
    fail(
        "report",
        "inspect_file.py --capability checksum did not print the fingerprint "
        f"(exit {run.returncode}, output {run.stdout.strip()[-160:]!r})",
    )

# ------------------------------------------------------------------ test layer
plugin_test = ROOT / "tests" / "test_plugins_bytes_stats.py"
if not plugin_test.exists():
    fail("tests", "tests/test_plugins_bytes_stats.py is missing")
if "checksum" not in plugin_test.read_text(encoding="utf-8"):
    fail(
        "tests",
        "tests/test_plugins_bytes_stats.py has no test covering the new capability: "
        "the word 'checksum' does not appear in it",
    )
run = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    tail = [ln for ln in (run.stderr or "").splitlines() if ln.strip()][-1:]
    fail("tests", f"the project's own test suite does not pass: {tail or ['no output']}")

print("OK checksum capability wired through catalog, base class, registry, plugin, report and tests")
PYCODE
