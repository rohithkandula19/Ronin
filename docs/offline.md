# Offline mode

`ronin --offline` runs with **zero network egress** — a local brain and no network tools. Nothing leaves the machine.

```bash
ronin --offline "explain @main.py and suggest tests"
ronin --offline code "fix the failing test"
```

## What it does

- **Forces a local brain.** If your provider isn't already local, it switches to **Ollama** (`http://localhost:11434`). Cloud failover is cleared.
- **Strips every network tool** — `web_search`, `fetch_url`, image/video generation, and browser tools are removed from the toolbelt (`NETWORK_TOOLS` in `offline.py`).
- Shows a **LOCAL / OFFLINE** badge.

## Two ways to run local

1. **Ollama** — install from ollama.com, run `ollama serve`, pull a model (e.g. `ollama pull llama3.1`), then `ronin --offline`.
2. **Keyless `local` brain** — `ronin local` (or `/provider local`) runs a small open coder model **in-process**, no daemon and no key. It downloads a quantized model on first use (a one-time multi-GB download to `~/.ronin/models/`).

## Good for

Planes, air-gapped boxes, or any time you want a hard guarantee that your code never touches the network. Everything is gated exactly as online — reads free, writes/commands approved.
