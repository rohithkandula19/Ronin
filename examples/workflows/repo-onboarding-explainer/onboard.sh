#!/bin/sh
# Point Ronin at an unfamiliar repository and get an orientation you can check.
#
#   ./onboard.sh /path/to/some/repo            # to stdout
#   ./onboard.sh /path/to/some/repo out.md     # and to a file
#
# Read-only by construction: --mode plan removes write, edit, multi_edit, bash and
# bash_output from the registry the model is shown. It is not told to be careful; it
# has no tool that could change anything. That matters for a repository you do not own.
#
# See README.md in this directory for what it costs and what can go wrong.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PROMPT_FILE="$HERE/onboard.md"

TARGET="${1:-.}"
OUT="${2:-}"

MAX_USD="${RONIN_ONBOARD_MAX_USD:-1.00}"
MAX_TURNS="${RONIN_ONBOARD_MAX_TURNS:-40}"
MAX_SECONDS="${RONIN_ONBOARD_MAX_SECONDS:-600}"

if [ ! -d "$TARGET" ]; then
    printf 'onboard: %s is not a directory\n' "$TARGET" >&2
    exit 1
fi
if [ ! -f "$PROMPT_FILE" ]; then
    printf 'onboard: cannot find %s\n' "$PROMPT_FILE" >&2
    exit 1
fi

# --cwd is how Ronin is pointed at another tree; it also becomes the boundary the file
# tools refuse to leave. --no-wizard because a first run in someone else's repository
# must not offer to create files in it.
set +e
ANSWER=$(python -m ronin -p "$(cat "$PROMPT_FILE")" \
    --cwd "$TARGET" \
    --output-format text \
    --mode plan \
    --max-turns "$MAX_TURNS" \
    --max-usd "$MAX_USD" \
    --max-seconds "$MAX_SECONDS" \
    --no-wizard \
    --no-record \
    --no-mcp)
CODE=$?
set -e

# 0 done · 1 error · 2 an approval was requested. In plan mode nothing mutating is in
# the registry, so a partial answer is still worth printing — which is why the text is
# emitted before the code is checked.
if [ -n "$ANSWER" ]; then
    if [ -n "$OUT" ]; then
        printf '%s\n' "$ANSWER" >"$OUT"
        printf 'wrote %s\n' "$OUT" >&2
    else
        printf '%s\n' "$ANSWER"
    fi
fi

if [ "$CODE" -ne 0 ]; then
    printf 'onboard: ronin exited %s; the orientation above may be incomplete.\n' "$CODE" >&2
    printf 'onboard: run `python -m ronin doctor --cwd %s` to see why.\n' "$TARGET" >&2
fi

exit "$CODE"
