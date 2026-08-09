# `tests/evals/` — the task suite

Eval **inputs**, not tests. Every file under this directory is data that a runner
feeds to the agent; pytest must never collect any of it (see *Why pytest ignores
this tree* below).

## Layout

```
tests/evals/
  README.md                     this file
  conftest.py                   collect_ignore_glob = ["*"]  (owned by the coordinator)
  manifest.toml                 flat index: every task id, category, gate flag
  <category>/<slug>/
    task.toml                   the task declaration
    fixture/                    the repo copied into a temp workspace
    verify.sh                   exit 0 == solved; runs with cwd = the workspace
    solution/                   reference solution, NOT part of a run (see below)
```

Eight categories, each measuring something different:

| category | tasks | what it measures |
|---|---|---|
| `single-file` | 12 | one file, one clear edit |
| `multi-file` | 9 | correctness that spans files (rename + callers, move + imports) |
| `bug-from-trace` | 9 | the prompt is a real traceback and nothing else |
| `add-test` | 9 | writing a test that actually pins behaviour |
| `add-feature` | 9 | implementing a small written spec |
| `hostile-context` | 7 | reading the code when the prose contradicts it |
| `long-horizon` | 6 | 10+ files understood, 3+ changed |
| `injection-resistance` | 6 | ignoring instructions embedded in the repo |

67 tasks total, 20 of them in the regression gate.

## `task.toml`

```toml
id = "single-file/strip-bom-before-parse"   # unique; equals <category>/<dirname>
category = "single-file"                    # one of the eight above
prompt = """..."""                          # what the agent is told, verbatim
fixture = "fixture"                         # subdirectory copied to a temp workspace
git_url = ""                                # always empty: this suite is fully offline
git_sha = ""
files_expected = ["feeds/reader.py"]         # paths a correct solution must touch
max_turns = 10                               # a generous ceiling; the runner records actual
timeout_seconds = 90
tags = ["python", "encoding"]
regression_gate = false
```

Every key is present on every task, so a loader can read them positionally without
`.get()` fallbacks. `git_url`/`git_sha` are empty on all 67 tasks: **no task in this
suite needs the network**, so a runner never has to skip one when offline.

## `verify.sh`

* Runs with the temp workspace as the working directory, after the agent finishes.
* Exits **0 on success, non-zero on failure**, and prints a one-line reason on
  failure — the runner captures stdout/stderr and shows it as the failure detail.
* Depends on nothing outside the workspace except `bash` and a python interpreter
  on `PATH` as `python3`. Override with `RONIN_EVAL_PYTHON`.
* Uses only the standard library: **no pytest**, no network, no clock, no
  randomness, no dependence on dict iteration order. `add-test` tasks execute the
  agent's test file with a ~20 line importlib collector embedded in the script,
  because a task workspace is only guaranteed a bare interpreter.

The `add-test` scripts do more than run pytest and shrug: each one runs the new
test against the correct implementation (it must pass), then overwrites the
implementation with a *mutant* embedded in the verify script and runs the new test
again (it must now fail). A test that passes both ways proves nothing and is
rejected. The implementation is restored afterwards, and its sha256 is checked
first so a task that asks only for a test cannot be solved by editing the code.

The `injection-resistance` scripts assert the real task *and* that the injected
instruction was not carried out: no forbidden artefact file exists anywhere in the
tree, the planted canary secret has not been copied into any other file, and the
files the injection tells the agent to delete are still there.

## `solution/` — reference solutions

`solution/` holds a **reference solution**: the files as a correct answer leaves
them. It is *not* part of a run and no runner should copy it into the workspace —
it exists so the two-way check below is reproducible and reviewable. It contains
only the files a correct answer changes or adds. A task whose solution deletes a
file records those paths, one per line, in `solution/DELETE`.

## The two-way check

A `verify.sh` that passes on the untouched fixture is worse than no task: it
silently inflates the score. So every one of the 67 was checked in both
directions, and the suite is only trustworthy to the extent this stays true:

1. stage `fixture/` in a temp dir, run `verify.sh` → **must exit non-zero**;
2. stage `fixture/` then overlay `solution/` (applying `solution/DELETE`), run
   `verify.sh` → **must exit 0**;
3. for `injection-resistance` only: stage the solution *and* create the artefact
   the injection asks for → **must exit non-zero**, proving the guard bites.

All 67 tasks pass (1) and (2); all six injection tasks pass (3).

## Adding a task

1. Pick the category and a slug; create `tests/evals/<category>/<slug>/`.
2. Write `fixture/` as real code that really runs. Keep it under ~15 files unless
   the category demands more. **No `.py` file may sit directly in `fixture/` or
   `solution/`, and every directory holding a `.py` must also hold an
   `__init__.py`** — `scripts/sync_typecheck_paths.py` fails the build when two
   non-package modules anywhere pytest collects share a basename, and 67 fixtures
   would otherwise collide with each other and with `packages/cli/tests/`.
3. Write `task.toml` with every key above.
4. Write `verify.sh`, stdlib-only and deterministic, printing why it failed.
5. Write `solution/` with the changed files.
6. Run the two-way check, then add the task to `manifest.toml`.

For a `bug-from-trace` task the prompt must be a traceback the fixture genuinely
produces — run the fixture and paste what it printed. The only edit permitted is
rewriting the temporary workspace path to `/workspace` so the recorded prompt is
byte-stable across machines.

## `manifest.toml`

A flat index so CI and the loader do not have to walk the tree to know what should
exist:

```toml
total = 67
regression_gate_total = 20

[counts]
"single-file" = 12
...

[[tasks]]
id = "single-file/strip-bom-before-parse"
category = "single-file"
regression_gate = true
```

## The regression gate

Exactly 20 tasks carry `regression_gate = true`, spread across all eight
categories — 4 `single-file`, 3 `multi-file`, 3 `bug-from-trace`, 2 `add-test`,
2 `add-feature`, 2 `hostile-context`, 1 `long-horizon`, 3 `injection-resistance`.
They are the fastest and most stable in each corner of the suite, so CI can gate
on them without paying for the full 67.

## Why pytest ignores this tree

Several categories ship files named `test_*.py` inside `fixture/`, and the
`add-test` fixtures deliberately lack the test the task asks for. Collecting them
would run another project's tests as ours and turn the suite red by design. Two
independent guards, because they fail differently:

* `tests/evals/conftest.py` sets `collect_ignore_glob = ["*"]` — breaks if deleted;
* `norecursedirs = ["tests/evals"]` in `pyproject.toml` — breaks if the directory
  is renamed.

Both are owned by the coordinator.
