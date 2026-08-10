#!/usr/bin/env bash
# Verifier for add-feature/add-a-priority-field-to-tasks.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/add-a-priority-field-to-tasks): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/add-a-priority-field-to-tasks): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "add-a-priority-field-to-tasks"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

try:
    from board.task import Task, from_dict, pending
except Exception as exc:
    fail(f"could not import board.task: {type(exc).__name__}: {exc}")

# 1. the default
try:
    default_task = Task("t3", "Ship the thing", False)
except TypeError as exc:
    fail(f"Task('t3', 'Ship the thing', False) no longer builds, so existing callers broke: {exc}")
if not hasattr(default_task, "priority"):
    fail("Task has no `priority` attribute after the change")
if default_task.priority != "normal":
    fail(f"priority should default to 'normal', got {default_task.priority!r}")

# 2. backward compatibility of the positional/short constructor calls
try:
    two_arg = Task("t4", "Later")
except TypeError as exc:
    fail(f"Task('t4', 'Later') no longer builds, so existing callers broke: {exc}")
if two_arg.done is not False or two_arg.priority != "normal":
    fail(
        "Task('t4', 'Later') should still be not-done with the default priority, got "
        f"done={two_arg.done!r} priority={two_arg.priority!r}"
    )

# 3. an explicit priority survives
try:
    high = Task("t5", "Page someone", False, "high")
except TypeError:
    try:
        high = Task("t5", "Page someone", False, priority="high")
    except TypeError as exc:
        fail(f"cannot set a priority on a Task: {exc}")
if high.priority != "high":
    fail(f"explicit priority 'high' did not stick, got {high.priority!r}")

# 4. to_dict carries it, without dropping the old keys
row = default_task.to_dict()
for key, value in (("id", "t3"), ("title", "Ship the thing"), ("done", False)):
    if key not in row:
        fail(f"to_dict() dropped the pre-existing key {key!r}: got {sorted(row)}")
    if row[key] != value:
        fail(f"to_dict()[{key!r}] should still be {value!r}, got {row[key]!r}")
if row.get("priority") != "normal":
    fail(f"to_dict() should include priority 'normal', got {row.get('priority')!r}")
if high.to_dict().get("priority") != "high":
    fail(f"to_dict() should report the set priority, got {high.to_dict().get('priority')!r}")

# 5. old rows on disk have no priority key at all
legacy = {"id": "t6", "title": "Written last month", "done": True}
try:
    restored = from_dict(legacy)
except Exception as exc:
    fail(
        "from_dict() must still read a stored row that has no priority key "
        f"(backward compatibility), but it raised {type(exc).__name__}: {exc}"
    )
if restored.priority != "normal":
    fail(f"a stored row without a priority key should load as 'normal', got {restored.priority!r}")
if restored.id != "t6" or restored.title != "Written last month" or restored.done is not True:
    fail(f"from_dict() mangled the old fields: {restored!r}")

# 6. round trip, and the untouched helper
new_row = {"id": "t7", "title": "Fresh", "done": False, "priority": "low"}
loaded = from_dict(new_row)
if loaded.priority != "low":
    fail(f"from_dict() ignored an explicit priority: got {loaded.priority!r}")
if from_dict(loaded.to_dict()) != loaded:
    fail("Task does not round-trip through to_dict()/from_dict() any more")
if [task.id for task in pending([Task("a", "A"), Task("b", "B", True), Task("c", "C")])] != ["a", "c"]:
    fail("pending() no longer returns the not-done tasks in order")

RONIN_VERIFY_EOF
