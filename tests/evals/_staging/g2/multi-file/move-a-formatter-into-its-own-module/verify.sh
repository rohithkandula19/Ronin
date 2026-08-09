#!/usr/bin/env bash
# Checks that the slug code really moved out of textkit/util.py into
# textkit/slug.py and that every caller (including the aliased `import
# textkit.util as util` one) follows it.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

"$PY" - <<'CHECK'
import ast
import pathlib
import sys

sys.path.insert(0, ".")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def load(name):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001
        fail(f"`import {name}` raised {type(exc).__name__}: {exc}")
    return sys.modules[name]


slug_path = pathlib.Path("textkit/slug.py")
if not slug_path.is_file():
    fail("textkit/slug.py does not exist; the slug code was supposed to move into it")

slug_mod = load("textkit.slug")
if not hasattr(slug_mod, "slugify"):
    fail(f"textkit/slug.py does not define slugify, only {sorted(n for n in vars(slug_mod) if not n.startswith('__'))}")
slugify = slug_mod.slugify
if slugify.__module__ != "textkit.slug":
    fail(f"textkit.slug.slugify is still defined in {slugify.__module__}; it was re-exported, not moved")

util = load("textkit.util")
leftovers = [n for n in vars(util) if "slug" in n.lower()]
if leftovers:
    fail(f"textkit/util.py still carries slug names {sorted(leftovers)}; util should not know about slugs any more")

tree = ast.parse(pathlib.Path("textkit/util.py").read_text(encoding="utf-8"))
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and "slug" in node.name.lower():
        fail(f"textkit/util.py still defines {node.name}()")
    if isinstance(node, ast.ImportFrom) and "slug" in (node.module or ""):
        fail("textkit/util.py imports from textkit.slug; util should not depend on the slug module")
    if isinstance(node, ast.Import) and any("slug" in a.name for a in node.names):
        fail("textkit/util.py imports the slug module; util should not depend on it")

squashed = util.normalize_ws("a  b\tc")
if squashed != "a b c":
    fail(f"textkit.util.normalize_ws is broken: got {squashed!r}")
shortened = util.truncate("abcdefgh", 6)
if shortened != "abc...":
    fail(f"textkit.util.truncate is broken: got {shortened!r}")

textkit = load("textkit")
if getattr(textkit, "slugify", None) is not slugify:
    fail("`from textkit import slugify` no longer gives the moved function; the package's public API broke")

if slugify("The Quick Brown Fox") != "quick-brown-fox":
    fail(f"slugify('The Quick Brown Fox') returned {slugify('The Quick Brown Fox')!r}")

render = load("textkit.render")
card = render.render_card("The  Quick   Brown Fox", "a  b   c", width=5)
if card != {"slug": "quick-brown-fox", "title": "The Quick Brown Fox", "teaser": "a b c"}:
    fail(f"textkit.render.render_card returned {card!r}")
if render.render_anchor("one two three four five") != "#one-two-three-four":
    fail(f"textkit.render.render_anchor returned {render.render_anchor('one two three four five')!r}")

index_mod = load("textkit.index")
try:
    index = index_mod.build_index(["The Quick Brown Fox", "Unicode Café"])
except Exception as exc:  # noqa: BLE001
    fail(f"textkit.index.build_index raised {type(exc).__name__}: {exc} -- it still reaches for slugify through textkit.util")
if sorted(index) != ["quick-brown-fox", "unicode-cafe"]:
    fail(f"textkit.index.build_index produced keys {sorted(index)!r}")
try:
    got = index_mod.lookup(index, "unicode  café")
except Exception as exc:  # noqa: BLE001
    fail(f"textkit.index.lookup raised {type(exc).__name__}: {exc} -- it still reaches for slugify through textkit.util")
if got != "Unicode Café":
    fail(f"textkit.index.lookup returned {got!r}")
CHECK
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

if [ ! -f tests/test_slug.py ]; then
  echo "FAIL: tests/test_slug.py is missing -- it was supposed to be updated, not deleted"
  exit 1
fi
out=$("$PY" -m unittest tests.test_slug 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|FAILED|ERROR)' | head -1)
  echo "FAIL: tests/test_slug.py does not pass after the move (${reason:-see unittest output})"
  exit 1
fi
ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)
if [ "${ran:-0}" -lt 9 ]; then
  echo "FAIL: expected the 9 existing tests in tests/test_slug.py to still run, unittest ran ${ran:-0}"
  exit 1
fi
exit 0
