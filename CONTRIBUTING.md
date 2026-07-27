# Contributing to Ronin

Glad you're here. This is a small, opinionated project — contributions are welcome, but please read this once before opening a PR so we don't waste each other's time.

## Quick start

```bash
git clone https://github.com/rohithkandula19/Ronin
cd Ronin
uv sync --all-packages --all-groups
uv run pytest packages/agent-patterns packages/eval-suite packages/memory \
    packages/hardening packages/mcp-servers packages/cli apps/demo -q
```

CI runs the same command. If it's green locally, it'll be green in CI.

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

- **Python**: 3.11+. Type hints everywhere. Pydantic v2 for state.
- **Imports**: standard library, then third-party, then local. Sorted alphabetically within each group.
- **Tests**: pytest. Mock the network with `MagicMock(spec=httpx.Client)` or `FakeProvider`. No real API calls in tests.
- **Commits**: conventional-style scopes in imperative mood (`feat(agent): add planner cache`, not "added planner cache"). One logical change per commit.
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
