#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import sys
import typing

sys.path.insert(0, "src")


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


try:
    from billing.tiers import RESOLVERS, overage_cents, tier_for
except Exception as exc:  # noqa: BLE001
    fail(f"could not import billing.tiers: {exc!r}")

# 1. The new rung, and every old one, at its exact boundary.
ladder = [
    (0, "free"),
    (999, "free"),
    (1_000, "starter"),
    (49_999, "starter"),
    (50_000, "team"),
    (199_999, "team"),
    (200_000, "pro"),
    (499_999, "pro"),
    (500_000, "scale"),
    (12_000_000, "scale"),
]
for requests, want in ladder:
    got = tier_for(requests)
    if got != want:
        fail(f"expected tier_for({requests:_}) to be {want!r}, got {got!r}")

try:
    tier_for(-1)
except ValueError:
    pass
else:
    fail("expected tier_for(-1) to raise ValueError")

# 2. Overage pricing follows the new rung.
for requests, want in ((500, "0"), (2_000, "40"), (60_000, "30"), (300_000, "25"), (900_000, "12")):
    got = overage_cents(requests)
    if got != want:
        fail(f"expected overage_cents({requests:_}) to be {want!r}, got {got!r}")

# 3. tier_for is on the metering hot path: the cache must still be there.
if not hasattr(tier_for, "cache_info"):
    fail("tier_for lost its functools.lru_cache decorator - no cache_info() on it any more")
before = tier_for.cache_info().hits
tier_for(4_242_424)
tier_for(4_242_424)
after = tier_for.cache_info().hits
if after <= before:
    fail(f"tier_for is no longer caching: hits went {before} -> {after} over a repeated call")

# 4. ...and it must still be registered for the admin console.
if RESOLVERS.get("tier") is not tier_for:
    fail(
        "tier_for lost its @resolver('tier') registration: "
        f"RESOLVERS['tier'] is {RESOLVERS.get('tier')!r}"
    )
if RESOLVERS.get("overage") is not overage_cents:
    fail("overage_cents lost its @resolver('overage') registration")

# 5. ...and keep its annotations, which the console's schema dump reads.
try:
    hints = typing.get_type_hints(tier_for)
except Exception as exc:  # noqa: BLE001
    fail(f"could not read tier_for's type hints: {exc!r}")
if hints != {"monthly_requests": int, "return": str}:
    fail(
        "expected tier_for to stay annotated (monthly_requests: int) -> str, "
        f"got {hints!r}"
    )
PYCODE
