# Deploying Ronin AI OS to staging

Honest status of what deploys today, what's blocked, and the exact steps. No
part of this has been deployed from the build environment — it documents the
activation path. Labels follow the program scheme.

## Frontend (`apps/web`) — READY, one action from you

`apps/web` is standalone-buildable and verified: `pnpm exec next build` → exit 0,
all 14 routes prerender (React 19 / Next 16 / Tailwind 4). It runs in
**demo/offline mode** with no backend (API calls fall back to
`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`, and the UI degrades to
labeled demo fixtures).

**Vercel is connected** (team `rohithkandula19's projects`, hobby = $0). Deploy
via **git import** (the reliable path; the MCP file-tree deploy can't carry this
35-file / ~264 KB tree reliably):

1. Vercel → **Add New… → Project** → import `rohithkandula19/ronin`
2. **Root Directory: `apps/web`** · Framework: **Next.js** (auto) · Build: `next build`
3. Env: none required for demo mode. Later set `NEXT_PUBLIC_API_URL` to the
   deployed backend URL.
4. **Deploy** (target = Preview for staging, $0, non-production).

CLI equivalent: `vercel --cwd apps/web` (Preview) with a Vercel token.

Once triggered, build failures can be diagnosed from Vercel build logs.

### Connecting the frontend to a running backend

The Ronin OS worlds (`/os/*`) read real data from `/api/v1` and fall back to a
labelled offline sample when no backend is reachable (the default). Two ways to
connect them to a hosted `apps/api`:

1. **Same-origin proxy (recommended — no CORS).** Set **`RONIN_API_ORIGIN`** to
   the backend base URL (e.g. `https://api.example.com`) as a **build-time** env
   var (Next bakes `rewrites()` into the build). `apps/web` then proxies
   `/api/v1/*` to that backend, and the browser only ever calls its own origin.
2. **Direct cross-origin.** Set **`NEXT_PUBLIC_API_URL`** to the backend URL and
   add the web origin to the backend's **`CORS_ORIGINS`** allowlist
   (comma-separated exact origins; never `*`). The API emits CORS headers only
   for listed origins.

With neither set, the worlds stay in offline/sample mode — correct and honest,
never a fabricated backend.

## Backend (`apps/api`) — DB layer now wireable; host + secrets still needed

Status: **BLOCKED_INFRASTRUCTURE + BLOCKED_CREDENTIALS** (the DB code gap is now
closed).

- **Persistence is now swappable** via `ronin-persistence` (`DocumentStore` with
  in-memory / JSON-file / **PostgreSQL (JSONB)** backends). `apps/api` selects
  the backend from the environment: set **`RONIN_DATABASE_URL`** and all API
  state (models, adapters, vault, artifacts) moves to Postgres — one JSONB row
  per store in a `ronin_documents` table, created on first use. Unset, it stays
  on the byte-identical local JSON files (the default; unchanged for tests).
  Install the driver with the `postgres` extra
  (`pip install 'ronin-persistence[postgres]'`).
- **Provision Postgres**: Supabase free tier is $0. Create a project, take its
  connection string, and set it as `RONIN_DATABASE_URL`. (A live round-trip test
  exists behind `RONIN_TEST_DATABASE_URL` — skipped until a server is supplied.)
- **Compute host** (still needed): `apps/api/Dockerfile` exists; the FastAPI app
  needs a container host (Fly / Railway / Render). Vercel serverless is not a fit
  for the uv-workspace app as-is.
- **Secrets required** before boot (enforced by `ronin_platform.envcheck`):
  `secret_key`, `encryption_key` (non-default), a provider API key, non-`*`
  CORS, `RONIN_DATABASE_URL`, and a monthly cost ceiling. `envcheck`
  fail-closes on an unsafe prod/staging config.

### Backend activation order (when you want it live)

1. Provision Postgres (Supabase free tier is $0); set `RONIN_DATABASE_URL`.
   The `ronin_documents` table is created automatically on first write.
2. Host `apps/api` (Docker) with the required secrets; point the frontend's
   `NEXT_PUBLIC_API_URL` at it.
3. Confirm `check_environment("production"|"staging", cfg)` returns no errors.
4. Wire one provider key into the inference gateway; set per-user/org/provider
   quotas and the platform monthly ceiling before exposing the cohort.

> Migration note: today each store persists its whole collection as one JSONB
> document — simple and correct for beta scale. A future row-per-entity schema
> is a drop-in `DocumentStore`/repository swap when query patterns demand it.

## What is intentionally NOT done here

- No production deploy. No paid infra provisioned. No secrets committed.
- Live payments remain disabled by design; healthcare stays non-diagnostic;
  legal docs remain DRAFT_REQUIRES_LEGAL_REVIEW.
