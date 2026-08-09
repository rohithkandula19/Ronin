# ronin docs

Reference and setup for the v2 agent in `src/ronin/`.

| page | what it answers | source |
|---|---|---|
| [quickstart.md](quickstart.md) | install, point it at a model, run one turn, read the exit code | hand-written |
| [tools.md](tools.md) | every tool, its arguments, and the exact description the model sees | generated from `ronin.tools.registry` |
| [config.md](config.md) | the settings layers, every key, the rule grammar, every path on disk | generated from `ronin.safety.settings` + `ronin.cli.spine` |
| [hooks.md](hooks.md) | the five events, the exit-code contract, the payload on stdin | generated from `ronin.agents.hooks` |
| [subagents.md](subagents.md) | frontmatter keys, the four builtins, path restriction, the `model` role | generated from `ronin.agents.definitions` |
| [providers.md](providers.md) | setup per provider, including both local paths | generated from `ronin.providers.registry` |
| [telemetry.md](telemetry.md) | what opt-in telemetry sends, what it refuses to send, how to audit it | generated from `ronin.telemetry` |

## generated means generated

Six of these seven pages are produced by `docs/site/generate.py` from the code they
document, and `tests/telemetry/test_ronin_telemetry_docsite.py` fails when a page on
disk disagrees with the code. Hand-written reference docs drift the day they are
written; if the tool list on [tools.md](tools.md) is wrong, the build is red.

```sh
python docs/site/generate.py           # rewrite the generated pages
python docs/site/generate.py --check   # exit 1 if any page is stale
```

Every generated page opens with an HTML comment saying so. Editing one by hand loses
the edit on the next regeneration — change the generator instead.

## the entry point is `python -m ronin`

Not `ronin`. The `ronin` console script belongs to `packages/cli` (the v1 CLI) and
repointing a shipped command at a different program is a product decision, not a
wiring one, so the v2 app is reached as a module. Every command in these pages is
spelled `python -m ronin …` for that reason.

## no numbers here

Nothing on these pages reports a benchmark score, a pass rate, a token count or a
timing. Where a figure would help, the pages say what would produce it. Counts that
are derived by walking a real object in this checkout — "13 tools", "the five hook
events" — are recomputed by the generator on every run and are facts about the code,
not measurements of behaviour.

## examples

Three copyable workflows live in [`examples/workflows/`](../../examples/workflows/):
a pre-commit reviewer, a CI failure fixer, and a repo onboarding explainer. Each one
uses only flags that exist in `ronin.cli.main`, and each README says what it costs to
run and what can go wrong.
