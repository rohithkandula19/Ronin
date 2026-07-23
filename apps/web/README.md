# apps/web — csk SaaS frontend

Next.js 14 (App Router) + Tailwind. Auth-light: paste email → backend issues a one-time API token → stored in `localStorage` → attached as `Bearer` on every fetch.

## Pages

| Path | What |
|---|---|
| `/` | Landing — hero, sample briefing, how-it-works, pricing |
| `/signin` | Email-only signup form |
| `/dashboard` | Connections grid + "Run briefing now" + briefing history |

## Run locally

```bash
cd apps/web
pnpm install
cp .env.example .env.local   # edit if your backend isn't at localhost:8000
pnpm dev
# open http://localhost:3000

# In another terminal — the backend it talks to:
uv run uvicorn csk_api.main:app --reload --port 8000 --app-dir apps/api
```

## Deploy (Vercel)

```bash
cd apps/web
vercel --prod
```

Set `NEXT_PUBLIC_API_URL` to your production backend URL (e.g. the Railway host serving `apps/api`).

## What's intentionally minimal

- No state library — `useState` + a tiny typed fetch client (`lib/api.ts`)
- No UI library — Tailwind utilities + a few CSS component classes in `app/globals.css`
- No auth library — token in `localStorage`. Swap to Clerk/Auth.js when you have real users.
- No tests — frontend is glue around the backend; the backend has 14 tests covering the API.

## What needs your accounts

- A real domain to deploy at
- A Vercel account for hosting
- (Optional) Clerk for "Sign in with Google" upgrades
- (Optional) Stripe Connect / Stripe Billing — backend scaffolding is in `apps/api/csk_api/billing.py`

<!-- staging deploy trigger: ronin-ai-os -->
