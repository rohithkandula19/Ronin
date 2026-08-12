---
name: autoplan
description: Survey the code, draft a written plan, and get confirmation before editing anything.
allowed-tools: [read, grep, glob, ls, todo_write]
adapted-from: gstack/plan
license: MIT
---
# autoplan — plan before you touch code

Run this before any change big enough that a wrong turn costs real rework. The
deliverable is a plan the user approves, not code. Do not edit a file while this
skill is driving.

1. **Frame the goal.** Restate the task in one sentence and name the definition of
   done — the observable thing that will be true when you are finished. If that
   sentence is vague or hides a choice, stop and load the `office-hours` skill first;
   an ambiguous goal only ever produces a confident wrong plan.

2. **Survey — do not guess.** Find the code the change touches before proposing how
   to change it.
   - `glob` for files by shape (`glob "src/**/*.py"`); `ls` to read a directory's layout.
   - `grep` for the symbols, call sites, error strings, and config keys involved — and
     for existing patterns you should imitate rather than invent.
   - `read` the two or three files the change actually lands in, plus their tests, so
     the plan matches how this codebase already does things.

3. **Write the plan.** Keep it to the smallest set of steps that reaches done:
   - the files you will edit and what changes in each,
   - the order things must land in (what depends on what),
   - the tests you will add or run to prove it works,
   - what you are deliberately NOT doing, and the single risk most likely to bite.

4. **Record the steps** with `todo_write` so the plan survives the work — it outlives
   compaction and lets the user watch the burn-down.

5. **Stop and confirm.** Present the plan and wait for an explicit go-ahead before the
   first edit. If the user redirects, revise the plan and re-confirm — do not start
   coding around a plan they have not agreed to.
