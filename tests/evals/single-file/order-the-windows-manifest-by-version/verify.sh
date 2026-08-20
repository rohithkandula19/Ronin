#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import re
import sys
from pathlib import Path

sys.path.insert(0, "tools")

SOURCE = Path("tools/manifest.py")


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


if not SOURCE.exists():
    fail(f"{SOURCE} is missing")

# 1. The file is read by a Windows INI parser that rejects bare LF, so every
#    line ending in the source must still be CRLF after the edit.
data = SOURCE.read_bytes()
total = data.count(b"\n")
crlf = data.count(b"\r\n")
if total != crlf:
    fail(
        f"{SOURCE} must keep its CRLF line endings: {total} line endings, "
        f"{crlf} of them CRLF, {total - crlf} bare LF"
    )
if total == 0:
    fail(f"{SOURCE} has no line endings at all")

try:
    from manifest import Release, parse_index, render_manifest
except Exception as exc:  # noqa: BLE001
    fail(f"could not import tools/manifest.py: {exc!r}")

releases = parse_index(Path("releases.index").read_text(encoding="utf-8"))
if len(releases) != 5:
    fail(f"expected parse_index to read 5 releases from releases.index, got {len(releases)}")

out = render_manifest(releases)

# 2. Newest first, ordered numerically rather than as text.
order = re.findall(r"^\[(.+?)\]", out, flags=re.MULTILINE)
want = ["2.0.0", "1.10.1", "1.10.0", "1.9.10", "1.9.0"]
if order != want:
    fail(f"expected manifest sections in the order {want}, got {order}")

# 3. The rendered manifest itself must still be CRLF-delimited.
if "\r\n" not in out:
    fail("the rendered manifest no longer uses CRLF line endings")
out_lf = out.count("\n")
out_crlf = out.count("\r\n")
if out_lf != out_crlf:
    fail(
        "the rendered manifest mixes line endings: "
        f"{out_lf} line endings, {out_crlf} of them CRLF"
    )

# 4. The body of each entry is unchanged.
for release in releases:
    block = f"[{release.version}]\r\nchannel={release.channel}\r\nsha256={release.sha256}\r\n"
    if block not in out:
        fail(f"expected the manifest to still contain the full entry for {release.version}")
if not out.startswith("; ronin update manifest"):
    fail(f"the manifest lost its header comment: it starts {out[:40]!r}")

# 5. A single release, and a malformed index, still behave.
single = render_manifest([Release("3.1.4", "stable", "deadbeefdeadbeef")])
if "[3.1.4]" not in single:
    fail("expected render_manifest to handle a single release")
try:
    parse_index("1.0.0 stable\n")
except ValueError:
    pass
else:
    fail("expected parse_index to raise ValueError on a malformed line")
PYCODE
