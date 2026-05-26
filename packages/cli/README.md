# csk — the agent CLI for startup ops

```bash
$ pipx install ro-claude-kit-cli
$ csk init --demo
$ csk ask "how many active subscriptions do we have?"

🤔 thinking…
✅ You have 2 active subscriptions: Alice (Pro, $49/mo) and Bob (Starter, $29/mo).
   Total MRR from active subs: $78/mo.
```

`csk` is the Claude-powered CLI you point at your startup's data. Configure once, then ask questions in plain English. Under the hood: the `Ronin` agent loop, hardening, and read-only MCP servers for Stripe / Linear / Slack / Notion / Postgres.

## Why this exists

Founders spend hours each week clicking through Stripe dashboards, Linear boards, Slack channels, and Postgres queries to answer questions they could just *ask*:

- "Which customers churned this month and what was their last support thread?"
- "Show me all open ENG issues over priority 2 that mention auth."
- "How many active Pro subscriptions, total MRR, and YoY growth?"

`csk` is the answer. One CLI, one config, ask anything.

## Install

```bash
pipx install ro-claude-kit-cli                    # recommended (isolated venv)
# or
pip install ro-claude-kit-cli
```

For Postgres support: `pipx install 'ro-claude-kit-cli[postgres]'`.

## Quickstart (no real credentials needed)

```bash
csk init --demo
csk ask "how many active subscriptions do we have?"
csk ask "what ENG issues are in progress and their priorities?"
csk chat
```

Demo mode ships with a small set of fake customers, subscriptions, charges, and Linear issues so you can play with the CLI before connecting real services.

## Real config

```bash
csk init   # interactive — prompts for each service's credentials
```

Or write `.csk/config.toml` directly:

```toml
anthropic_api_key = "sk-ant-..."
stripe_api_key = "rk_live_..."          # use a Restricted Key
linear_api_key = "lin_api_..."
slack_bot_token = "xoxb-..."
notion_token = "secret_..."
database_url = "postgres://readonly_user:...@host:5432/db"
model = "claude-sonnet-4-6"
```

`.gitignore` `.csk/` — the file is plaintext.

## Commands

| Command | What it does |
|---|---|
| `csk init [--demo]` | Create a config (interactive or demo) |
| `csk ask "<question>"` | One-shot — Claude runs against your tools, prints answer + trace |
| `csk chat` | Multi-turn REPL with short-term memory |
| `csk tools` | List the tools registered for the current config |
| `csk doctor` | Health check: config, auth, services |
| `csk version` | Print the version |

## Safety

Every input is run through the prompt-injection scanner from `ro-claude-kit-hardening` before reaching the agent. Every tool is read-only by design — there is no way to make `csk` mutate your data, even if Claude tries.

To add write paths: don't. If you must, fork and wrap them in `ApprovalGate` from the hardening package.

## Tests

```bash
uv run pytest packages/cli -q
```

No real credentials needed — Anthropic and HTTP clients are mocked.
