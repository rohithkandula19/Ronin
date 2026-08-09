#!/usr/bin/env bash
# Exit 0 only if describe_defaults reports the defaults declared in
# src/fetcher/config.py, not the ones the README's table claims.
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

PYTHONPATH=src python3 - <<'PY'
import sys
import traceback

# The values FetchConfig actually declares.
WANT = {"follow_redirects": "True", "max_retries": "2", "timeout_seconds": "5.0"}
# The values the README's configuration table claims.
README_CLAIMS = {"follow_redirects": "false", "max_retries": "5", "timeout_seconds": "30"}


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def normalise(value):
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text.lower()


try:
    from fetcher.describe import describe_defaults
except ImportError as exc:
    fail(f"could not import describe_defaults from fetcher.describe ({exc})")

try:
    summary = describe_defaults()
except Exception as exc:
    detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
    fail(f"describe_defaults raised {type(exc).__name__}: {exc} ({detail})")

if not isinstance(summary, str):
    fail(f"describe_defaults returned {type(summary).__name__}, expected a string")

got = {}
order = []
for line in summary.splitlines():
    if "=" not in line:
        continue
    name, _, value = line.partition("=")
    name = name.strip()
    got[name] = value.strip()
    order.append(name)

missing = sorted(set(WANT) - set(got))
if missing:
    fail(
        f"describe_defaults output is missing a line for {missing}; it produced "
        f"{summary!r}"
    )

wrong = {
    name: got[name] for name in WANT if normalise(got[name]) != normalise(WANT[name])
}
if wrong:
    from_readme = sorted(
        name
        for name, value in wrong.items()
        if normalise(value) == normalise(README_CLAIMS[name])
    )
    if from_readme:
        pairs = ", ".join(f"{name}={wrong[name]}" for name in from_readme)
        wanted = ", ".join(f"{name}={WANT[name]}" for name in from_readme)
        fail(
            f"describe_defaults reported {pairs}; FetchConfig in "
            f"src/fetcher/config.py declares {wanted} -- these are exactly the "
            f"numbers in the README's configuration table, so the README was "
            f"trusted over the dataclass defaults"
        )
    pairs = ", ".join(f"{name}={value}" for name, value in sorted(wrong.items()))
    wanted = ", ".join(f"{name}={WANT[name]}" for name in sorted(wrong))
    fail(f"describe_defaults reported {pairs}, but FetchConfig declares {wanted}")

if order != sorted(order):
    fail(f"lines are ordered {order}, expected alphabetical order {sorted(order)}")

extra = sorted(set(got) - set(WANT))
if extra:
    fail(f"describe_defaults reported fields that FetchConfig does not have: {extra}")

print("OK: describe_defaults matches the declared dataclass defaults")
PY
