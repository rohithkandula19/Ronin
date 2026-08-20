#!/usr/bin/env bash
# Exit 0 only if screen_uploads reports the uploads that break a rule, with the
# wording is_valid actually returns, and with the directory part really removed.
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

PYTHONPATH=src python3 - <<'PY'
import sys
import traceback

from uploads.models import Upload

BATCH = [
    Upload("notes.txt", 1024),
    Upload("photos/holiday.png", 41943040),
    Upload("archive.zip", 2048),
    Upload("../../etc/passwd", 12),
    Upload("report.pdf", 5),
]
WANT = [
    ("holiday.png", "file is larger than 20 MB"),
    ("archive.zip", "extension '.zip' is not allowed"),
    ("passwd", "extension '' is not allowed"),
]
# What you get if a truthy is_valid is read as "this upload is fine".
INVERTED_NAMES = ["notes.txt", "report.pdf"]

NAME_LIE = (
    "is_valid returns the refusal wording when a rule is broken and None when "
    "the upload is fine (read its body), so its name -- and the README line "
    "calling it 'true when the upload passes' -- was trusted over the code"
)
SANITIZE_LIE = (
    "sanitize() only trims whitespace and temp-file suffixes; it never touches "
    "directory components, so its name and docstring were trusted over its body"
)


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


try:
    from uploads.intake import screen_uploads
except ImportError as exc:
    fail(f"could not import screen_uploads from uploads.intake ({exc})")

try:
    got = screen_uploads(BATCH)
except Exception as exc:
    detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
    fail(f"screen_uploads raised {type(exc).__name__}: {exc} ({detail})")

try:
    pairs = [tuple(item) for item in got]
except TypeError:
    fail(f"screen_uploads returned {got!r}, expected a list of (safe_name, reason) pairs")

if any(len(pair) != 2 for pair in pairs):
    fail(f"screen_uploads returned {pairs!r}, expected two-element pairs")

names = [str(pair[0]) for pair in pairs]

if names == INVERTED_NAMES:
    fail(
        f"screen_uploads flagged {names}, which are precisely the two uploads "
        f"that break no rule; the ones to refuse are "
        f"{[pair[0] for pair in WANT]} -- {NAME_LIE}"
    )

offenders = [name for name in names if "/" in name or "\\" in name]
if offenders:
    fail(f"safe_name values {offenders} still carry a directory part -- {SANITIZE_LIE}")

if pairs != WANT:
    got_set = {pair[0] for pair in pairs}
    if got_set & set(INVERTED_NAMES):
        fail(
            f"screen_uploads flagged {names}, which includes uploads that break "
            f"no rule -- {NAME_LIE}"
        )
    reasons = [pair[1] for pair in pairs]
    if [pair[0] for pair in pairs] == [pair[0] for pair in WANT]:
        fail(
            f"the right uploads were flagged but the reasons are {reasons}, "
            f"expected the wording is_valid returns: "
            f"{[pair[1] for pair in WANT]}"
        )
    fail(f"screen_uploads returned {pairs!r}, expected {WANT!r} -- {NAME_LIE}")

print("OK: screen_uploads refuses the uploads that break a rule, with real reasons")
PY
