# CI failure fixer

Given a failing CI run, reproduce the failure, fix it, and open a draft pull request.

> **Needs an API key and network.** `pip install "ronin[http]"` for real sockets, a
> provider key in a GitHub secret, and `GITHUB_TOKEN` (which Actions provides) for the
> PR. It also **writes to your repository** — on a branch, never on the default branch,
> and always as a draft PR. This is the one workflow here that mutates anything.

## files

| file | goes where | what it is |
|---|---|---|
| `ronin-fix.yml` | `.github/workflows/ronin-fix.yml` | the workflow |
| `prompt.md` | stays here; the workflow `cat`s it | the instruction the agent gets |
| `settings.json` | copied to `.ronin/settings.local.json` by the workflow | the permission rules for this run |
| `models.toml` | `.ronin/models.toml` (commit it) | provider config; holds no secrets |

Then add your provider key as a repository secret and make the two `secrets.*`
references in `ronin-fix.yml` match its name.

## running it

Actions → **ronin fix** → *Run workflow*, and paste the run id of the red build (it is
the number in that run's URL). `max_usd` defaults to `1.00`.

The trigger is `workflow_dispatch` **on purpose**. A `workflow_run` trigger that fires
an agent with `contents: write` on every red build is how you get a surprise branch at
3am from a flaky test, billed. The commented block at the bottom of the yaml is what to
switch to once you have watched it work several times.

## the exit code trap — read this before you debug anything

**In this build a headless run exits `2` whenever a gated tool was used at all, even
when a rule allowed it.** `ronin.core.loop` yields the `ApprovalRequest` event *before*
it consults the policy, because a UI has to render the prompt in order to ask;
`ronin.ui.headless.exit_code_for` then counts requests rather than refusals. So a
perfectly clean `--mode auto_edit` run that writes one file exits `2`, and the `result`
record lists that write under `approvals_denied` even though its `tool_end` shows
`"ok": true`.

Verified on this checkout, from the real event stream:

```json
{"type":"approval_request","tool_use_id":"t1","name":"write","danger_level":"mutating",…}
{"type":"tool_end","tool_use_id":"t1","name":"write","ok":true,"error":"",…}
{"type":"result","exit_code":2,"stop_reason":"no_tool_calls","errors":[],
 "approvals_denied":[{"tool_use_id":"t1","name":"write",…}]}
```

So this workflow does **not** trust the exit code. It derives the verdict from the
stream, which is unambiguous:

```sh
# a call that was really refused — the loop prefixes the tool result with "DENIED:"
jq -r 'select(.type=="tool_end" and (.error | startswith("DENIED:"))) | .tool_use_id' ronin.jsonl
# and the turn has to have finished, not run out of budget or iterations
jq -r 'select(.type=="result") | "\(.stop_reason) \(.errors|length)"' ronin.jsonl
```

`no_tool_calls` is the clean finish. `max_iterations`, `cost_budget`, `token_budget`,
`stalled` and `interrupted` all mean it ran out of something rather than finished.

If the exit code is fixed upstream, this workflow keeps working — a refusal is still a
`DENIED:` tool result — so the check is not a workaround with a shelf life.

## why `--mode auto_edit` and not `--yolo`

`auto_edit` relaxes an `ask` for anything at or below `mutating`, which is `write`,
`edit` and `multi_edit`. It does **not** relax `bash`, which is `destructive`. That is
fine here because the builtin rules already allow the commands a fixer needs —
`python`, `pytest`, `ruff`, `mypy`, `make`, `cargo`, `npm` and the read-only shell
utilities are all in `ronin.safety.policy.DEV_BINARIES` / `READ_ONLY_BINARIES`, and
`git status|diff|log|show|add|commit` are in `SAFE_GIT_SUBCOMMANDS`.

`--yolo` is absent deliberately. It is not "auto_edit but more": it waives the deny
list entirely, so `rm -rf`, a fork bomb and reading private key material stop being
refused. On a runner with a checkout and a token, that is a bad trade for saving a rule.

`settings.json` here **denies** rather than asks for the four things the agent must not
do: git history commands, `gh`, writes under `.ronin/`, and writes under `.github/`. A
deny returns a failed tool result explaining why, which the model can act on. An `ask`
would produce an `ApprovalRequest` and, in an unattended run, a refusal — same outcome,
worse message.

The `git commit` denial is the one that is easy to omit and expensive to omit: if the
agent commits its own fix, `git status --porcelain` is empty afterwards, the workflow
concludes nothing changed, and the fix is thrown away silently.

## what it costs

Per invocation, and the drivers are: how many turns the agent needs to reproduce the
failure, how long your test suite takes (every rerun is a `bash` call whose output goes
into the context), and the price of the model you routed `main` to.

No figure is quoted here. None has been measured on this machine, and a plausible number
in a cost table is worse than no number. Measure yours from the run's own output:

```sh
jq -r 'select(.type=="result") | "turns=\(.turns) tokens=\(.tokens) usd=\(.cost_usd)"' ronin.jsonl
```

`cost_usd` is computed from the prices *you* wrote in `models.toml`. Leave them at zero
and the run is recorded as unpriced and flagged — not as free.

Bound it before you trust it: `max_usd` is a workflow input, `--max-turns 30` and
`--max-seconds 900` are in the yaml, and `timeout-minutes: 25` is the backstop for all
of them.

## what could go wrong

| symptom | cause | what to do |
|---|---|---|
| "a tool call was refused" | the agent needed something the rules deny or ask about | the refusals are printed in the log with the reason; add one narrow allow rule, or do this fix by hand |
| "the turn did not finish cleanly (stop_reason=max_iterations)" | 30 turns was not enough, or the model is looping | read the stream artifact; a loop usually means the failure does not reproduce and the model is guessing |
| "ronin finished but changed nothing" | it could not reproduce the failure, or the fix really is a dependency bump — both of which `prompt.md` tells it to report rather than force | read the final `result.text`; this is a legitimate and useful outcome |
| `doctor` fails on the key check | the secret name in the yaml does not match `api_key_env` in `models.toml` | make them match; `doctor` runs before any spend precisely so this costs nothing |
| the PR contains unrelated churn | `git add -A` picks up anything the test run left behind (caches, `.pyc`, coverage files) | add those paths to `.gitignore`; the workflow already removes `.ronin/tmp` and its own `settings.local.json` |
| the fix passes CI but is wrong | it made the test pass, which is what it was asked to do | the PR is a **draft** for this reason. Check that the test would still fail without the fix |
| it edits the test | `prompt.md` forbids it, and a prompt is not a mechanism | consider a `PreToolUse` hook that exits 2 on writes under `tests/` — see [`docs/site/hooks.md`](../../../docs/site/hooks.md) — or run the fixer as the `fixer` subagent, which is bounded to five attempts |
| a flaky test gets "fixed" | the agent found a plausible-looking cause for a failure that was not deterministic | this is the strongest argument for keeping `workflow_dispatch`: a human decides the failure is worth an attempt |

**The security note worth reading twice**: this workflow gives a language model
`contents: write` on your repository and feeds it text from a CI log, which is content
your PRs can influence. Ronin's taint tracking covers content that arrives through
`web_fetch` and MCP; a log written into the workspace by `gh` and then `read` is **not**
tainted (see the laundering path documented in `ronin.cli.gate`). Keep the trigger
manual, keep the PR a draft, and read the diff.
