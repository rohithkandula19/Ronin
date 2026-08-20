---
name: design-review
description: Score a design or plan on five dimensions 0-10, say what a 10 looks like, then fix the weak ones.
allowed-tools: [read, grep, glob]
model: plan
adapted-from: gstack/design-review
license: MIT
---
# design-review — grade the plan, then raise its grade

Run this on a plan or design **before** it becomes code, best from plan mode (this
skill prefers the `plan` role — switch with `/model`). It is read-only: the output is
a scored critique and a revised plan, not edits to the tree. Use `read`, `grep`, and
`glob` only to check the design against the real code it will touch.

Score each dimension **0-10**. For every one: state the score, name the single most
important thing holding it back, and describe **what a 10 would look like** here in
concrete terms — a bar the revision can actually aim at.

1. **Correctness** — does the design produce the right result for the real inputs,
   including the edge and failure cases? *10: every input class is accounted for, and
   the one that would break it has a named answer.*

2. **Simplicity** — is this the least machinery that solves the problem? *10: nothing
   can be removed without losing a requirement; no speculative generality, no layer
   that exists only in case.*

3. **Testability** — can the important behaviour be proven by a test that fails when
   it regresses? *10: each claim maps to a test at the seam the change already has, with
   no new scaffolding invented to reach it.*

4. **Blast radius** — how much does this touch, and who feels it if it is wrong? *10:
   the change is contained behind one interface; a mistake is visible fast and hurts one
   caller, not the whole tree.*

5. **Reversibility** — how cheaply can it be undone? *10: one revert with no data
   migration to unwind and no consumer already depending on the new shape.*

Then **act on the scores**: rewrite the plan to lift the lowest dimensions toward their
10, calling out each change and the score it targets. If a dimension cannot reach a
passing bar, say so plainly and name the trade-off being accepted rather than papering
over it. End with the revised plan and hand off to `autoplan` or the builder.
