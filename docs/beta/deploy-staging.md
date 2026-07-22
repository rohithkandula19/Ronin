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

## Backend (`apps/api`) — BLOCKED, needs real work + secrets

Status: **BLOCKED_INFRASTRUCTURE + BLOCKED_CREDENTIALS**, and one code gap.

- **Persistence is JSON-file today** (`ModelRegistry("models.json")`,
  `VaultStore("vault.json")`, `ArtifactStore("artifacts.json")`, …) behind
  swappable interfaces. There is **no SQL schema / migration** yet, so a managed
  Postgres (e.g. Supabase) cannot be wired in without first implementing a
  Postgres-backed store for those interfaces. **Not creating a Supabase project
  until that store exists** — it would be a dangling empty DB.
- **Compute host**: `apps/api/Dockerfile` exists; the FastAPI app needs a
  container host (Fly / Railway / Render). Vercel serverless is not a fit for
  the uv-workspace app as-is.
- **Secrets required** before boot (enforced by `ronin_platform.envcheck`):
  `secret_key`, `encryption_key` (non-default), a provider API key, non-`*`
  CORS, a real DB URL once the store exists, and a monthly cost ceiling.
  `envcheck` fail-closes on an unsafe prod/staging config.

### Backend activation order (when you want it live)

1. Implement a Postgres-backed store for the registry/vault/artifact/etc.
   interfaces (or a durable SQLite volume for a first trusted-tester staging).
2. Provision Postgres (Supabase free tier is $0) + run the (new) schema.
3. Host `apps/api` (Docker) with the required secrets; point the frontend's
   `NEXT_PUBLIC_API_URL` at it.
4. Confirm `check_environment("production"|"staging", cfg)` returns no errors.
5. Wire one provider key into the inference gateway; set per-user/org/provider
   quotas and the platform monthly ceiling before exposing the cohort.

## What is intentionally NOT done here

- No production deploy. No paid infra provisioned. No secrets committed.
- Live payments remain disabled by design; healthcare stays non-diagnostic;
  legal docs remain DRAFT_REQUIRES_LEGAL_REVIEW.
