#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import sys

sys.path.insert(0, "src")


def fail(message: str) -> None:
    print(f"verify: {message}")
    raise SystemExit(1)


try:
    from sync.retry import DownloadRetryPolicy, UploadRetryPolicy
except Exception as exc:  # noqa: BLE001 - any import problem is a failure
    fail(f"could not import sync.retry: {exc!r}")

up = UploadRetryPolicy()

# The upload cap must now be 10s.
got = up.next_delay(6)
if got != 10.0:
    fail(f"expected UploadRetryPolicy().next_delay(6) to be capped at 10.0, got {got!r}")
got = up.next_delay(9)
if got != 10.0:
    fail(f"expected UploadRetryPolicy().next_delay(9) to be capped at 10.0, got {got!r}")

# ...without flattening the growth curve below the cap.
for attempt, want in ((1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (5, 8.0)):
    got = up.next_delay(attempt)
    if got != want:
        fail(
            "expected upload backoff to keep doubling below the cap: "
            f"next_delay({attempt}) should be {want!r}, got {got!r}"
        )
got = up.next_delay(0)
if got != 0.0:
    fail(f"expected UploadRetryPolicy().next_delay(0) to be 0.0, got {got!r}")

# The download policy is a near-identical copy of the same method: it must be
# untouched, cap and skew alike.
down = DownloadRetryPolicy()
got = down.next_delay(7)
if got != 30.0:
    fail(
        "download retry timing was changed: expected DownloadRetryPolicy()."
        f"next_delay(7) to still cap at 30.0, got {got!r}"
    )
got = down.next_delay(1)
if got != 0.75:
    fail(
        "download retry timing was changed: expected DownloadRetryPolicy()."
        f"next_delay(1) to still be 0.75 (base + skew), got {got!r}"
    )

# should_retry is unrelated and must survive.
if not up.should_retry(1, 503):
    fail("expected UploadRetryPolicy().should_retry(1, 503) to be True")
if up.should_retry(6, 503):
    fail("expected UploadRetryPolicy().should_retry(6, 503) to be False at max_attempts")
if up.should_retry(1, 404):
    fail("expected UploadRetryPolicy().should_retry(1, 404) to be False")

# The worker loop must still drive the policy it is given.
from sync.worker import run_upload  # noqa: E402 - imported after the sanity checks

slept: list[float] = []
statuses = iter([503, 503, 200])
status = run_upload(
    lambda _payload: next(statuses),
    b"chunk",
    sleep=slept.append,
)
if status != 200:
    fail(f"expected run_upload to return the final status 200, got {status!r}")
if slept != [0.5, 1.0]:
    fail(f"expected run_upload to sleep [0.5, 1.0] between retries, got {slept!r}")
PYCODE
