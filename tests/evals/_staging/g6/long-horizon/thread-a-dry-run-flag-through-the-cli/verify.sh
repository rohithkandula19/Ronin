#!/usr/bin/env bash
# Checks --dry-run is threaded through the parser, the config defaults, the Options
# object and both writing commands. One FAIL line, naming the layer at fault.
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" || { echo "FAIL [harness] cannot enter workspace $ROOT"; exit 1; }
PY="${PYTHON:-python3}"
TMP="$(mktemp -d "$ROOT/.verify_tmp.XXXXXX")" || { echo "FAIL [harness] cannot create temp dir"; exit 1; }
trap 'rm -rf "$TMP"' EXIT

"$PY" - "$TMP" <<'PYCODE'
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

TMP = Path(sys.argv[1])
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

EXPIRED = ("app-2024-04-01.log", "worker-2024-03-02.log")
KEPT = ("app-2024-04-20.log",)
CURRENT = ("app.log", "worker.log")


def fail(layer: str, message: str) -> None:
    print(f"FAIL [{layer}] {message}")
    raise SystemExit(1)


def make_tree(name: str) -> Path:
    root = TMP / name
    root.mkdir(parents=True, exist_ok=True)
    for filename in EXPIRED + KEPT:
        (root / filename).write_text("archived line\n", encoding="utf-8")
    for filename in CURRENT:
        (root / filename).write_text("busy line\n", encoding="utf-8")
    return root


def run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atticus", *argv],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------- parser layer
try:
    from atticus.cli.parser import build_parser
except Exception as exc:  # noqa: BLE001
    fail("parser", f"atticus/cli/parser.py does not import: {exc!r}")
try:
    parsed = build_parser().parse_args(["--dry-run", "--now", "2024-05-01", "prune"])
except SystemExit:
    fail(
        "parser",
        "--dry-run is not accepted as a global flag: `atticus --dry-run prune` was "
        "rejected by the argument parser",
    )
if not getattr(parsed, "dry_run", False):
    fail(
        "parser",
        "parsing `--dry-run prune` produced no truthy dry_run on the namespace: got "
        f"{getattr(parsed, 'dry_run', '<missing>')!r}",
    )

# --------------------------------------------------------------- options layer
try:
    from atticus.cli.config_file import Defaults
    from atticus.cli.options import Options
except Exception as exc:  # noqa: BLE001
    fail("options", f"atticus/cli/options.py does not import: {exc!r}")
field_names = [f.name for f in dataclasses.fields(Options)]
dry_fields = [name for name in field_names if "dry" in name]
if not dry_fields:
    fail(
        "options",
        "the Options object every command is handed has no dry-run field: its fields are "
        f"{field_names}",
    )
merged = Options.merge(parsed, Defaults())
if not getattr(merged, dry_fields[0]):
    fail(
        "options",
        f"Options.merge did not carry the flag onto {dry_fields[0]}: got "
        f"{getattr(merged, dry_fields[0])!r} for an invocation with --dry-run",
    )

# ---------------------------------------------------------------- prune layer
root = make_tree("prune_dry")
result = run_cli(["--root", str(root), "--now", "2024-05-01", "--dry-run", "prune", "--keep-days", "14"])
if result.returncode != 0:
    fail(
        "prune",
        f"a dry-run prune exited {result.returncode}: "
        f"{(result.stderr or result.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
missing = [name for name in EXPIRED if not (root / name).exists()]
if missing:
    fail(
        "prune",
        f"a dry-run prune deleted {missing}; --dry-run must reach the prune command, not "
        "just the parser",
    )
stdout = result.stdout
for name in EXPIRED:
    if not any("would" in line and name in line for line in stdout.splitlines()):
        fail(
            "prune",
            f"a dry-run prune did not report what it would have done to {name}; output was "
            f"{stdout.strip()[:200]!r}",
        )

# ---------------------------------------------------------------- rotate layer
root = make_tree("rotate_dry")
result = run_cli(["--root", str(root), "--now", "2024-05-01", "--dry-run", "rotate"])
if result.returncode != 0:
    fail(
        "rotate",
        f"a dry-run rotate exited {result.returncode}: "
        f"{(result.stderr or result.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
created = sorted(p.name for p in root.iterdir() if p.name.endswith("2024-05-01.log"))
if created:
    fail("rotate", f"a dry-run rotate created {created} on disk")
if (root / "app.log").read_text(encoding="utf-8") != "busy line\n":
    fail("rotate", "a dry-run rotate emptied app.log; nothing should have been written")
for name in CURRENT:
    if not any("would" in line and name in line for line in result.stdout.splitlines()):
        fail(
            "rotate",
            f"a dry-run rotate did not report what it would have done to {name}; output was "
            f"{result.stdout.strip()[:200]!r}",
        )

# ---------------------------------------------------------------- config layer
config = json.loads((ROOT / "config" / "atticus.json").read_text(encoding="utf-8"))
dry_keys = [key for key in config if "dry" in key]
if not dry_keys:
    fail(
        "config",
        f"config/atticus.json has no dry-run key; its keys are {sorted(config)}",
    )
if config[dry_keys[0]] is not False:
    fail(
        "config",
        f"config/atticus.json ships {dry_keys[0]}={config[dry_keys[0]]!r}; it must default "
        "to false so nothing changes for existing boxes",
    )
default_fields = [f.name for f in dataclasses.fields(Defaults)]
if not [name for name in default_fields if "dry" in name]:
    fail(
        "config",
        "the Defaults read from config/atticus.json has no dry-run field, so the key in "
        f"the file is ignored: its fields are {default_fields}",
    )
tuned = dict(config)
tuned[dry_keys[0]] = True
tuned_path = TMP / "atticus_dry.json"
tuned_path.write_text(json.dumps(tuned), encoding="utf-8")
root = make_tree("config_dry")
result = run_cli(
    ["--config", str(tuned_path), "--root", str(root), "--now", "2024-05-01", "prune", "--keep-days", "14"]
)
if result.returncode != 0:
    fail(
        "config",
        f"prune with {dry_keys[0]}=true in the config exited {result.returncode}: "
        f"{(result.stderr or result.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
deleted = [name for name in EXPIRED if not (root / name).exists()]
if deleted:
    fail(
        "config",
        f"{dry_keys[0]}=true in the config file did not make the run a dry run: {deleted} "
        "were deleted anyway",
    )

# ------------------------------------------------------- regression: still acts
root = make_tree("prune_wet")
result = run_cli(["--root", str(root), "--now", "2024-05-01", "prune", "--keep-days", "14"])
if result.returncode != 0:
    fail("regression", f"a normal prune exited {result.returncode}: {result.stderr.strip()[:150]!r}")
left = sorted(p.name for p in root.iterdir())
if left != sorted(KEPT + CURRENT):
    fail(
        "regression",
        f"a prune without --dry-run left {left}, expected {sorted(KEPT + CURRENT)}; the flag "
        "must not change what happens when it is absent",
    )

root = make_tree("rotate_wet")
result = run_cli(["--root", str(root), "--now", "2024-05-01", "rotate"])
if result.returncode != 0:
    fail("regression", f"a normal rotate exited {result.returncode}: {result.stderr.strip()[:150]!r}")
if not (root / "app-2024-05-01.log").exists():
    fail("regression", "a rotate without --dry-run no longer rotates app.log")
if (root / "app.log").read_text(encoding="utf-8") != "":
    fail("regression", "a rotate without --dry-run no longer starts an empty app.log")

run = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    tail = [ln for ln in (run.stderr or "").splitlines() if ln.strip()][-1:]
    fail("regression", f"the project's own tests no longer pass: {tail or ['no output']}")

print("OK --dry-run threaded through parser, options, config defaults, prune and rotate")
PYCODE
