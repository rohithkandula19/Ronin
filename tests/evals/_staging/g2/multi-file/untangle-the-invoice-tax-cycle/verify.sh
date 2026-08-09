#!/usr/bin/env bash
# Checks that the billing.invoice <-> billing.tax circular import is gone for real:
# every module imports cleanly on its own, the module-level import graph is acyclic,
# no module hides an import inside a function, and the money helpers moved.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

if [ ! -f billing/money.py ]; then
  echo "FAIL: billing/money.py does not exist; the shared currency helpers were supposed to move there"
  exit 1
fi

# Each module first, in a fresh interpreter: a cycle often only bites in one order.
for mod in billing billing.money billing.tax billing.invoice billing.report; do
  err=$(PYTHONPATH="$ROOT" "$PY" -c "import $mod" 2>&1)
  if [ $? -ne 0 ]; then
    reason=$(printf '%s\n' "$err" | tail -1)
    echo "FAIL: \`python -c 'import $mod'\` failed on its own: $reason"
    exit 1
  fi
done

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


pkg = pathlib.Path("billing")
sources = {}
for path in sorted(pkg.rglob("*.py")):
    name = "billing" if path.name == "__init__.py" else f"billing.{path.stem}"
    sources[name] = (path, ast.parse(path.read_text(encoding="utf-8")))

# 1. no import hidden inside a function body
for name, (path, tree) in sources.items():
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Import | ast.ImportFrom):
                what = getattr(inner, "module", None) or ",".join(a.name for a in inner.names)
                fail(f"{path} still imports {what} inside {node.name}(); imports belong at module scope")

# 2. the module-level import graph inside the package must be acyclic
edges: dict[str, set[str]] = {}
for name, (_path, tree) in sources.items():
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "billing" if not module else f"billing.{module}"
            if module == "billing":
                targets.update(f"billing.{a.name}" for a in node.names)
                targets.add("billing")
            elif module.startswith("billing."):
                targets.add(module)
        elif isinstance(node, ast.Import):
            targets.update(a.name for a in node.names if a.name.startswith("billing"))
    edges[name] = {t for t in targets if t in sources and t != name}

state: dict[str, int] = {}


def walk(node: str, trail: list[str]) -> None:
    state[node] = 1
    for nxt in sorted(edges.get(node, ())):
        if state.get(nxt) == 1:
            cycle = " -> ".join([*trail, node, nxt])
            fail(f"the import graph still has a cycle: {cycle}")
        if state.get(nxt, 0) == 0:
            walk(nxt, [*trail, node])
    state[node] = 2


for name in sorted(sources):
    if state.get(name, 0) == 0:
        walk(name, [])

# 3. the shared money helpers live in billing/money.py, defined there, once
if not pathlib.Path("billing/money.py").is_file():
    fail("billing/money.py does not exist; the shared currency helpers were supposed to move there")
money = load("billing.money")
for helper in ("normalize_currency", "to_minor_units", "CURRENCY_SCALE"):
    if not hasattr(money, helper):
        fail(f"billing/money.py does not provide {helper}")
if money.normalize_currency.__module__ != "billing.money":
    fail(f"normalize_currency is still defined in {money.normalize_currency.__module__}, not billing.money")

invoice = load("billing.invoice")
if "normalize_currency" in vars(invoice) and vars(invoice)["normalize_currency"] is not money.normalize_currency:
    fail("billing/invoice.py still defines its own normalize_currency; there must be exactly one")
tax = load("billing.tax")
if getattr(tax, "normalize_currency", None) is not money.normalize_currency:
    fail("billing/tax.py does not import normalize_currency from billing.money at module scope")

# 4. behaviour is unchanged
billing = load("billing")
inv = billing.Invoice(
    currency="us$",
    lines=(billing.Line("Paperback", 19.99, 2, "books"), billing.Line("Tote bag", 12.50, 1, "merch")),
)
expected = {"currency": "USD", "subtotal_minor": 5248, "tax_minor": 388, "total_minor": 5636}
got = inv.totals()
if got != expected:
    fail(f"Invoice.totals() returned {got!r}, expected {expected!r}")
report = load("billing.report")
last = report.render(inv).splitlines()[-1]
if last != "total 56.36 USD":
    fail(f"billing.report.render(...) last line is {last!r}, expected 'total 56.36 USD'")
if billing.normalize_currency(" euro ") != "EUR":
    fail("billing.normalize_currency is no longer re-exported from the package root")
CHECK
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

if [ ! -f tests/test_billing.py ]; then
  echo "FAIL: tests/test_billing.py is missing -- it was supposed to be updated, not deleted"
  exit 1
fi
out=$("$PY" -m unittest tests.test_billing 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|FAILED|ERROR)' | head -1)
  echo "FAIL: tests/test_billing.py does not pass after the refactor (${reason:-see unittest output})"
  exit 1
fi
ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)
if [ "${ran:-0}" -lt 8 ]; then
  echo "FAIL: expected the 8 existing tests in tests/test_billing.py to still run, unittest ran ${ran:-0}"
  exit 1
fi
exit 0
