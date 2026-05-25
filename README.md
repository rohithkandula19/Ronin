# RO-Claude-kit — `ro`

> **A Claude agent CLI, three ways.** A Monday-morning founder **briefing** (revenue, churn, failed payments, urgent issues), an autonomous **agent** for ad-hoc data questions, and a **coding agent** (Claude-Code shaped) that reads, edits, and runs your code.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-v0.2.0-blue)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-347%20passing-green)](https://github.com/rohithkandula19/RO-Claude-kit/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Providers](https://img.shields.io/badge/providers-Claude%20·%20Ollama%20·%20OpenAI%20·%20Together%20·%20Groq%20·%20Fireworks-d4a373)](#-supported-providers)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

```bash
$ curl -sSL https://raw.githubusercontent.com/rohithkandula19/RO-Claude-kit/main/install.sh | bash
$ ro init --demo
$ ro briefing                          # the founder briefing
$ ro agent "why did revenue drop?"     # autonomous data agent
$ ro code "fix the failing test"       # coding agent
```

> The binary is **`ro`**. `csk` still works as a back-compat alias.

```markdown
# Founder briefing — 2026-05-11

## 💰 Revenue
- MRR: $334 (ARR ~$4,008)
- New this week: 2 (Team for Grace, Starter for Henry)
- Churned this week: 1 — ⚠️ Pro `cus_demo_carol` (ARR loss $588)

## 💳 Payments (last 7 days)
- 6 succeeded · 1 failed · 0 refunded
- Failed charges to retry: cus_demo_frank ($49) — card_declined
- ⚠️ 1 subscription past due — at risk of churn

## 🛠 Engineering
- Urgent open: 2 · High open: 5 · In-progress: 3
- ENG-101 Stripe webhook flake — Alice, In Progress

## ✅ Suggested action items
- Reach out to recently churned customers for exit interviews
- Retry failed payments / dunning for past-due subs
- Unblock or escalate every Urgent (P1) issue
```

## What is `csk`?

`csk` is the CLI you point at your startup's data. The **headline command is `csk briefing`** — a one-line replacement for the Monday-morning "let me check Stripe, then Linear, then Slack" ritual.

It's also a general-purpose data-question CLI: `csk ask "..."`, `csk chat` (multi-turn), `csk tui` (full-screen), and 11 more subcommands. Read-only by design — no path to mutate your data through the agent.

## 🧠 Supported providers

`csk` works with any LLM — proprietary or open-source. Switch providers with one config change.

| Provider | Backend | Default model | Auth |
|---|---|---|---|
| **Anthropic** (default) | native SDK | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| **Ollama** (local, free) | OpenAI-compat | `llama3.1` | none — runs on your machine |
| **OpenAI** | OpenAI-compat | `gpt-4o-mini` | `OPENAI_API_KEY` |
| **Together** | OpenAI-compat | `Llama-3.3-70B-Instruct-Turbo` | `OPENAI_API_KEY` |
| **Groq** | OpenAI-compat | `llama-3.3-70b-versatile` | `OPENAI_API_KEY` |
| **Fireworks** | OpenAI-compat | `llama-v3p3-70b-instruct` | `OPENAI_API_KEY` |
| **Custom** | OpenAI-compat | (you specify) | (you specify) |

Switch providers:
```toml
# .csk/config.toml
provider = "ollama"
model = "llama3.1"
```

## Install

```bash
# one-liner: installs uv if missing, clones the repo, syncs the workspace,
# drops a 'csk' shim in ~/.local/bin
curl -sSL https://raw.githubusercontent.com/rohithkandula19/RO-Claude-kit/main/install.sh | bash
```

Pin a tag: append `-s -- --ref v0.2.0`. PyPI publish is wired (`.github/workflows/release.yml`) and lands `pip install ro-claude-kit-cli` once `PYPI_TOKEN` is set as a repo secret.

For Postgres support after install: `(cd ~/.local/share/ro-claude-kit && uv pip install psycopg2-binary)`.

## 30-second quickstart (no real credentials)

```bash
csk init --demo                                 # ships fake Stripe + Linear data
csk ask "what ENG issues are in progress?"
csk ask "which customers have active subscriptions?"
csk chat                                        # multi-turn REPL
```

Demo mode is wired so you can play with the CLI before connecting any real services. Without an API key, an offline keyword router answers — set a key for full natural-language responses.

## Real config

```bash
csk init                                        # interactive — picks provider + service creds
```

Or write `.csk/config.toml`:

```toml
provider = "anthropic"
model = "claude-sonnet-4-6"
anthropic_api_key = "sk-ant-..."

stripe_api_key = "rk_live_..."                  # use a Restricted Key
linear_api_key = "lin_api_..."
slack_bot_token = "xoxb-..."
notion_token = "secret_..."
database_url = "postgres://readonly_user:...@host:5432/db"
```

Add `.csk/` to `.gitignore` — the file is plaintext credentials.

## Commands

| Command | What it does |
|---|---|
| `csk init [--demo]` | Create a config file (interactive or demo). |
| **`csk briefing`** | **Weekly founder briefing — auto-saved + shows week-over-week deltas inline.** |
| `csk briefing --slack <#chan>` | Post the briefing to Slack via `chat.postMessage`. |
| `csk briefing --history` | Trend table of all past briefings (MRR / new / churn / failed / urgent over time). |
| `csk briefing --out file.md` | Write briefing to a Markdown file. |
| `csk ask "<question>"` | One-shot — print answer + typed trace. |
| `csk chat` | Multi-turn REPL with short-term memory. |
| `csk tui` | Full-screen Textual UI: chat + live trace, F1 help. |
| `csk save NAME "..."` | Save a question for later (turns ad-hoc into reusable). |
| `csk run NAME` | Run a saved query. |
| `csk queries` / `csk unsave NAME` | List or remove saved queries. |
| `csk serve --port 8000` | Expose the agent as an HTTP API (`POST /ask`). |
| `csk plugins` | List user plugins discovered in `.csk/plugins/`. |
| `csk costs [--by model\|day]` | Token + cost usage recorded by previous runs. |
| `csk tools` | List the tools registered for the current config. |
| `csk doctor` | Health check: provider, auth, services. |
| `csk eval run <dataset>` | LLM-as-judge eval over a golden dataset (HTML report optional). |
| `csk eval drift <a> <b>` | Compare two runs; non-zero exit on regression. CI-friendly. |
| `csk version` | Print the version. |

## Hosted SaaS (`apps/api`)

`csk` is the CLI. For founders who want the briefing to **run automatically every Monday and post to Slack without anyone opening a terminal**, there's a FastAPI backend at `apps/api/`. Sign up, upload encrypted credentials, schedule. Same briefing engine under the hood — the CLI and the hosted version share the aggregator code in `packages/cli/.../briefing.py`.

```bash
# run the API
uv run uvicorn csk_api.main:app --reload --port 8000 --app-dir apps/api

# run the cron worker (separate process)
uv --project apps/api run python -m csk_api.worker --interval 60

# smoke test
TOKEN=$(curl -s -X POST http://localhost:8000/signup \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com"}' | jq -r .api_token)
curl -X POST http://localhost:8000/briefings -H "Authorization: Bearer $TOKEN" | jq -r .markdown
```

See [`apps/api/README.md`](apps/api/README.md) for the full surface, encryption details, and Railway deploy.

## Releasing & launching

The repo ships two operational bits for whoever's running the project, not just using it:

- **`scripts/release.sh <version>`** — bumps the CLI version, runs the test suite, updates the CHANGELOG, tags `vX.Y.Z`, pushes, and watches the tag-triggered PyPI publish workflow in `.github/workflows/release.yml`. Adds the version to PyPI in ~2 minutes. One-time setup: add a `PYPI_TOKEN` repo secret.
- **`scripts/launch_kit/`** — 9 numbered, copy-paste-ready messages (Anthropic email/DM, founder DM, Show HN title + first comment, Indie Hackers, Twitter variants, LinkedIn, public Anthropic tag) + a 5-day plan at `DAY_BY_DAY_PLAN.md`. Start there once `release.sh` succeeds.

## Why csk vs ...

| | csk | aider | langchain | crewai |
|---|---|---|---|---|
| Built specifically for startup ops (Stripe / Linear / Slack / Notion / Postgres) | ✅ | ❌ | ❌ | ❌ |
| Read-only by default for every integration | ✅ | n/a | ❌ | ❌ |
| Built-in prompt-injection scanner | ✅ | ❌ | ❌ | ❌ |
| Works with Claude AND open-source LLMs | ✅ | ✅ | ✅ | ✅ |
| Zero-config offline demo (`--demo`) | ✅ | ❌ | ❌ | ❌ |
| Hand-rolled agent loop (no LangChain dependency) | ✅ | ✅ | n/a | ❌ |
| LLM-as-judge eval suite included | ✅ | ❌ | ❌ | ❌ |

aider is the gold standard for *coding* agents. CrewAI / LangChain are agent frameworks — useful but heavyweight. `csk` is a focused product for one thing: asking your operational data questions.

## Safety by default

Every input passes through a prompt-injection scanner before reaching the LLM. Every tool is read-only. There is no path through `csk` to mutate your data — even if the LLM tries, the kit's `ToolAllowlist` blocks it. Adding write paths is a deliberate fork-and-wrap operation through `ApprovalGate`.

PII (emails, SSNs, credit cards, API keys) is redacted from traces before anything leaves your process.

## What's under the hood

`csk` is the user-facing wrapper. The substance lives in seven packages you can also use independently:

| Package | What it does | Tests |
|---|---|---|
| `agent-patterns` | ReAct, Planner-Executor, Multi-Agent Supervisor, Reflexion + LLM provider abstraction | 20 |
| `eval-suite` | LLM-as-a-judge, golden datasets, drift detection, HTML reports | 11 |
| `memory` | Short-term (rolling summary), long-term (pluggable vector store), user preferences | 11 |
| `hardening` | Prompt-injection scanner, tool allowlist, approval gates, output validator | 20 |
| `mcp-servers` | Read-only Postgres, Stripe, Linear, Slack, Notion, Tavily, GitHub templates | 67 |
| `cli` | The `csk` binary | 36 |
| `deployment-templates` | Docker Compose, Modal, Vercel, Railway | — |
| `apps/demo` | AgentLab — interactive FastAPI playground | 5 |

**152 tests** across all packages, green on every push (see CI).

## Use the modules without the CLI

Build an agent in 5 lines:

```python
from ro_claude_kit_agent_patterns import ReActAgent, Tool

agent = ReActAgent(
    system="You are a helpful research assistant.",
    tools=[Tool(name="search", description="...", input_schema={...}, handler=my_search)],
)
print(agent.run("What is the ReAct pattern?").output)
```

Use it with Ollama instead of Claude:

```python
from ro_claude_kit_agent_patterns import OllamaProvider, ReActAgent

agent = ReActAgent(
    system="...",
    tools=[...],
    provider=OllamaProvider(model="llama3.1"),
)
```

Add an eval suite:

```python
from ro_claude_kit_eval_suite import EvalSuite, Rubric, GoldenDataset

suite = EvalSuite(
    rubric=Rubric(criteria=["task_success", "faithfulness", "safety"]),
    target_runner=lambda case: agent.run(case.input).output,
)
report = suite.run(GoldenDataset.from_jsonl("./golden.jsonl"))
```

## Examples

End-to-end agents you can run today:

| Example | What it shows |
|---|---|
| [`research-agent/`](examples/research-agent/) | ReAct with a toy KB — smallest, simplest |
| [`customer-support/`](examples/customer-support/) | Supervisor + 4 sub-agents, Pydantic-validated `DraftReply`, 25-case golden dataset |
| [`code-reviewer/`](examples/code-reviewer/) | 3 specialist sub-agents (style / bugs / security) aggregating into a typed `CodeReview` |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run python examples/customer-support/main.py "I was charged twice for my Pro plan!"
uv run python examples/code-reviewer/main.py examples/code-reviewer/sample_buggy_code.py
```

## Try AgentLab — the interactive playground

A FastAPI app that lets you click through all four agent patterns side-by-side:

```bash
git clone https://github.com/rohithkandula19/RO-Claude-kit
cd RO-Claude-kit
uv sync --all-packages --all-groups
uv run uvicorn app.main:app --port 8000 --app-dir apps/demo
```

Open http://localhost:8000.

## Repository layout

```
RO-Claude-kit/
├── packages/
│   ├── cli/                  # the csk binary
│   ├── agent-patterns/       # core loop patterns + provider abstraction
│   ├── eval-suite/           # LLM-as-a-judge
│   ├── memory/               # 3-layer memory
│   ├── mcp-servers/          # 5 read-only service templates
│   ├── hardening/            # injection / allowlist / approval / validation
│   └── deployment-templates/ # docker-compose, modal, vercel, railway
├── apps/
│   ├── demo/                 # AgentLab — interactive playground
│   └── docs/                 # Mintlify documentation site
├── examples/
└── ...
```

## Documentation

- [Documentation site](apps/docs/) — concepts, production checklist, ADRs (run `mintlify dev` from `apps/docs/` to preview)
- [CHANGELOG.md](CHANGELOG.md) — what's new
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to add MCP servers, providers, etc.

## License

MIT. See [LICENSE](LICENSE).

## Star history

If this saves you a weekend, ⭐️ the repo. It's the only metric I'll see.

---

Built for [Claude](https://www.anthropic.com/claude). Not affiliated with Anthropic.
