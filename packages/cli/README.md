# ronin — a masterless, terminal-native Claude agent

```bash
pipx install ronin-cli
ronin init --demo
ronin chat
```

`ronin` is a **Claude-Code-style AI coding agent** — it reads, edits, and runs
your code from the terminal — built on a **provider-agnostic agent framework**
with first-class evals, memory, security hardening, and MCP tool integrations.

Plug in Claude for top quality, or run it **free** on Gemini, Cerebras, Groq,
or Ollama.

Provider keys can stay outside config files. Ronin discovers standard
provider-specific environment variables such as `GROQ_API_KEY`,
`GEMINI_API_KEY`, `CEREBRAS_API_KEY`, and `OPENROUTER_API_KEY`; `/provider`
reports readiness without printing secret values.

## Install

```bash
pipx install ronin-cli            # recommended (isolated venv)
# or
pip install ronin-cli
```

`pipx install 'ronin-cli[postgres]'` adds the read-only Postgres MCP server.

## What you get

- **Claude-Code-style coding agent** — `ronin code` opens a REPL where Claude
  reads, edits, and runs your code under a sandbox. Parallel tool calls,
  prompt caching, `/context` fullness bar, in-session `/cost` and `/router`,
  incremental, syntax-highlighted diff previews with literal file content,
  checkpoint & rewind, vision-in-the-loop.
- **Provider-agnostic** — Claude, Gemini, Cerebras, Groq, Ollama, OpenRouter,
  OpenAI. Switch with `/model`, or let the **Self-tuning Router** pick the
  cheapest blade that reliably wins on your repo.
- **200+ built-in plugins** — currency, crypto, weather, GitHub, recipes,
  NASA, Luhn, WCAG contrast, … plus a `ronin plugin from-api` generator that
  turns ANY REST endpoint into an agent tool.
- **MCP catalog** — 24 first-party servers (Stripe, Linear, Slack, Notion,
  Postgres, GitHub, Playwright, …) — `ronin mcp catalog` then `install`.
  Supports both stdio and remote (HTTP/SSE) transports.
- **Nightshift autonomous mode** — works your backlog into reviewable patches
  overnight (worktree-isolated, idempotent, cron-schedulable, opens real PRs).
- **20+ offline dev/API commands** — `hash`, `jwt`, `uuid`, `subnet`, `cron`,
  `chmod`, `json` (jq-lite), `passphrase`, `curl2code`, `redact`, `mock`,
  `tree`, `deadcode`, `complexity`, `smell`, `api`, `changelog`, … all stdlib,
  no network.
- **Trust gates** — Sentinel mode (abstain over bluff), secret-scan write
  guard, `.roninignore`, `--budget` spend cap.

## Quickstart (no credentials needed)

```bash
ronin init --demo
ronin ask "how many active subscriptions do we have?"
ronin chat
ronin code      # the Claude-Code-style coding agent
```

Demo mode answers from in-process fixtures — calls don't leave your machine.

## Bring your own keys

```bash
ronin init           # interactive — prompts for each provider's credentials
ronin login          # set/refresh provider keys later
```

Or write `~/.ronin/config.toml` directly:

```toml
anthropic_api_key = "sk-ant-..."
model = "claude-sonnet-4-6"
```

`~/.ronin/` is plaintext — keep it out of version control.

## Highlights

```bash
ronin code                       # Claude-Code-style coding REPL
ronin commit                     # Conventional Commit message from your diff
ronin pr                         # open a PR with a title + body from your diff
ronin nightshift                 # autonomous teammate works your backlog
ronin mcp catalog                # browse 24 popular MCP integrations
ronin plugin library             # 200+ ready-to-add plugins
ronin plugin from-api <url>      # turn any REST endpoint into an agent tool
ronin scan                       # block secrets at commit time
```

## Safety

`ronin code` writes only inside the project. The MCP servers shipped with
ronin are read-only by default; write paths require explicit opt-in.
Prompt-injection scanning is on by default for tool outputs.

## Project

- Source, issues, and full docs: <https://github.com/rohithkandula19/Ronin>
- Changelog: <https://github.com/rohithkandula19/Ronin/blob/main/CHANGELOG.md>
- License: MIT
