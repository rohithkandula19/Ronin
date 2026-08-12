---
name: review
description: Security and correctness review over a diff, delegating a structured pass to the reviewer subagent.
allowed-tools: [bash, read, grep, glob, task]
adapted-from: gstack/review
license: MIT
---
# review — security and correctness over a diff

Run this after a change and before declaring it done. It reads; it does not fix.
Fixing is the caller's job, and a review that quietly edited would leave you not
knowing what changed.

1. **Get the diff.** Identify the target — a diff range, a branch, or the uncommitted
   working tree. With `bash`, run `git diff` (working tree), `git diff --staged`, or
   `git diff main...HEAD` (a branch) to see exactly what changed. Read the full diff,
   not a summary; a review of a summary reviews the summary.

2. **Inspect around the change.** The diff shows what moved, not what it broke. Use
   `grep` to find every caller of a changed function or signature, and `read` the files
   on both sides of each new seam so you judge the change in context, not in isolation.

3. **Delegate a structured pass.** Spawn the built-in `reviewer` subagent with the
   `task` tool — `task(subagent_type="reviewer", prompt=...)`. Give it the diff range
   and the files, and state that it must return findings grouped BLOCKER / MAJOR /
   MINOR / NOTE, each starting with `file:line` and naming the consequence. Its context
   is separate, so the file dumps cost it, not you, and you get back only the verdict.

4. **Check the security surface yourself**, because it is where a quiet miss is worst:
   - untrusted input reaching a shell, a query, a path, or an eval,
   - authz/authn checks skipped, weakened, or moved after the effect,
   - secrets, tokens, or keys added to code, logs, or fixtures,
   - a new dependency or network call, and where its input is trusted from,
   - resource paths that could escape their root, and error text that leaks internals.

5. **Confirm the tests move.** A change with no test that fails without it is unproven;
   say so. If a test was weakened to pass, that is a BLOCKER.

6. **Report, do not fix.** Merge your findings with the subagent's into one list ordered
   by severity, each with `file:line` and the concrete consequence. If nothing rises
   above MINOR, say so plainly — a manufactured BLOCKER trains the caller to ignore you.
   Hand the list back for the author to act on.
