---
name: eng-review
description: Engineering review of a plan or change on six dimensions 0-10 with what a 10 looks like, then repair.
allowed-tools: [read, grep, glob, bash]
model: plan
adapted-from: gstack/eng-review
license: MIT
---
# eng-review — the engineering pass

Pairs with `design-review`: that one judges whether the plan is the right shape, this
one judges whether it is built like production. Run it on a concrete plan or a diff,
from the `plan` role (switch with `/model`). Read-only — `read`, `grep`, and `glob` to
inspect, and `bash` for non-mutating facts (`git diff`, `git log`, a dry-run build).
The output is a scored critique and the fixes it implies, not edits.

Score each dimension **0-10**: give the score, the one thing capping it, and **what a
10 looks like** in concrete terms for this change.

1. **Correctness & edge cases** — right answer on the boundary inputs, not just the
   happy path. *10: off-by-ones, empty inputs, and concurrency are each handled or
   explicitly ruled out.*

2. **Error handling** — what happens when a dependency fails. *10: every failure has a
   path that surfaces a usable message and leaves state consistent; nothing is swallowed.*

3. **Interfaces & naming** — the seam other code will bind to. *10: names say what they
   mean, the contract is minimal and hard to misuse, and callers cannot reach past it.*

4. **Performance & resources** — cost in time, memory, I/O, and handles. *10: no
   accidental N+1 or unbounded growth; the hot path is known and acceptable; resources
   are released.*

5. **Testability & coverage** — is the behaviour actually pinned? *10: the change ships
   with tests that fail before it and pass after, at the level where regressions occur.*

6. **Maintainability** — what the next reader pays. *10: it matches the surrounding
   style, duplicates nothing that already exists, and needs no comment to be understood.*

Then **repair**: list concrete fixes to lift the lowest dimensions toward their 10, each
tied to the score it raises and the file it lands in. Separate blockers (ship-stoppers)
from improvements (worth doing, not gating). Where a low score is an accepted trade-off,
say so and name what is being traded. Finish with the corrected plan or a change list
the builder can apply.
