# deployment-templates

Drop-in deployment configs for an Ronin agent. Pick the target that matches your app's profile.

| Target | When to use | Files |
|---|---|---|
| `docker-compose/` | Local dev with a real Postgres alongside the agent | `docker-compose.yml`, `Dockerfile`, `.env.example` |
| `modal/` | Compute-heavy / long-running agents; Python-native serverless | `app.py` |
| `vercel/` | HTTP-request agents alongside a Next.js frontend | `vercel.json`, `api/chat.py`, `requirements.txt` |
| `railway/` | Persistent agent backend with managed Postgres / Redis | `railway.json`, `Dockerfile` |

## Docker Compose (local dev)

```bash
cp packages/deployment-templates/docker-compose/.env.example .env
cd packages/deployment-templates/docker-compose
docker compose up --build
```

Brings up Postgres + your agent app on port 8000. The `Dockerfile` is multi-stage with a `uv`-based builder — fast cold builds, slim runtime image.

## Modal

```bash
pip install modal
modal token new
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...
modal deploy packages/deployment-templates/modal/app.py
```

The template ships a POST web endpoint that runs a `ReActAgent` and returns the typed trace. Includes prompt-injection scanning before dispatch.

## Vercel

```bash
vercel --prod \
  -e ANTHROPIC_API_KEY=@anthropic_api_key
```

Single Python serverless function at `/api/chat`. Pair with a Next.js frontend in the same project (the `apps/demo` AgentLab is wired this way).

## Railway

1. Create a new Railway project.
2. Add a Postgres plugin.
3. Set service env: `ANTHROPIC_API_KEY`, `DATABASE_URL` (auto-injected by the plugin).
4. Push — Railway auto-detects `Dockerfile` and `railway.json`.

`railway.json` includes a `/health` healthcheck and exponential-backoff restart on failure. Wire the healthcheck into your FastAPI app:

```python
@app.get("/health")
def health() -> dict:
    return {"ok": True}
```

## Choosing between them

- **Single-page chatbot, occasional traffic** → Vercel.
- **Long-running tool use, large context** → Modal (no request timeout).
- **Want a managed DB on day one** → Railway.
- **Want to develop offline** → Docker Compose.

Templates intentionally don't include secrets rotation, autoscaling tuning, or CDN config — those depend on your traffic profile and observability stack. Add them after you have real load.
