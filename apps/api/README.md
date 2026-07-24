# apps/api — hosted csk SaaS

The backend behind a hosted version of `csk briefing`. Users sign up, upload their Stripe/Linear/Slack credentials (encrypted at rest), and get briefings on a cron without ever touching a terminal.

## What's here

| | |
|---|---|
| `app/main.py` | FastAPI surface — `/signup`, `/connections`, `/briefings`, `/briefings/schedule`, `/health` |
| `app/db.py` | SQLAlchemy 2.0 models — users, service_connections (encrypted), briefing_runs, schedules |
| `app/crypto.py` | Fernet (AES-128-CBC + HMAC-SHA256) wrapper for stored credentials. Token hashes via SHA-256. |
| `app/services.py` | Business logic — orchestrates the CLI's briefing engine (`compute_briefing_data` + `render_briefing_md`) per user |
| `app/worker.py` | Standalone process: polls for due users and runs their briefings |
| `app/config.py` | env-driven settings via pydantic-settings |
| `tests/` | 12 tests — auth, connections, briefings, history, schedule, worker tick |

## Architecture in one diagram

```
                ┌────────────┐
                │  Browser   │
                │  (future)  │
                └─────┬──────┘
                      │  HTTPS · Bearer csk_XXX
                      ▼
┌─────────────────────────────────────────────┐
│  FastAPI (uvicorn)                          │
│  ┌───────────┐  ┌──────────────┐            │
│  │ /signup   │  │ /connections │  …         │
│  └─────┬─────┘  └──────┬───────┘            │
└────────┼───────────────┼────────────────────┘
         │               │
         ▼               ▼
┌──────────────────────────────────┐
│  Postgres (sqlite in dev)        │
│  users / connections (encrypted) │
│  briefing_runs · schedules       │
└──────────────────────────────────┘
         ▲
         │
┌────────┴─────────┐
│  Worker (cron)   │  polls 'due' users every N min,
│  python -m       │  decrypts their creds, runs the
│  app.worker      │  same briefing engine as the CLI
└──────────────────┘
```

## Run locally

```bash
# from repo root
uv sync --all-packages --all-groups

# API
uv run uvicorn app.main:app --reload --port 8000 --app-dir apps/api

# Worker (separate terminal)
uv --project apps/api run python -m app.worker --interval 60
```

## Web dashboard (ronin ui)

This app also serves the ronin web dashboard. It is a single self-contained HTML
page (inline CSS, vanilla JS, no external resource URLs, fully offline) plus a
few read-only JSON endpoints that surface REAL data ronin wrote under `.ronin/`:

| Route | Returns |
|---|---|
| `GET /` | The self-contained dashboard HTML page |
| `GET /ui/runs` | Recent runs: orchestrations and chat sessions, newest first |
| `GET /ui/runs/{id}` | One run's detail: planner -> sub-agent tree, faithfulness scores, output (or a session transcript) |
| `GET /ui/memory` | Durable memory entries (`~/.ronin/memory.json`) |
| `GET /ui/skills` | Crystallized skills (`.ronin/commands/*.md`) |

The dashboard code lives in `csk_api/dashboard.py` (read-only data layer) and
`csk_api/dashboard_html.py` (the page). The endpoints honour `RONIN_HOME` and the
current working directory exactly as the CLI does, so tests point a temp `.ronin`
home at them. Launch it with `ronin ui` (serves on localhost and opens a
browser) or:

```bash
uv run uvicorn csk_api.main:app --port 8765 --app-dir apps/api
curl -s http://127.0.0.1:8765/ | head      # the page
curl -s http://127.0.0.1:8765/ui/runs      # recent runs (JSON)
```

## End-to-end smoke

```bash
# 1. Sign up — copy the api_token; you only see it once
TOKEN=$(curl -s -X POST http://localhost:8000/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"founder@startup.io"}' | jq -r .api_token)

# 2. Upload credentials (server encrypts them at rest)
curl -X POST http://localhost:8000/connections \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"service":"stripe","secret":"rk_live_..."}'

curl -X POST http://localhost:8000/connections \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"service":"linear","secret":"lin_api_..."}'

# 3. Run a briefing now
curl -X POST http://localhost:8000/briefings \
  -H "Authorization: Bearer $TOKEN" | jq .markdown -r

# 4. Schedule it
curl -X POST http://localhost:8000/briefings/schedule \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"cron":"0 9 * * 1","slack_channel":"#founders","enabled":true}'
```

## Configuration

| env var | default | purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./csk_saas.db` | Use a Postgres URI in prod. |
| `FERNET_KEY` | dev placeholder | **Generate one in prod**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotating it requires re-encrypting existing rows. |
| `API_TOKEN_BYTES` | 24 | Per-user API token length in bytes. |
| `ENABLE_SCHEDULER` | `true` | Worker no-op switch. |

## Deploy

This dir ships a `Dockerfile` + `railway.json`. On Railway:

1. Create a project, point it at this directory.
2. Add a Postgres plugin → `DATABASE_URL` is injected automatically.
3. Set `FERNET_KEY` as a service variable.
4. Optionally add a second service running `python -m app.worker` against the same Postgres.

Cost at low scale: ~$5/mo (Railway Postgres + 1 web service).

## Tests

```bash
uv run pytest apps/api -q
```

No network, no real credentials — sqlite-backed, briefings run against the CLI's demo dataset semantics.

## What's intentionally NOT in this MVP

- **OAuth flows** for Stripe/Linear/Slack. Users currently paste their raw API keys (same as the CLI). OAuth is a follow-up — needs registering apps in each provider's developer console.
- **Frontend**. There's no React app yet. Hit the endpoints with curl, or build a Next.js app at `apps/web/` that consumes them.
- **Email** for the per-user API token. Currently returned in the signup response, which is fine for the API-first phase.
- **Real OAuth-based session auth**. Bearer tokens are sufficient for API consumption; add Clerk/Auth.js when the frontend lands.
- **Rate limits**. Add via FastAPI middleware + Redis when traffic justifies.
