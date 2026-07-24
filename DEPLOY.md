# Deploy runbook

End-to-end instructions for taking `csk` from "GitHub repo" to "live SaaS at briefing.so." Walk it once, top to bottom.

## What you'll need

- A domain (Namecheap, Cloudflare Registrar, ~$12/yr).
- Accounts: GitHub (you have it), Railway, Vercel, Stripe, Slack, Linear.
- Cards & emails handy.

Estimated total time: 1–2 hours, the first time.

---

## 1. Backend on Railway (`apps/api`) — 20 min

1. Sign up at https://railway.com if you haven't.
2. New project → "Deploy from GitHub repo" → select `Ronin`.
3. In the service settings, set the **Root Directory** to `apps/api`.
4. Add a **Postgres** plugin to the project. Railway injects `DATABASE_URL` automatically.
5. Set service variables (Settings → Variables):
   ```
   FERNET_KEY            <run `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` locally and paste the output>
   ```
6. Add a **second service** in the same project for the worker:
   - Same repo, same root dir
   - Override start command: `python -m csk_api.worker --interval 300`
   - Same env vars.
7. After both deploy, copy the public URL of the API service (e.g. `https://csk-api-production.up.railway.app`).

Smoke test:
```bash
curl https://csk-api-production.up.railway.app/health
# {"ok":true,"version":"0.0.1"}
```

## 2. Frontend on Vercel (`apps/web`) — 10 min

1. Sign up at https://vercel.com.
2. "Import Project" → pick `Ronin`.
3. **Root Directory**: `apps/web`.
4. **Framework Preset**: Next.js (auto-detected).
5. Environment variables:
   ```
   NEXT_PUBLIC_API_URL   https://csk-api-production.up.railway.app   (the Railway URL from step 1)
   ```
6. Deploy.

Smoke test: open the Vercel URL. The landing page should render. Click **Sign in**, enter your email, hit **Continue** — you should land on `/dashboard`.

## 3. Domain — 15 min

1. Buy `briefing.so` (or your pick) at Cloudflare Registrar or Namecheap.
2. In Vercel, add the domain to your project. Vercel shows the DNS records you need.
3. At your registrar, set the DNS to those records (an `A` record + a `CNAME`).
4. Wait 5-30 min for propagation. Then `https://briefing.so` serves the frontend.
5. For the API, add `api.briefing.so` as a custom domain in Railway and set a `CNAME` at your registrar.
6. Update `NEXT_PUBLIC_API_URL` in Vercel to `https://api.briefing.so` and redeploy.

## 4. OAuth apps — 30 min

For each provider, register an app in their developer console with our callback URL.

### Slack — https://api.slack.com/apps
- "Create New App" → "From scratch" → name "csk briefing"
- OAuth & Permissions:
  - **Redirect URL**: `https://api.briefing.so/oauth/slack_bot/callback`
  - **Bot Token Scopes**: `chat:write`, `channels:read`, `channels:history`, `users:read`
- Note the Client ID + Client Secret.
- In Railway, set:
  ```
  SLACK_CLIENT_ID         <from Slack>
  SLACK_CLIENT_SECRET     <from Slack>
  SLACK_REDIRECT_URI      https://api.briefing.so/oauth/slack_bot/callback
  ```

### Linear — https://linear.app/settings/api/applications/new
- Name: "csk briefing"
- **Callback URL**: `https://api.briefing.so/oauth/linear/callback`
- Scope: `read`
- Note Client ID + Client Secret.
- Railway:
  ```
  LINEAR_CLIENT_ID
  LINEAR_CLIENT_SECRET
  LINEAR_REDIRECT_URI     https://api.briefing.so/oauth/linear/callback
  ```

### Stripe — https://dashboard.stripe.com/settings/connect/onboarding-options
- Enable Stripe Connect (OAuth).
- Set **Redirect URIs** → `https://api.briefing.so/oauth/stripe/callback`.
- Note Client ID. Use your existing Stripe secret key for `STRIPE_SECRET_KEY`.
- Railway:
  ```
  STRIPE_CLIENT_ID
  STRIPE_SECRET_KEY
  STRIPE_REDIRECT_URI     https://api.briefing.so/oauth/stripe/callback
  ```

## 5. Stripe Billing — 20 min

1. Stripe Dashboard → Products → Add Product:
   - "csk Pro" — recurring, $19/month → copy the **price ID** (`price_xxx`)
   - "csk Team" — recurring, $99/month → copy the price ID
2. Set Railway env vars:
   ```
   STRIPE_PRICE_PRO        price_xxx
   STRIPE_PRICE_TEAM       price_xxx
   STRIPE_SUCCESS_URL      https://briefing.so/dashboard?billing=ok
   STRIPE_CANCEL_URL       https://briefing.so/dashboard?billing=cancel
   ```
3. Dashboard → Developers → Webhooks → "Add endpoint":
   - **URL**: `https://api.briefing.so/webhooks/stripe`
   - **Events**: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
   - Note the signing secret (`whsec_xxx`).
4. Railway:
   ```
   STRIPE_WEBHOOK_SECRET   whsec_xxx
   ```

Test in Stripe test mode first; flip to live keys after one real test transaction.

## 6. PyPI for `pip install ronin-cli` — 5 min

(Independent of the SaaS — for users who want the local CLI.)

1. https://pypi.org/account/register/ → sign up + verify email.
2. https://pypi.org/manage/account/token/ → create token "entire account" scope.
3. In your local terminal:
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   gh secret set PYPI_TOKEN   # paste the pypi-... token
   gh workflow run release.yml --ref v0.2.0
   gh run watch
   ```
4. Verify: open a fresh shell, run `pipx install ronin-cli && ronin briefing`.

## 7. Smoke test the live SaaS — 5 min

```bash
# 1. Sign up
TOKEN=$(curl -s -X POST https://api.briefing.so/signup \
  -H 'Content-Type: application/json' -d '{"email":"you@yourdomain.com"}' | jq -r .api_token)

# 2. OAuth-connect Stripe (in a browser)
open "https://api.briefing.so/oauth/stripe/start" -H "Authorization: Bearer $TOKEN"
# follow the redirect, approve, you get bounced back with a connection saved

# 3. Run a briefing
curl -X POST https://api.briefing.so/briefings \
  -H "Authorization: Bearer $TOKEN" | jq -r .markdown
```

If that prints a real Markdown briefing about your data — you've shipped a SaaS.

## After launch

- **Add monitoring**. Railway has built-in basic metrics; pair with Sentry (5 min setup) for errors.
- **Migrate to Alembic** when the schema changes for the first time. Right now we use `Base.metadata.create_all` which is fine for additive changes only.
- **Rate limit** the API (FastAPI middleware + Redis) once you have >10 paying users.
- **Replace localStorage auth** with Clerk or Auth.js once you have real users (currently the API token sits in browser storage — fine for an MVP, not for scale).

## Common breakage

- **CORS errors in the browser**: add `https://briefing.so` to the FastAPI CORS allowlist in `apps/api/csk_api/main.py` (you'll need to add `fastapi.middleware.cors.CORSMiddleware`).
- **Encrypted column errors after rotating FERNET_KEY**: write a one-off migration that decrypts with the old key and re-encrypts with the new one. Or wipe + ask users to reconnect (acceptable for <100 users).
- **Stripe webhook signature failures**: double-check `STRIPE_WEBHOOK_SECRET` matches the endpoint, and that the timestamp tolerance (5 min in our code) hasn't expired during retry storms.
- **Worker not running briefings**: confirm the second Railway service is up and its env vars match the API service.

## What this doesn't include yet

- Sending the briefing via **email** (we have Slack today; SendGrid/Resend is a 1-hour add).
- **Multi-tenant teams** (multiple users in one workspace).
- Self-serve **plan switching** in the dashboard (you currently land on Stripe Checkout, but downgrading is manual).

Each is a 1-day feature when there's user demand for it.
