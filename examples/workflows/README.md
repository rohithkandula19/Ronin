# copyable workflows

Three things people actually want an agent for, wired against the **real** v2 surface.
Every flag in here comes from `ronin.cli.main.build_parser`; every exit-code check
matches `ronin.ui.headless`. A copyable example containing a flag that does not exist is
worse than no example, so nothing on this page is aspirational.

| workflow | what it does | mutates your repo? | needs an API key / network? |
|---|---|---|---|
| [pre-commit-reviewer](pre-commit-reviewer/) | reviews the staged diff and can block the commit | no — runs in `--mode plan`, which removes every mutating tool | yes, unless your model is local |
| [ci-failure-fixer](ci-failure-fixer/) | reproduces a failing CI job, fixes it, opens a PR | yes — on a branch, never on the default branch | yes; plus a `GITHUB_TOKEN` for the PR |
| [repo-onboarding-explainer](repo-onboarding-explainer/) | orients you in an unfamiliar repository | no — `--mode plan` | yes, unless your model is local |

## the three facts they all rely on

**The entry point is `python -m ronin`, not `ronin`.** The `ronin` console script
belongs to `packages/cli` (the v1 CLI). Every script here uses the module form.

**Exit codes.** `0` done, `1` error, `2` an approval was requested and denied. A
headless run has no human attached, so it denies **every** gated call — there is no
flag that turns that off. `2` therefore means "this task needed a permission you have
not granted in `.ronin/settings.json`", and it is the code that trips people up first.

**`--mode ask` is the default and the safe one.** Two of these three workflows use
`--mode plan`, which is stricter still: the mutating tools are *removed from the
registry*, so the guarantee is structural rather than a sentence in a prompt. The one
workflow that must edit uses `--mode auto_edit` plus an explicit allow rule for the
commands it needs, and says so at the top of its README.

**`--yolo` is not in any of these, on purpose.** It does not merely stop asking: it
waives the deny list (`ronin.safety.denylist.Denylist.yolo`), so `rm -rf /`, a fork
bomb and reading private key material stop being refused. If you add it to one of these
scripts, you are running an unattended agent with nothing between it and your disk.

## what they cost

No figure is quoted anywhere in these examples, because none has been measured on this
machine and a plausible-looking invented number is worse than no number. Each README
says what drives the cost and how to measure yours:

```sh
# bound it, then read what it actually spent
python -m ronin -p "…" --max-usd 0.25 --max-turns 10 --max-seconds 300
python -m ronin sessions      # per-session turn count and dollar spend
```

Spend is computed from the per-million-token prices *you* write in `models.toml`.
Ronin ships no price table — a hardcoded price goes stale silently and then every cost
figure is wrong with no warning. A model with no prices set is recorded as unpriced and
flagged, not as free.
