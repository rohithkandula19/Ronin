# Free mode

ronin is **free-first**: point it at a free model and it codes for $0, no credit card.

## Free providers

| Provider | Where to get a key |
|---|---|
| **Cerebras** | cloud.cerebras.ai |
| **Groq** | console.groq.com/keys |
| **Gemini** | aistudio.google.com/apikey |
| **OpenRouter** (`:free` models) | openrouter.ai/keys |
| **Ollama** (local, no key) | ollama.com |
| **local** (keyless in-process brain) | built in — downloads a small model on first use |

## Get started free

On first run with no key, ronin shows a free-first picker. Or, in-session:

```
/free              # is my current model free? which free providers are ready?
/free on           # switch to the best free provider I can run right now
/provider          # every provider · free/paid · which have a key
/login gemini      # paste a free key (masked)
```

## What "free" means in the UI

- A **FREE** / **PAID** / **LOCAL** badge in the chip strip and per-turn footer.
- The cost ledger counts `$0` free turns vs a strong-model baseline.
- `--free` on `ronin pipeline` keeps every stage on a $0 provider (a free tier you hold a key for, else the keyless `local` brain — never a paid API).

## Honest notes

- Free tiers have **rate limits**; a long session may hit them (ronin can fail over to a sibling free model or fall back to `local`).
- Claude and OpenAI are **optional** — only if you want to pay for top quality. ronin never requires them.
