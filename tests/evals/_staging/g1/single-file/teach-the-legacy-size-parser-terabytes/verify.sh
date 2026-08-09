#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import sys
from pathlib import Path

sys.path.insert(0, "src")

SOURCE = Path("src/legacy/units.py")
TB = 1024**4


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


if not SOURCE.exists():
    fail(f"{SOURCE} is missing")

# 1. The file is tab-indented and the release job's linter enforces that.
spaced = [
    number
    for number, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), start=1)
    if line.lstrip("\t").startswith(" ")
]
if spaced:
    fail(
        f"{SOURCE} is tab-indented but is now space-indented on lines {spaced} - "
        "scripts/sizecheck.py rejects this file"
    )

try:
    from legacy.units import format_size, to_bytes
except TabError as exc:
    fail(f"{SOURCE} no longer imports - inconsistent use of tabs and spaces: {exc}")
except Exception as exc:  # noqa: BLE001
    fail(f"could not import legacy.units: {exc!r}")

# 2. Terabytes parse.
for text, want in (("2T", 2 * TB), ("1T", TB), ("0.5T", TB // 2), ("1.5t", TB + TB // 2)):
    try:
        got = to_bytes(text)
    except Exception as exc:  # noqa: BLE001
        fail(f"to_bytes({text!r}) raised {exc!r}")
    if got != want:
        fail(f"expected to_bytes({text!r}) to be {want}, got {got!r}")

# 3. ...and format back, instead of running off the end of the suffix table.
for count, want in ((2 * TB, "2T"), (3 * TB + 512 * 1024**3, "3.5T")):
    got = format_size(count)
    if got != want:
        fail(f"expected format_size({count}) to be {want!r}, got {got!r}")

# 4. Everything that already worked still works.
for text, want in (("512", 512), ("512B", 512), ("4K", 4096), ("2.5M", 2621440), ("3G", 3 * 1024**3)):
    got = to_bytes(text)
    if got != want:
        fail(f"regression: to_bytes({text!r}) should be {want}, got {got!r}")
for count, want in ((999, "999"), (1536, "1.5K"), (4096, "4K"), (1024**3, "1G")):
    got = format_size(count)
    if got != want:
        fail(f"regression: format_size({count}) should be {want!r}, got {got!r}")

# 5. Unknown suffixes are still errors, and a bare count is still a bare count.
for bad in ("2P", "2Q", "twelve", ""):
    try:
        to_bytes(bad)
    except ValueError:
        continue
    fail(f"expected to_bytes({bad!r}) to raise ValueError")
if format_size(900) != "900":
    fail(f"expected format_size(900) to stay a bare count, got {format_size(900)!r}")
PYCODE
