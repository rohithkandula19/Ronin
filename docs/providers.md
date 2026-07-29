# Providers

ronin is **provider-agnostic** — the same agent runs on Claude or on free open models. Switch provider or model from inside a session, no restart.

## Supported

| Provider | Free? | Default model | Notes |
|---|---|---|---|
| **Anthropic** | — | `claude-sonnet-4-6` | top quality; native SDK |
| **Gemini** | ✅ free tier | `gemini-2.5-flash` | generous free RPM |
| **Cerebras** | ✅ free tier | `gpt-oss-120b` | very fast; auto same-key sibling failover |
| **Groq** | ✅ free tier | `openai/gpt-oss-20b` | fast, tool-calling |
| **OpenRouter** | ✅ free models | `qwen/qwen3-coder:free` | one key, many models |
| **Ollama** | ✅ local | `llama3.1` | runs on your machine, no key |
| **local** | ✅ keyless | in-process | small open model, no daemon |
| **OpenAI** | — | `gpt-4o-mini` | — |
| **Custom** | — | (you specify) | any OpenAI-compatible endpoint |

## Setting a provider

```bash
ronin                         # first run: free-first onboarding picker
# in-session:
/provider                     # list all · free/paid · which have a key
/login gemini                 # set provider + paste a key (masked)
/free on                      # jump to a $0 provider you can run now
/model <name>                 # switch model without re-entering the key
/models                       # list what the current provider offers
```

Keys are stored per-provider in a local config (`.ronin/config.toml` or `~/.config/ronin/config.toml`), so a `/login openai` never clobbers your Cerebras key. Env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) are also honored.

## Smart routing (optional)

`/route <fast> <strong>` runs cheap/free turns on one model and complex turns on another; a self-tuning router escalates a cheap blade that proves unreliable in a repo. `/router` shows what it has learned. `ronin doctor --check` does a live key+model ping.

For orchestrated work, `ronin util agent-runs` also reports project-local
provider observations from completed subtasks. These are historical outcomes,
not a synthetic health check: a provider with no row has no recorded evidence.
