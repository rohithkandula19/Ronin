---
name: ship
description: Release behind verify gates — run the tests, read the diff, then a staged rollout with a rollback plan.
allowed-tools: [bash, read, grep, todo_write]
adapted-from: gstack/ship
license: MIT
---
# ship — release behind gates, not on hope

Getting code to green is not shipping it. This skill is the gate between "it works on
my machine" and "it is live," and every gate can stop the release. Lay the steps out
with `todo_write` first so a skipped gate is visible, then work them in order.

**Gate 1 — the tree is clean.** With `bash`: `git status` shows nothing unexpected, and
`git diff` / `git diff --staged` is read in full — you are signing off on exactly these
lines. An unrelated change riding along is a reason to stop.

**Gate 2 — verify.** Run the project's real checks, not a subset: the test suite, the
linter, the type checker, the build. Read failures; do not retry until green by luck.
If you cannot find the commands, `grep` the CI config, `Makefile`, or `pyproject.toml`
/ `package.json` and run what CI runs. A red gate ends the release here.

**Gate 3 — the release is minimal and described.** Confirm the change is the intended
one and nothing debug-only (a print, a hardcoded token, a disabled test) is in it.
Write the release note from the diff: what changed and why, in the terms a user or an
on-call engineer would search for.

**Gate 4 — stage the rollout.** Do not flip everything at once.
- Ship to the smallest audience first — canary, one instance, an internal flag.
- Name the signal you will watch (error rate, latency, a specific log line) and the
  threshold that means abort.
- Widen only after the canary is clean for a defined window.

**Gate 5 — rollback is ready before you need it.** State the exact revert — the command
or the flag flip — and confirm it needs no data migration to unwind. If a rollback is
not clean, that is a design problem to fix before shipping, not during the incident.

Ronin runs commands; it does not press your deploy button or watch your dashboards.
Be honest about that line: do the parts you can (`bash`, reading output) and hand the
human a precise checklist for the parts only they can see.
