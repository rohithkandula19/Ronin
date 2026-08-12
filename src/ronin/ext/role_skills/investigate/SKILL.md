---
name: investigate
description: Root-cause a failure from a stack trace — trace to source, form hypotheses, then hand a repro to the fixer subagent.
allowed-tools: [read, grep, glob, bash, task, todo_write]
adapted-from: gstack/investigate
license: MIT
---
# investigate — from stack trace to root cause

The goal is the *cause*, not the symptom. Changing code until the error message
changes is not fixing a bug; it is hiding one. Track the steps with `todo_write` so the
hypotheses you rule out stay recorded.

1. **Read the trace bottom-up.** The exception type and message say what went wrong; the
   deepest frame in *your* code (skip library frames) says where. Note the exact file,
   line, and the values named in the message.

2. **Land on the source.** `grep` for the error string and the failing symbol to reach
   the exact line, and `glob` / `read` to open the frames the trace names. Read the
   function that raised and its callers — the bad value usually enters upstream of where
   it finally blew up.

3. **Reproduce it.** With `bash`, run the failing test or command and see the trace with
   your own eyes. A bug you cannot reproduce, you cannot confirm you fixed. If there is
   no test that triggers it, write the smallest command or input that does — that repro
   is what makes the fix provable.

4. **Form hypotheses, then confirm by reading — not by editing.** Write down the two or
   three plausible causes. For each, `read` the code that would confirm or kill it
   (`bash` for a `git log -L` or `git blame` on the suspect line tells you what changed
   and when). Keep only the hypothesis the code actually supports; discard the rest out
   loud so the reasoning is visible.

5. **Hand the confirmed failure to the fixer.** Once you have a failing test or a
   reliable repro command and a located cause, spawn the built-in `fixer` subagent with
   the `task` tool — `task(subagent_type="fixer", prompt=...)`. Give it the exact command
   that fails, the trace, the file and line you believe is the cause, and your leading
   hypothesis. The `fixer` makes the smallest change that turns the test green and never
   edits the test to pass; it reports back what it changed or, if it cannot, the most
   likely remaining cause. Review its result against the root cause you found — a green
   test that fixed a different thing is not the fix.
