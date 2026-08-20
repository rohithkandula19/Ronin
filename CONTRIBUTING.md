# Contributing to Ronin

Glad you're here. This is a small, opinionated project — contributions are welcome, but please read this once before opening a PR so we don't waste each other's time.

## Read this first

**[`docs/RULES.md`](docs/RULES.md) is the bible.** It is one page of non-negotiables — the
`$0` constraint, never inventing a number, offline tests, errors as values, "two
reasonable options → ask" — and where it disagrees with anything below, it wins. This
file covers *how* to open a PR; that one covers what the code has to be before it merges.

Two companions worth reading once: [`docs/STACK.md`](docs/STACK.md) for what the stack is
and, honestly labelled, what is decided but not yet implemented; and
[`docs/POSITIONING.md`](docs/POSITIONING.md) for what the project is actually arguing.

## Quick start

```bash
git clone https://github.com/rohithkandula19/Ronin
cd Ronin
make install     # uv sync --all-packages --all-groups, plus pre-commit hooks
make test        # the full suite: packages, apps, training, tests
```

`make install` installs the pre-commit hooks, so lint, types, the offline-tests check and
the generated-file gates all run before a commit rather than in CI. If you skip it, CI
will tell you the same things more slowly.

Other targets: `make lint`, `make typecheck`, `make coverage`, `make eval` (the demo — no
key, no model, no cost), `make run ARGS='"fix the failing test"'`.

CI runs `pytest packages apps training tests -q`, which is exactly what `make test` runs.
If it's green locally, it'll be green in CI.

## What we welcome

- **Bug fixes** — open an issue first if it's non-trivial; otherwise a PR with a failing test → fix is great.
- **New MCP servers** — read-only, following the pattern in `packages/mcp-servers/src/ronin_mcp_servers/`. Each server: a `*_ReadOnlyTools` Pydantic class + a `*_tools()` factory + tests with mocked `httpx`.
- **New providers** — add a class subclassing `LLMProvider` in `packages/agent-patterns/src/ronin_agent_patterns/providers/`. See `openai_compat.py` for the template.
- **Documentation** — typos, clarifications, new cookbook recipes in `apps/docs/`.
- **CLI commands** — keep them small and composable. `ronin` should feel like `git`, not `kubectl`.
- **Roadmap workstreams** — use `docs/CONTRIBUTION_ROADMAP.md` to pick substantial areas that can become several focused PRs.

## What we don't want (please don't open PRs for these)

- **Write paths** in MCP servers. Read-only is a deliberate design choice. If you need writes, wrap the handler in `ApprovalGate` from `ronin-hardening` *in your own code*.
- **Skipping the prompt-injection scanner** for "convenience". The scanner is the load-bearing safety check.
- **New agent frameworks** as dependencies (LangChain, LlamaIndex, CrewAI, etc.). The point of this kit is to *not* depend on them.
- **Vendor SDKs** as hard dependencies. Use `httpx` against documented HTTP APIs; vendor SDKs go behind optional extras.
- **Major refactors** without an issue first. Open one, describe the problem, get a thumbs-up, then code.

## Conventions

See [`docs/RULES.md`](docs/RULES.md) for the ones that are enforced. These are the rest:

- **Python**: 3.11+. Type hints everywhere. Pydantic v2 for state in the v1 `packages/` tree; plain frozen dataclasses in `src/ronin`.
- **Imports**: standard library, then third-party, then local. Sorted alphabetically within each group — `ruff` does this for you.
- **Tests**: pytest. No real API calls, and **no network import in a test file at all** — `scripts/check_test_imports.py` fails the commit. Mock with `FakeProvider` or an injected transport rather than `MagicMock(spec=httpx.Client)`, which needs the import the hook forbids.
- **Commits**: imperative mood ("add X", not "added X"). One logical change per commit.
- **PR titles**: `<area>: <what changed>`. Examples: `cli: add --json flag to ask`, `mcp-servers: support Stripe usage records`.

## Adding a new MCP server (template)

1. Create `packages/mcp-servers/src/ronin_mcp_servers/<name>.py` with a Pydantic class wrapping the read-only methods.
2. Add a `<name>_tools()` factory returning a `name -> handler` dict.
3. Export both from `__init__.py`.
4. Tests in `packages/mcp-servers/tests/test_<name>.py` mocking `httpx.Client`.
5. Update the README status table.

## Adding a new LLM provider (template)

1. Create `packages/agent-patterns/src/ronin_agent_patterns/providers/<name>.py`.
2. Subclass `LLMProvider`, implement `complete(...)`.
3. Translate the neutral `Message` list to your provider's wire format.
4. Export from `providers/__init__.py`.
5. If it's a CLI-supported provider, add a preset to `PROVIDER_PRESETS` in `packages/cli/src/ronin_cli/config.py` and route it in `runner.build_provider`.
6. Tests in `packages/agent-patterns/tests/test_providers.py`.

## Releasing

Maintainer-only:
1. Bump version in `packages/cli/pyproject.toml` (semver).
2. Update `CHANGELOG.md`.
3. Tag: `git tag v0.x.y && git push --tags`.
4. `cd packages/cli && uv build && uv publish`.

## Getting help

Open a [discussion](https://github.com/rohithkandula19/Ronin/discussions) for questions, an [issue](https://github.com/rohithkandula19/Ronin/issues) for bugs.

## Code of conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
