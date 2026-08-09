#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import sys

sys.path.insert(0, "src")


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


try:
    from schema import names
except Exception as exc:  # noqa: BLE001
    fail(f"could not import schema.names: {exc!r}")

# 1. The new name exists and does the old job.
canonicalize = getattr(names, "canonicalize", None)
if canonicalize is None:
    fail("schema.names has no `canonicalize` after the rename")
for raw, want in (
    ("First Name ", "first_name"),
    ("  Total $$ 2024 ", "total_2024"),
    ("Größe (m²)", "groe_m2"),
    ("ID", "id"),
    ("a--b", "a_b"),
):
    try:
        got = canonicalize(raw)
    except AttributeError as exc:
        fail(
            "canonicalize() raised AttributeError - the rename hit "
            f"unicodedata.normalize as well: {exc}"
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"canonicalize({raw!r}) raised {exc!r}")
    if got != want:
        fail(f"expected canonicalize({raw!r}) to be {want!r}, got {got!r}")

# 2. The old name is gone.
if hasattr(names, "normalize"):
    fail("schema.names still exports `normalize` - the old name should be gone after a rename")
if "normalize" in getattr(names, "__all__", ()):
    fail(f"__all__ still lists 'normalize': {names.__all__!r}")
if "canonicalize" not in getattr(names, "__all__", ()):
    fail(f"__all__ does not list 'canonicalize': {getattr(names, '__all__', None)!r}")

# 3. The helpers whose names merely *contain* the old one are untouched.
for helper in ("normalize_path", "denormalize"):
    if not hasattr(names, helper):
        fail(f"`{helper}` disappeared - only `normalize` itself was meant to be renamed")
    if helper not in getattr(names, "__all__", ()):
        fail(f"__all__ no longer lists {helper!r}: {names.__all__!r}")
got = names.normalize_path("./sheets/./q3/")
if got != "sheets/q3":
    fail(f"expected normalize_path('./sheets/./q3/') to be 'sheets/q3', got {got!r}")
got = names.denormalize("total_2024")
if got != "Total 2024":
    fail(f"expected denormalize('total_2024') to be 'Total 2024', got {got!r}")

# 4. The in-module caller was updated, not left dangling.
try:
    mapping = names.header_map(["First Name", "Notes", "notes", "Größe (m²)"])
except NameError as exc:
    fail(f"header_map still calls the old name: {exc}")
except Exception as exc:  # noqa: BLE001
    fail(f"header_map raised {exc!r}")
want_map = {
    "First Name": "first_name",
    "Notes": "notes",
    "notes": "notes_2",
    "Größe (m²)": "groe_m2",
}
if mapping != want_map:
    fail(f"expected header_map to return {want_map!r}, got {mapping!r}")
PYCODE
