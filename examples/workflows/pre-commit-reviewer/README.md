# pre-commit reviewer

A git `pre-commit` hook that has Ronin read the staged diff and either comment or block
the commit.

> **Needs a model, and therefore usually an API key and network.** The hook shells out
> to `python -m ronin`, which needs a `models.toml` (see
> [`docs/site/providers.md`](../../../docs/site/providers.md)). With a local model
> (`provider = "ollama"`, `provider = "mlx"`) it needs no key and no internet. With a
> hosted model it needs the key named by `api_key_env` present in the environment your
> git client runs in — which is **not** always the environment of your shell. GUI git
> clients in particular start from a login shell with a different environment; if the
> hook works in a terminal and not in your editor, that is why.

## install

```sh
cp examples/workflows/pre-commit-reviewer/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
printf '.ronin/tmp/\n' >> .gitignore
```

The hook writes the staged diff to `.ronin/tmp/staged.diff` and deletes it afterwards.
It has to be **inside the workspace**: the file tools refuse any path that resolves
outside the tree, so a diff in `/tmp` is a diff the agent cannot read.

No `.ronin/settings.json` changes are needed. `--mode plan` removes every mutating tool
from the registry, and `read`/`grep`/`glob` are read-only and declare they need no
approval, so nothing in this workflow is gated.

## what it runs

```sh
python -m ronin -p "$PROMPT" \
    --output-format text \
    --mode plan \
    --max-turns 8 --max-usd 0.20 --max-seconds 120 \
    --no-record --no-mcp --no-wizard
```

* `--mode plan` — the guarantee that a reviewer cannot edit your code is *structural*:
  `write`, `edit`, `multi_edit`, `bash` and `bash_output` are not in the registry the
  model is shown. A prompt saying "only review" is a preference; a model with no `write`
  tool cannot write.
* `--output-format text` — the answer on stdout, warnings on stderr, so the script can
  capture one without the other.
* `--no-record` — a transcript per commit fills `.ronin/sessions/` with noise. Drop
  this flag if you want the reviews on disk; `python -m ronin sessions` then lists them
  with their cost.
* `--no-mcp` — starting MCP servers on every commit costs seconds you will notice.
* `--no-wizard` — a hook must never stop to ask a question.

## the two design decisions, and why they look backwards

**It fails open.** A missing config, a network blip, a timeout, a `2` exit or a
malformed answer lets the commit through with a warning on stderr. This is deliberate: a
reviewer that blocks commits when the *reviewer* is broken gets deleted within a week,
and then you have no reviewer at all. Set `RONIN_REVIEW_STRICT=1` to invert it once you
trust the setup.

**It blocks only on an explicit verdict.** The answer must contain `VERDICT: BLOCK`.
Findings alone — even severe-sounding ones — pass. An agent that can veto a commit by
accident is an agent you will route around with `git commit --no-verify` permanently,
and a bypassed hook reviews nothing.

## knobs

| variable | default | meaning |
|---|---|---|
| `RONIN_REVIEW_SKIP` | `0` | `1` skips the review entirely |
| `RONIN_REVIEW_STRICT` | `0` | `1` makes a harness failure block the commit too |
| `RONIN_REVIEW_MAX_USD` | `0.20` | passed to `--max-usd` |
| `RONIN_REVIEW_MAX_TURNS` | `8` | passed to `--max-turns` |
| `RONIN_REVIEW_MAX_SECONDS` | `120` | passed to `--max-seconds` |
| `RONIN_REVIEW_MAX_DIFF_BYTES` | `60000` | diffs larger than this are not reviewed |

`git commit --no-verify` bypasses the hook, as it does every pre-commit hook. That is a
feature: the escape hatch is what makes the hook tolerable.

## what it costs

Once per commit, and it is the only workflow here that runs on your critical path —
every `git commit` waits for it.

The spend per run is driven by three things: the size of the staged diff, how many files
the model reads around it, and the price of the model you routed `main` to. No figure is
quoted here because none has been measured on this machine, and a plausible-looking
invented number in a cost table is worse than no number. Measure yours:

```sh
# leave --no-record off for a few commits, then:
python -m ronin sessions        # turns and dollars, newest first
```

`--max-usd 0.20` is a ceiling, not an estimate. Lower it until commits feel slow to
review, then raise it one step.

Cheapest configuration: point `main` at a local model. The review is a read-heavy,
judgement-light task, which is the shape local models are least bad at.

## what could go wrong

| symptom | cause | fix |
|---|---|---|
| every commit warns "ronin exited 1" | no `models.toml`, or the API key is missing from git's environment | `python -m ronin doctor` from the same shell; check `api_key_env` is exported where your git client runs |
| commits take a long time | the diff is large, or `main` is a slow reasoning model | lower `RONIN_REVIEW_MAX_SECONDS`; route this workflow's model to something faster |
| "ronin exited 2" | something was gated even in plan mode — a hook of your own, or a rule with `always_ask` matching a read | `python -m ronin doctor` shows which layer contributed the rule |
| the review talks about code that is not in the diff | the model read surrounding files, which it is told to do, and lost track of which lines are new | the prompt in the hook is the place to tighten this; keep the diff small |
| the review is confidently wrong | it is a language model reading a diff with no test run — `--mode plan` means it cannot execute anything to check itself | treat the output as a comment from a reviewer who has not run the code, which is what it is |
| a huge refactor is silently not reviewed | over `RONIN_REVIEW_MAX_DIFF_BYTES` | the hook says so on stderr; split the commit |
| the diff file is committed | `.ronin/tmp/` is not gitignored | add it; the hook also deletes the file on the normal path |

**The failure mode worth naming loudest**: this hook sends your staged diff to whatever
provider `main` points at. That is your unreleased code leaving your machine on every
commit. If that is not acceptable — and for a lot of employers it is not — use a local
model, or do not install this hook.
