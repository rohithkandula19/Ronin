# apps/demo — AgentLab

Interactive playground demonstrating Ronin. Pick a pattern, send a message, see the trace.

## Run

```bash
# from repo root
uv sync --all-packages --all-groups
export ANTHROPIC_API_KEY=sk-ant-...   # optional — demo mode kicks in if absent
uv run uvicorn app.main:app --reload --port 8000 --app-dir apps/demo
```

Open `http://localhost:8000`.

## What it shows

- All four agent patterns (ReAct, Planner-Executor, Supervisor, Reflexion) wired into a single UI.
- Two demo tools the agents can call: `search` (toy KB) and `calc` (sandboxed arithmetic).
- Prompt-injection scanning at the API boundary (`InjectionScanner` from the `hardening` package).
- The full typed `trace` rendered alongside the answer — same trace shape Langfuse / your DB would store.

## Demo mode

If `ANTHROPIC_API_KEY` is unset, `/api/run` returns a canned trace so the page is interactive for anyone visiting the deployed demo. The banner at the top of the page tells users which mode they're in.

## Deploy

This app pairs cleanly with the `deployment-templates/vercel` config — drop `apps/demo` next to the Vercel template and `vercel deploy`.

## Tests

```bash
uv run pytest apps/demo -q
```
