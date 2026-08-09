# repo onboarding explainer

Point Ronin at a repository you have never read and get an orientation whose every claim
carries a `file:line` you can check.

> **Needs a model, and therefore usually an API key and network.** With a local model
> (`provider = "ollama"`, `provider = "mlx"`) it needs neither. It reads the target
> repository and does not modify it — but it does leave two directories behind; see
> [what it writes](#what-it-writes-even-in-read-only-mode) below, because "read-only"
> here means "cannot edit your code", not "touches nothing".

## use it

```sh
chmod +x examples/workflows/repo-onboarding-explainer/onboard.sh
examples/workflows/repo-onboarding-explainer/onboard.sh ~/src/some-unfamiliar-repo
examples/workflows/repo-onboarding-explainer/onboard.sh ~/src/some-unfamiliar-repo out.md
```

Or without the script:

```sh
python -m ronin -p "$(cat examples/workflows/repo-onboarding-explainer/onboard.md)" \
  --cwd ~/src/some-unfamiliar-repo \
  --output-format text --mode plan \
  --max-turns 40 --max-usd 1.00 --max-seconds 600 \
  --no-wizard --no-record --no-mcp
```

## why `--mode plan`

`plan` does not ask the model to be careful; it removes the tools. The registry it is
shown contains `read`, `glob`, `grep` and `task` and nothing else — `write`, `edit`,
`multi_edit`, `bash` and `bash_output` are absent, and `ronin.cli.stream.plan_runtime`
re-derives that property from the specs rather than trusting the narrowing, so a
mislabelled tool fails at startup instead of at the first write.

For a repository you do not own, that is the difference between a guarantee and a
promise. A model told "do not change anything" changes something three turns later, when
the instruction has scrolled past the interesting part of the context.

The cost of `plan` is real and worth stating: with no `bash`, the agent **cannot run
anything**. It cannot execute the test suite to check that the commands it found in CI
actually work, and it cannot run `git log` to see what changed recently. The prompt
compensates by telling it to prefer CI configuration over README prose — CI is executed,
documentation is aspirational — but everything in section 2 ("how to run it") is read off
config files, not verified. If you want it verified, run again with `--mode ask` and
approve the commands yourself.

## the prompt is the product

`onboard.md` is most of the value here, and it is worth reading before you run it. Three
things in it do the work:

* **Every claim carries a citation, or is not made.** An orientation that is 90% right
  and unattributed is worse than one that is 70% right and checkable, because you cannot
  tell which 10% to distrust.
* **It forbids pattern-matching.** The default failure of this task is a confident essay
  about what a repository of this shape *usually* contains. The prompt names that
  failure and tells the model to read one file and describe *this* one.
* **It asks for a traced path through the system**, not a directory listing. A listing is
  something you can get yourself in one command.

It also asks for "three questions you would ask the author", which is the section people
report as most useful: the questions are where the model's uncertainty is honest.

The file doubles as a `.ronin/commands/onboard.md` slash command:

```sh
mkdir -p .ronin/commands
cp examples/workflows/repo-onboarding-explainer/onboard.md .ronin/commands/onboard.md
```

**But `/onboard` does not work yet.** In this build the line session dispatches slash
commands against the *builtin* registry only (`ronin.cli.main._slash`), so a
user-defined command is reported by name as "not wired into this line session" rather
than run. The markdown still loads — `python -m ronin doctor` counts it under "slash
commands" — and the working way to use it today is the `-p "$(cat …)"` form above. When
the slash layer is wired, the same file starts working with no change.

## what it writes, even in read-only mode

This is the part that surprises people, so it is measured rather than assumed. A single
`python -m ronin doctor --cwd <target>` on a fresh git repository created:

```
<target>/.ronin/cache/repomap-<hash>.json   the repo-map parse cache
<target>/.ronin/checkpoints/                a shadow git repository (git init)
```

The repo map is built when the workspace loads, and it caches its parse so the second
run is fast. The checkpoint store initialises a *separate* git repository under
`.ronin/checkpoints/` so it can snapshot state before a mutating turn — it does this on
probe, before any turn has run.

`--no-record` is why there is no `.ronin/sessions/` in that list; without it, a
transcript is written too. Nothing under `.ronin/` is inside the target's own git
history, but it *will* show up as untracked in `git status`, which matters if the
repository is not yours. Delete `<target>/.ronin/` when you are done, or run against a
copy.

Neither directory can be avoided from the command line in this build. If that is
unacceptable, clone to a scratch directory first — which is also what makes a second
opinion from a second model free of interference.

## what it costs

One invocation, and the driver is repository size: the agent greps and reads its way
around, and every file it reads is tokens. A large monorepo will hit `--max-turns 40`
before it finishes section 7.

No figure is quoted here. None has been measured on this machine, and a plausible number
in a cost table is worse than no number. Measure yours:

```sh
# drop --no-record for one run, then:
python -m ronin sessions --cwd ~/src/some-unfamiliar-repo
```

Practical advice: run it once with a low ceiling (`RONIN_ONBOARD_MAX_USD=0.25`) to see
how far it gets, then raise it. The prompt tells the model to stop mid-section and say
which sections it did not reach, so a cheap partial run is a usable answer rather than a
wasted one.

| variable | default | meaning |
|---|---|---|
| `RONIN_ONBOARD_MAX_USD` | `1.00` | `--max-usd` |
| `RONIN_ONBOARD_MAX_TURNS` | `40` | `--max-turns` |
| `RONIN_ONBOARD_MAX_SECONDS` | `600` | `--max-seconds` |

## what could go wrong

| symptom | cause | what to do |
|---|---|---|
| stops mid-orientation | hit `--max-turns`, `--max-usd` or `--max-seconds` | the answer says which sections it skipped; raise the relevant ceiling |
| citations point at lines that do not say what it claims | the model read a file, summarised from memory, and the line number drifted | this is the failure the citation format exists to expose — spot-check three and you will know whether to trust the rest |
| a confident section about a framework the repo does not use | pattern-matching, which the prompt forbids and cannot prevent | check the citations in that section; usually they are absent or vague |
| "how to run it" is wrong | `--mode plan` means it never executed anything, so the commands come from config files that may be stale | rerun with `--mode ask` and approve the commands, or just try them |
| slow on a large repository | the repo map and the greps scale with the tree | narrow it: run with `--cwd <target>/<the-one-subdirectory-you-care-about>` |
| exits 2 | something was gated even in plan mode — a local rule with `always_ask`, or a hook of your own | note that in **this build** exit 2 is also raised for approvals that were *granted*; see the trap documented in [`../ci-failure-fixer/README.md`](../ci-failure-fixer/README.md). The script prints the answer before checking the code, so a partial orientation is not lost |
| `.ronin/` appears in the target's `git status` | expected; see [what it writes](#what-it-writes-even-in-read-only-mode) | delete it, or work on a clone |

**The privacy note**: this sends the contents of files from the target repository to
whatever provider `main` points at. For someone else's private code, or an employer's,
that is a decision to make deliberately. A local model makes this workflow entirely
offline, and orientation is a read-heavy, judgement-light task — the shape local models
handle least badly.
