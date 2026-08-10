#!/usr/bin/env bash
# Checks the note quota is enforced in the layer that owns rules, is configurable,
# and is translated to a status code by the web layer. One FAIL line, naming the
# layer at fault.
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

LIMIT_KEY = "max_active_notes_per_user"
CONFIG = ROOT / "config" / "app.json"


def fail(layer: str, message: str) -> None:
    print(f"FAIL [{layer}] {message}")
    raise SystemExit(1)


# ---------------------------------------------------------------- config layer
raw = json.loads(CONFIG.read_text(encoding="utf-8"))
limits = raw.get("limits") or {}
if limits.get(LIMIT_KEY) != 5:
    fail(
        "config",
        f"config/app.json has no limits.{LIMIT_KEY} of 5; its limits are {limits}",
    )
try:
    from notedeck.config import Settings

    settings = Settings.load(CONFIG)
except Exception as exc:  # noqa: BLE001
    fail("config", f"notedeck/config.py failed to load config/app.json: {exc!r}")
if getattr(settings, LIMIT_KEY, None) != 5:
    fail(
        "config",
        f"Settings does not expose {LIMIT_KEY} from the config file (got "
        f"{getattr(settings, LIMIT_KEY, '<missing>')!r}); notedeck/config.py is the only "
        "reader of that file",
    )

try:
    from notedeck.errors import NotedeckError
    from notedeck.util.clock import FixedClock
    from notedeck.web.error_map import code_for, status_for
    from notedeck.wiring import build_app, build_services
    from notedeck.web.request import Request
except Exception as exc:  # noqa: BLE001
    fail("imports", f"the project no longer imports: {exc!r}")

CLOCK = FixedClock("2024-01-01T00:00:00")


def fresh(local_settings=settings):
    services = build_services(local_settings, CLOCK)
    services.users.create(email="ada@example.com", display_name="Ada")
    return services


# --------------------------------------------------------------- service layer
services = fresh()
for index in range(5):
    try:
        services.notes.create("user-1", f"note {index}", "body")
    except Exception as exc:  # noqa: BLE001
        fail(
            "service",
            f"creating note {index + 1} of the allowed 5 failed with "
            f"{type(exc).__name__}: {exc}",
        )
try:
    services.notes.create("user-1", "one too many", "body")
except Exception as exc:  # noqa: BLE001
    quota_error: Exception | None = exc
else:
    quota_error = None

if quota_error is None:
    fail(
        "service",
        "a 6th un-archived note was accepted by NotesService.create, so the cap is not "
        "enforced where the rules live (the seeder and the importer never touch a route)",
    )
if not isinstance(quota_error, NotedeckError):
    fail(
        "errors",
        f"exceeding the cap raised {type(quota_error).__name__}, which the web layer "
        "cannot translate; it has to be a NotedeckError from notedeck/errors.py",
    )

# ------------------------------------------------------------------- web layer
status = status_for(quota_error)
if status != 429:
    fail(
        "web",
        f"notedeck/web/error_map.py maps the quota error to {status}, expected 429 "
        "(the table matches the first entry an error is an instance of)",
    )
code = code_for(quota_error)
if code != "quota_exceeded":
    fail(
        "web",
        f"the error code clients see is {code!r}, expected 'quota_exceeded'",
    )

# ------------------------------------------------- service layer, the fine print
services = fresh()
notes = [services.notes.create("user-1", f"note {i}", "body") for i in range(5)]
services.notes.archive(notes[0].id, "user-1")
services.notes.archive(notes[1].id, "user-1")
try:
    services.notes.create("user-1", "after archiving", "body")
    services.notes.create("user-1", "after archiving again", "body")
except Exception as exc:  # noqa: BLE001
    fail(
        "service",
        "archiving two notes did not free two slots: creating again raised "
        f"{type(exc).__name__}: {exc}",
    )

services = fresh()
for index in range(5):
    services.notes.create("user-1", f"note {index}", "body")
services.users.create(email="grace@example.com", display_name="Grace")
try:
    services.notes.create("user-2", "first note", "body")
except Exception as exc:  # noqa: BLE001
    fail(
        "service",
        "the cap is being counted across all users, not per user: a second user's first "
        f"note raised {type(exc).__name__}: {exc}",
    )

# ------------------------------------------------------------ repository layer
try:
    from notedeck.domain import Note
    from notedeck.repository import MemoryStore, NotesRepo

    repo = NotesRepo(MemoryStore())
    for _ in range(9):
        repo.add(
            Note(
                id=repo.next_id(),
                user_id="user-1",
                title="t",
                body="b",
                created_at="2024-01-01T00:00:00",
            )
        )
except Exception as exc:  # noqa: BLE001
    fail(
        "repository",
        f"NotesRepo refused the notes it was handed ({type(exc).__name__}: {exc}); the "
        "repository is storage and must stay free of policy, or fixtures and migrations "
        "cannot use it",
    )
if repo.count_active_for_user("user-1") != 9:
    fail(
        "repository",
        f"the repository stored {repo.count_active_for_user('user-1')} of 9 notes handed "
        "to it directly; it must store what it is given",
    )

# -------------------------------------------------- config layer, configurable
tuned = json.loads(CONFIG.read_text(encoding="utf-8"))
tuned.setdefault("limits", {})[LIMIT_KEY] = 2
tuned_path = TMP / "app_tuned.json"
tuned_path.write_text(json.dumps(tuned), encoding="utf-8")
services = fresh(Settings.load(tuned_path))
services.notes.create("user-1", "one", "body")
services.notes.create("user-1", "two", "body")
try:
    services.notes.create("user-1", "three", "body")
except Exception:  # noqa: BLE001
    pass
else:
    fail(
        "config",
        f"the cap does not follow limits.{LIMIT_KEY}: with the limit set to 2 a third "
        "note was still accepted, so the number is hardcoded somewhere",
    )

# ------------------------------------------------------------------ end to end
script = [{"method": "POST", "path": "/users", "body": {"email": "ada@example.com"}}]
script += [
    {"method": "POST", "path": "/users/user-1/notes", "body": {"title": f"note {i}"}}
    for i in range(6)
]
script_path = TMP / "requests.json"
script_path.write_text(json.dumps(script), encoding="utf-8")
run = subprocess.run(
    [
        sys.executable,
        "serve.py",
        "--requests",
        str(script_path),
        "--now",
        "2024-01-01T00:00:00",
    ],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if run.returncode != 0:
    fail(
        "end-to-end",
        f"serve.py exited {run.returncode} while replaying six note creations: "
        f"{(run.stderr or run.stdout).strip().splitlines()[-1:] or ['no output']}",
    )
lines = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
statuses = [entry["status"] for entry in lines]
if statuses[:6] != [201, 201, 201, 201, 201, 201]:
    fail("end-to-end", f"the first five creations did not all succeed: statuses were {statuses}")
last = lines[-1]
if last["status"] != 429 or last["body"].get("error") != "quota_exceeded":
    fail(
        "end-to-end",
        "the sixth creation over the API returned "
        f"{last['status']} {last['body'].get('error')!r}, expected 429 'quota_exceeded'",
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

seed = subprocess.run(
    [sys.executable, "scripts/seed.py", "--out", str(TMP / "seeded.json")],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)
if seed.returncode != 0:
    fail(
        "regression",
        f"scripts/seed.py now fails ({seed.returncode}): "
        f"{(seed.stderr or seed.stdout).strip().splitlines()[-1:] or ['no output']}",
    )

print("OK quota enforced in the service layer, configurable, and mapped to 429 by the web layer")
PYCODE
