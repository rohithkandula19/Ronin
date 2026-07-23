# Deploying the Ronin backend (`apps/api`) live

Honest status: **BLOCKED_INFRASTRUCTURE + BLOCKED_CREDENTIALS** from the build
environment — no Python container host is reachable here and no provider key is
available. This runbook is the exact, verified path to bring it live yourself;
every step has been checked against the real app.

What's already verified (in this repo, no host needed):
- The app **boots and serves** real data: `uvicorn csk_api.main:app` →
  `/health`, `/api/v1/worlds/coding`, `/api/v1/models`,
  `/api/v1/healthcare/limitations` all return 200 with real content.
- A safe production config **passes `envcheck`** (`check_environment("production", …)`
  returns zero errors) — see the contract below.
- `apps/api` is a uv workspace member; the image builds from the repo root via
  `apps/api/Dockerfile` (a lean `.dockerignore` keeps the context small).
- The frontend is ready to point at it (see step 5) — `apps/web` supports both a
  same-origin proxy (`RONIN_API_ORIGIN`) and direct CORS (`NEXT_PUBLIC_API_URL`
  + backend `CORS_ORIGINS`).

## What you must supply

1. A **container host** — Fly.io (config included), Railway
   (`packages/deployment-templates/railway/`), or Render. Vercel serverless is
   **not** a fit for this uv-workspace FastAPI app.
2. A **PostgreSQL** database — Supabase free tier is $0, or Fly Postgres.
3. A **provider API key** (e.g. `ANTHROPIC_API_KEY`) for inference. Without one,
   set `local_only` and run against Ollama instead.

## Environment contract (from `ronin_platform.envcheck`)

| Env var | Purpose | Notes |
| --- | --- | --- |
| `FERNET_KEY` | Encryption key | Must NOT be a dev/default value. Generate one (below). |
| `SECRET_KEY` | Platform secret | Random, non-default. |
| `DATABASE_URL` | SQLAlchemy store (users/sessions/audit) | `postgresql://…` — never sqlite in prod. |
| `RONIN_DATABASE_URL` | Ronin OS state (worlds/vault/artifacts) | Postgres; flips the DocumentStore off local JSON. |
| `CORS_ORIGINS` | Browser allowlist | Comma-separated exact origins; never `*`. |
| `ANTHROPIC_API_KEY` | Provider key | Or another provider; or `local_only`. |
| cost ceiling | Monthly spend cap | Set a platform monthly cost ceiling; unlimited is rejected. |
| `DEBUG` / demo | Off in prod | `envcheck` rejects debug/demo in production. |

Generate the non-default secrets:

```bash
python - <<'PY'
import secrets
from cryptography.fernet import Fernet
print("SECRET_KEY=", secrets.token_urlsafe(32))
print("FERNET_KEY=", Fernet.generate_key().decode())
PY
```

## Deploy (Fly.io example)

```bash
# 0. from the repo root, with flyctl authenticated
# 1. Postgres: Supabase (free) or `fly postgres create`; grab the connection URL.
# 2. Create the app (build context = repo root; config = apps/api/fly.toml):
fly launch --no-deploy --copy-config --dockerfile apps/api/Dockerfile

# 3. Secrets (values never committed):
fly secrets set \
  SECRET_KEY='…' FERNET_KEY='…' \
  DATABASE_URL='postgresql://…' RONIN_DATABASE_URL='postgresql://…' \
  CORS_ORIGINS='https://ronin-ai-os-staging.vercel.app' \
  ANTHROPIC_API_KEY='…'

# 4. Ship it (Fly builds the Dockerfile remotely):
fly deploy -c apps/api/fly.toml

# 5. Verify:
curl https://<your-app>.fly.dev/health
curl https://<your-app>.fly.dev/api/v1/worlds/coding
```

Railway: use `packages/deployment-templates/railway/` (Dockerfile + railway.json)
and set the same env vars in the Railway dashboard.

## Point the live site at it

Pick one (see `deploy-staging.md` for detail):

- **Same-origin proxy (no CORS):** set `RONIN_API_ORIGIN=https://<your-app>.fly.dev`
  as a **build-time** env on the `apps/web` Vercel project, then redeploy. The
  browser calls its own origin; Next proxies `/api/v1/*` to the backend.
- **Direct CORS:** set `NEXT_PUBLIC_API_URL=https://<your-app>.fly.dev` on Vercel
  and include the web origin in the backend's `CORS_ORIGINS`.

Once live, the OS worlds flip from **Offline · sample** to **Live · API**
automatically — no frontend code change.

## Guardrails (unchanged)

Live payments stay disabled; healthcare stays non-diagnostic; legal stays
DRAFT. `envcheck` fail-closes an unsafe prod/staging config, so a missing key or
a `*` CORS will refuse to start rather than boot insecure.
