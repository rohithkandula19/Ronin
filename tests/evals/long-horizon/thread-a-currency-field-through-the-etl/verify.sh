#!/usr/bin/env bash
# Checks the currency field was threaded through every layer of the import
# pipeline. Prints one FAIL line naming the layer that is missing.
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" || { echo "FAIL [harness] cannot enter workspace $ROOT"; exit 1; }
PY="${PYTHON:-python3}"
TMP="$(mktemp -d "$ROOT/.verify_tmp.XXXXXX")" || { echo "FAIL [harness] cannot create temp dir"; exit 1; }
trap 'rm -rf "$TMP"' EXIT

"$PY" - "$TMP" <<'PYCODE'
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TMP = Path(sys.argv[1])
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

CURRENCIES = {"USD", "EUR", "GBP"}


def fail(layer: str, message: str) -> None:
    print(f"FAIL [{layer}] {message}")
    raise SystemExit(1)


# ---------------------------------------------------------------- config layer
try:
    from etl.config import Config

    config = Config.load(ROOT / "config" / "pipeline.json")
    allowlists = dict(config.section("allowlists"))
except Exception as exc:  # noqa: BLE001
    fail("config", f"config/pipeline.json is not loadable through etl.config.Config: {exc!r}")

matching = [
    name
    for name, values in allowlists.items()
    if isinstance(values, list) and {str(v).upper() for v in values} == CURRENCIES
]
if not matching:
    fail(
        "config",
        "no allowlist in config/pipeline.json holds exactly USD, EUR, GBP; "
        f"the file declares allowlists {sorted(allowlists)}",
    )

# ---------------------------------------------------------------- schema layer
try:
    from etl import schema
except Exception as exc:  # noqa: BLE001
    fail("schema", f"etl/schema.py does not import: {exc!r}")
if "currency" not in tuple(schema.FIELD_NAMES):
    fail(
        "schema",
        "'currency' is not a declared field: etl/schema.py FIELD_NAMES is "
        f"{list(schema.FIELD_NAMES)}",
    )

# ---------------------------------------------------------------- reader layer
try:
    from etl.readers import CsvOrderReader

    rows = list(CsvOrderReader(ROOT / "data" / "orders_sample.csv").read())
except Exception as exc:  # noqa: BLE001
    fail("reader", f"reading data/orders_sample.csv raised {exc!r}")
if not rows:
    fail("reader", "data/orders_sample.csv produced no rows")
first = rows[0][1]
if "currency" not in first:
    fail(
        "reader",
        "the CSV reader still drops the source column 'currency_code': the first "
        f"row arrived with fields {sorted(first)}",
    )
if first["currency"].strip().lower() != "usd":
    fail(
        "reader",
        "the CSV reader mapped the wrong source column onto 'currency': row 2 of "
        f"data/orders_sample.csv arrived as {first['currency']!r}, expected 'usd'",
    )

# ------------------------------------------------------------- transform layer
try:
    from etl.transforms import Normalize

    normalized = Normalize().apply({"currency": " eur ", "amount": "5"})
except Exception as exc:  # noqa: BLE001
    fail("transform", f"Normalize().apply raised {exc!r} on a record with a currency")
if normalized.get("currency") != "EUR":
    fail(
        "transform",
        "the normalize transform did not upper-case the currency: ' eur ' became "
        f"{normalized.get('currency')!r}, expected 'EUR'",
    )

# ------------------------------------------------------------ validation layer
BASE = {
    "order_id": "Z-9001",
    "customer_id": "C-1",
    "channel": "web",
    "amount": "10.00",
    "quantity": "1",
    "region": "emea",
    "currency": "USD",
}
try:
    from etl.validate import Validator

    validator = Validator(config)
    good = validator.check(2, dict(BASE))
    unknown = validator.check(3, dict(BASE, currency="XYZ"))
    absent = {k: v for k, v in BASE.items() if k != "currency"}
    blank = validator.check(4, absent)
except Exception as exc:  # noqa: BLE001
    fail("validation", f"the validator raised {exc!r} on a record carrying a currency")
if good:
    fail(
        "validation",
        "a well-formed row with currency 'USD' was rejected: "
        f"{[e.render() for e in good]}",
    )
if not any(e.field == "currency" for e in unknown):
    fail(
        "validation",
        "currency 'XYZ' was accepted: the currency field is not constrained by the "
        f"config allowlist (errors reported: {[e.render() for e in unknown] or 'none'})",
    )
if not any(e.field == "currency" for e in blank):
    fail(
        "validation",
        "a row with no currency at all was accepted: the field is not required in "
        f"etl/schema.py (errors reported: {[e.render() for e in blank] or 'none'})",
    )

# ---------------------------------------------------------------- writer layer
try:
    from etl.writers import JsonlWriter

    one = TMP / "one.jsonl"
    with JsonlWriter(one) as writer:
        writer.write(dict(BASE))
    keys = [key for key, _ in json.loads(one.read_text(encoding="utf-8").strip(),
                                         object_pairs_hook=lambda pairs: pairs)]
except Exception as exc:  # noqa: BLE001
    fail("writer", f"the jsonl writer raised {exc!r} on a record carrying a currency")
if "currency" not in keys:
    fail(
        "writer",
        f"the feed writer drops currency from its output: line keys are {keys}",
    )
if keys.index("currency") != keys.index("amount") + 1:
    fail(
        "writer",
        "currency is not written immediately after amount: line keys are "
        f"{keys}",
    )

# ------------------------------------------------------------------ end to end
out = TMP / "feed.jsonl"
run = subprocess.run(
    [sys.executable, "run_import.py", "--source", "data/orders_sample.csv", "--out", str(out)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    fail(
        "end-to-end",
        "run_import.py failed on data/orders_sample.csv with exit code "
        f"{run.returncode}: {(run.stderr or run.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(records) != 4:
    fail("end-to-end", f"expected 4 imported rows from the sample file, got {len(records)}")
got = [r.get("currency") for r in records]
if got != ["USD", "EUR", "GBP", "USD"]:
    fail(
        "end-to-end",
        f"imported currencies are {got}, expected ['USD', 'EUR', 'GBP', 'USD']",
    )

bad = TMP / "orders_mixed.csv"
bad.write_text(
    "order_id,cust,channel,amount_gross,qty,region,currency_code\n"
    "M-1,C-1,web,10.00,1,emea,usd\n"
    "M-2,C-2,web,10.00,1,emea,zzz\n"
    "M-3,C-3,web,10.00,1,emea,\n",
    encoding="utf-8",
)
mixed_out = TMP / "mixed.jsonl"
run = subprocess.run(
    [sys.executable, "run_import.py", "--source", str(bad), "--out", str(mixed_out)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    fail(
        "end-to-end",
        "a file with two unusable currencies aborted the run instead of rejecting "
        f"the rows: exit code {run.returncode}, stderr {run.stderr.strip()[-200:]!r}",
    )
mixed = [json.loads(line) for line in mixed_out.read_text(encoding="utf-8").splitlines() if line.strip()]
if [r["order_id"] for r in mixed] != ["M-1"]:
    fail(
        "end-to-end",
        "expected only M-1 to survive a file whose other rows have a bad and a "
        f"missing currency, got {[r['order_id'] for r in mixed]}",
    )
if "currency" not in run.stderr:
    fail(
        "end-to-end",
        "the rejected rows were not reported per field: run_import.py stderr never "
        f"mentions currency ({run.stderr.strip()[:200]!r})",
    )

# ------------------------------------------------------------------ regression
run = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    tail = [ln for ln in (run.stderr or "").splitlines() if ln.strip()][-1:]
    fail("regression", f"the project's own tests no longer pass: {tail or ['no output']}")

print("OK currency threaded through config, schema, reader, transform, validation and writer")
PYCODE
