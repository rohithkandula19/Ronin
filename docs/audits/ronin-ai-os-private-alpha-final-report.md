# Ronin AI OS — private-alpha hardening: final report

Branch `feat/ronin-ai-os-alpha-hardening` (from `feat/ronin-ai-os-foundation`).
Honesty labels per the program's scheme. Evidence = code that ran here.

## Executive summary

- **Genuinely verified:** the safety-critical invariant — a web action cannot
  bypass the destructive floor — now has executable proof; Education and
  Healthcare world workflows run end-to-end producing real, grounded, versioned
  artifacts; cross-industry memory isolation, training-disabled-by-default, and
  project deletion are proven through the HTTP API; Artifacts, Research/citations,
  and Tasks are real, tested subsystems.
- **Repaired:** a pytest basename collision that broke the full-suite run;
  request-model resolution for the new endpoints.
- **Blocked (honestly):** interactive model-driven agent runs
  (BLOCKED_CREDENTIALS), web `next build`/DOM-e2e/screenshots
  (BLOCKED_INFRASTRUCTURE — no `npm install`), `ronin-code-v1` training
  (BLOCKED_HARDWARE — no GPU; bundles GENERATED only).
- **Private-alpha gates:** the local/offline-testable gates pass; the gates
  requiring a provider, a browser build, or a GPU are blocked and labeled, not
  faked.

## Baseline vs final

| Metric | Baseline | Final |
|---|---|---|
| Backend tests | 3,642 | **3,764 passed**, 6 skipped, 0 failed |
| Frontend tests | 8 | 8 passed (`node --test`) |
| New packages | — | artifacts, research, tasks (+ prior industry-sdk, vault) |
| Safety-floor bypass test | none | 8 invariant tests, real runtime primitives |
| TODO/FIXME in new source | 0 | 0 |
| `csk` in web chrome | 0 | 0 |
| Secrets in new source | 0 | 0 (GitGuardian green on foundation PR) |

## Product status (honesty labels)

| Product | Status | Evidence |
|---|---|---|
| Ronin Core (runtime) | VERIFIED (preserved) | full suite green; web reuses real floor/tools by identity |
| Ronin Worlds (SDK + packs) | VERIFIED | SDK + repo-pack + eval-gate tests |
| Ronin Code — safety seam | VERIFIED | `test_v1_coding_safety.py`: floor not waivable, read-only can't write |
| Ronin Code — interactive agent loop | BLOCKED_CREDENTIALS | needs a provider key; decision layer proven, model loop not run |
| Education World | IMPLEMENTED | study-plan flow → grounded artifact; flow test B |
| Healthcare Information World | IMPLEMENTED | summary flow with disclosures + isolation + deletion; flow test C; refuses diagnosis |
| Ronin Forge | IMPLEMENTED | provenance/redaction/quality/bundle/job tests; generated≠trained |
| Ronin Vault | VERIFIED | isolation + consent tests at unit and HTTP level |
| Ronin Research | IMPLEMENTED | notebook: no fabricated URLs, no dangling cites, inference labeled |
| Artifacts | IMPLEMENTED | structured, versioned, compare/restore, provenance links |
| Tasks | IMPLEMENTED | state machine + scheduler; no silent high-risk |
| Developer Platform | NOT_IMPLEMENTED | next stage |

## Fine-tuning status

- Dataset pipeline: IMPLEMENTED (provenance-gated, redaction-reviewed).
- Training-bundle generation: IMPLEMENTED (complete runnable QLoRA bundle).
- Actual training run: **did not occur** — BLOCKED_HARDWARE (no GPU here).
- `ronin-code-v1` adapter: **does not exist** as trained weights. The only
  adapter with real evidence remains the prior v2 iter-150 (36/91, committed
  report), registered as `completed`, **not released**.

## Security status

- Critical repaired: web-cannot-bypass-floor invariant now enforced server-side
  and tested (import-identity drift guard included).
- Auth: pbkdf2, hashed tokens, no account enumeration (tested).
- Isolation: cross-owner (404) and cross-industry (no leak) tested via HTTP.
- Accepted/known: dev `FERNET_KEY` default in legacy `config.py` (must be set in
  any deployment); full web XSS/CSRF/DOM red-team is BLOCKED_INFRASTRUCTURE
  (no browser build) — token auth (no cookies) sidesteps CSRF on the v1 path.

## Test evidence (commands run here)

- `uv run --frozen pytest packages apps training -q` → 3,764 passed, 6 skipped.
- `uv run --frozen pytest apps/api -q` → 73 passed.
- `node --test apps/web/lib/*.test.mjs` → 8 passed.
- Live: `is_floored_tool_call('run_command',{'command':'rm -rf /'})` → True.

## Files & commits (this branch)

Commits: artifact store; reality-audit baseline + safety seam; research;
tasks; education/healthcare workflows + demo; test rename. New packages:
`packages/{artifacts,research,tasks}`; new API modules
`apps/api/csk_api/v1/{coding_runtime,workflows,demo}.py`; audit docs.

## Remaining work

- **Before private alpha:** interactive Coding agent loop wired to
  `run_code_agent(gate_cb=build_gate_cb(...))` with a real provider key
  (BLOCKED_CREDENTIALS); web build + DOM/e2e + screenshots (BLOCKED_INFRASTRUCTURE).
- **Before public beta:** Developer Platform, full web security red-team,
  Alembic migrations, org/RBAC depth.
- **Before production:** `ronin-code-v1` real training + locked-eval gates
  (BLOCKED_HARDWARE); regulated review for Healthcare.

## Run instructions

```bash
uv sync --all-packages --all-groups
uv run --frozen pytest packages apps training -q      # backend
(cd apps/web && node --test lib/*.test.mjs)           # frontend logic
make verify                                           # backend + frontend + secret scan
# API locally:
uv run uvicorn csk_api.main:app --app-dir apps/api --port 8000
# then GET /api/v1/worlds, /api/v1/demo ; POST /api/v1/education/study-plan
```
