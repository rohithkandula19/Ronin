#!/usr/bin/env bash
# Verifier for add-feature/limit-the-note-search-results.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/limit-the-note-search-results): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/limit-the-note-search-results): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "limit-the-note-search-results"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

try:
    from notebook.search import search_notes
except Exception as exc:
    fail(f"could not import notebook.search: {type(exc).__name__}: {exc}")

# Twelve hits, handed over out of order: more than any plausible default page
# size, so "no limit means everything" is actually tested.
ORDER = [f"note-{index:02d}" for index in range(12)]
NOTES = [{"title": title, "body": "the db pool again"} for title in reversed(ORDER)]
NOTES += [{"title": "readme", "body": "nothing to see"}, {"title": "scratch", "body": "empty"}]


def titles(result):
    if not isinstance(result, list):
        fail(f"search_notes should return a list, got a {type(result).__name__}")
    try:
        return [note["title"] for note in result]
    except Exception as exc:
        fail(f"search_notes returned something that is not a list of notes: {exc}")


# 1. unchanged behaviour when nobody asks for a page
if titles(search_notes(NOTES, "db")) != ORDER:
    fail(
        "search_notes(notes, 'db') with no paging arguments changed: expected "
        f"{ORDER}, got {titles(search_notes(NOTES, 'db'))}"
    )

# 2. limit
try:
    first_page = search_notes(NOTES, "db", limit=2)
except TypeError as exc:
    fail(f"search_notes does not accept a `limit` keyword: {exc}")
if titles(first_page) != ORDER[:2]:
    fail(f"limit=2 should give the first two hits {ORDER[:2]}, got {titles(first_page)}")

# 3. limit + skip
try:
    second_page = search_notes(NOTES, "db", limit=2, skip=2)
except TypeError as exc:
    fail(f"search_notes does not accept a `skip` keyword alongside `limit`: {exc}")
if titles(second_page) != ORDER[2:4]:
    fail(f"limit=2, skip=2 should give {ORDER[2:4]}, got {titles(second_page)}")

# 4. skip on its own, and a limit past the end
if titles(search_notes(NOTES, "db", skip=3)) != ORDER[3:]:
    fail(f"skip=3 should give {ORDER[3:]}, got {titles(search_notes(NOTES, 'db', skip=3))}")
if titles(search_notes(NOTES, "db", limit=99)) != ORDER:
    fail(f"a limit larger than the result count should give every hit, got {titles(search_notes(NOTES, 'db', limit=99))}")

# 5. paging past the end is empty, not an error
try:
    beyond = search_notes(NOTES, "db", limit=2, skip=99)
except Exception as exc:
    fail(f"skipping past the end of the results raised {type(exc).__name__}: {exc}")
if titles(beyond) != []:
    fail(f"skip past the end should give an empty list, got {titles(beyond)}")

# 6. the whole result set is still reachable page by page
paged = []
for offset in range(0, len(ORDER), 5):
    paged.extend(titles(search_notes(NOTES, "db", limit=5, skip=offset)))
if paged != ORDER:
    fail(f"walking the pages two at a time should visit every hit once, got {paged}")

RONIN_VERIFY_EOF
