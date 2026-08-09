A CI job on this repository failed. The failing job's log — its last 50KB, which is
where failures live — is at `.ronin/tmp/ci-failure.log`.

Work in this order and do not skip a step:

1. **Read the log.** Find the actual failure, not the first red line. A test that
   fails because an earlier fixture blew up is not the bug.
2. **Reproduce it locally.** Run the narrowest command that shows the failure — one
   test, not the suite. Say the command you ran and paste the failure. If you cannot
   reproduce it, stop and say so: a fix for a failure you never saw is a guess, and
   the most likely explanations are a flaky test, an environment difference, or a
   dependency that moved. Name which you think it is.
3. **Form one hypothesis** about the cause and say it before you change anything.
4. **Make the smallest change that tests the hypothesis**, and rerun the narrow
   command.
5. **Then run the wider suite** for the area you touched, to check you did not trade
   one failure for another.

Hard rules:

* **Never edit a test to make it pass.** Never delete, skip or xfail one. If the test
  is wrong, that is a finding, not a licence — say so and stop.
* **Do not use git.** No branch, no commit, no push, no checkout, no stash. Leave your
  changes in the working tree; the CI job that invoked you does the git work and opens
  a pull request. A commit from you would make the workflow think nothing changed.
* **Do not touch CI config, dependency pins or lockfiles** to make the failure go
  away. Loosening a pin is how a real incompatibility becomes a mystery three weeks
  later. If the fix genuinely is a version bump, say so and stop.
* **Do not write anything under `.ronin/`.** That is the harness's directory.

Finish with a summary in this shape, because the pull-request reviewer reads it before
the diff:

```
CAUSE: one sentence on why it failed.
FIX: what you changed and why that addresses the cause.
VERIFIED: the exact command you ran, and its result.
RISK: what this change could break that the suite does not cover.
```

If you did not fix it, say that plainly as the first line, then give CAUSE (your best
remaining hypothesis) and what you ruled out. A precise "not fixed, and here is why" is
worth more than a change that makes the error message different.
