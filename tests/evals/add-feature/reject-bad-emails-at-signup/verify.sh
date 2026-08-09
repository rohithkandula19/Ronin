#!/usr/bin/env bash
# Verifier for add-feature/reject-bad-emails-at-signup.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/reject-bad-emails-at-signup): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/reject-bad-emails-at-signup): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "reject-bad-emails-at-signup"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

try:
    from signup.register import Store, register
except Exception as exc:
    fail(f"could not import signup.register: {type(exc).__name__}: {exc}")

# 1. valid addresses still work, and are still normalised
store = Store()
try:
    user = register(store, "  Ada@Example.COM  ", "  Ada  ")
except Exception as exc:
    fail(f"registering a valid address raised {type(exc).__name__}: {exc}")
if user["email"] != "ada@example.com" or user["name"] != "Ada":
    fail(f"a valid signup is no longer normalised the way it was: {user!r}")
if len(store) != 1:
    fail(f"a valid signup should be stored once, store holds {len(store)}")
register(store, "grace+tag@sub.example.org", "Grace")
if len(store) != 2:
    fail(f"a second valid signup was not stored, store holds {len(store)}")

BEFORE = store.all()

BAD = ["not-an-email", "ada@@example.com", "@example.com", "ada@", "", "a@b@c", "no-at-sign.com"]
for value in BAD:
    try:
        register(store, value, "Somebody")
    except ValueError as exc:
        expected = f"invalid email address: {value}"
        if str(exc) != expected:
            fail(f"rejecting {value!r} should say {expected!r}, said {str(exc)!r}")
    except Exception as exc:
        fail(f"rejecting {value!r} should raise ValueError, raised {type(exc).__name__}: {exc}")
    else:
        fail(f"{value!r} was accepted as an email address")
    if store.all() != BEFORE:
        fail(f"the store was modified while rejecting {value!r}: {store.all()!r}")

# 2. the raw value goes in the message, not the normalised one
raw = "  NOPE  "
try:
    register(store, raw, "Somebody")
except ValueError as exc:
    if str(exc) != f"invalid email address: {raw}":
        fail(
            "the message must quote the raw value as passed in: expected "
            f"{'invalid email address: ' + raw!r}, got {str(exc)!r}"
        )
except Exception as exc:
    fail(f"rejecting {raw!r} should raise ValueError, raised {type(exc).__name__}: {exc}")
else:
    fail(f"{raw!r} was accepted as an email address")

RONIN_VERIFY_EOF
