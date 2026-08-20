#!/usr/bin/env bash
# Verifies the changelog renders, the section order really comes from the
# defaults table, and the KeyError was not simply caught.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' chg make_changelog.py 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the missing default to be supplied, but found exception suppression: $HIT"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = """## 1.4.0

### Added
- `chg render` writes to stdout ([#214](https://git.example.invalid/ronin/issues/214))
- read overrides from chg.toml ([#219](https://git.example.invalid/ronin/issues/219))

### Changed
- sort entries by kind, not by insertion order

### Fixed
- keep trailing newline in generated markdown

### Other
- drop the legacy YAML entry format
"""


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run([sys.executable, "make_changelog.py"], capture_output=True, text=True)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 make_changelog.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(
        "rendered changelog does not match the expected section order/content; got "
        f"{proc.stdout!r}"
    )

sys.path.insert(0, ".")
from chg.config import build_config  # noqa: E402

config = build_config()
if "section_order" not in config:
    die(
        "expected build_config() to supply 'section_order' from the defaults table, "
        f"but the merged config only has {sorted(config)}"
    )
if list(config["section_order"]) != ["added", "changed", "fixed", "other"]:
    die(
        "expected the default section order added/changed/fixed/other, got "
        f"{list(config['section_order'])!r}"
    )

# Because build_config drops keys absent from DEFAULTS, an override only lands
# if the default really exists - this rejects patching the read site instead.
overridden = build_config({"section_order": ["fixed", "added"]})
if list(overridden["section_order"]) != ["fixed", "added"]:
    die(
        "a chg.toml section_order override is still discarded; got "
        f"{list(overridden['section_order'])!r}"
    )
PY
