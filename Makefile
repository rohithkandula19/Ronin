# Ronin AI OS — local development entry points.
# Zero-cost, local-first: no paid APIs required for dev or the test path.

.PHONY: help dev test test-frontend lint typecheck security verify e2e clean

help:
	@echo "Ronin AI OS make targets:"
	@echo "  make dev          - print how to run API + web locally"
	@echo "  make test         - backend test suite (uv, all packages)"
	@echo "  make test-frontend- frontend logic tests (node --test, no install)"
	@echo "  make verify       - backend + frontend tests + secret scan"
	@echo "  make security     - secret scan of the working tree"

dev:
	@echo "1) API:  uv run uvicorn csk_api.main:app --app-dir apps/api --reload --port 8000"
	@echo "2) Web:  cd apps/web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev"
	@echo "   (web needs a one-time 'npm install'; the backend + tests need no install beyond 'uv sync')"
	@echo "3) World Navigator: http://localhost:3000/worlds"

test:
	uv run --frozen pytest packages apps training -q

test-frontend:
	cd apps/web && node --test lib/*.test.mjs

lint:
	@echo "backend: ruff (if configured); frontend: cd apps/web && npm run lint"

typecheck:
	@echo "frontend typecheck needs npm install: cd apps/web && npx tsc --noEmit"

security:
	@echo "Scanning working tree for obvious secrets..."
	@! git grep -nEI '(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA )?PRIVATE KEY-----)' -- . ':!*.md' ':!docs/*' \
		&& echo "no obvious secrets found" || (echo "POTENTIAL SECRET ABOVE" && exit 1)

verify: test test-frontend security
	@echo "verify complete"

# Public-beta verification. External staging/deploy/GPU/live-billing steps are
# intentionally NOT run here — they are credential/hardware-gated and labeled
# in docs/audits/ronin-ai-os-public-beta-final-report.md, never faked.
verify-public-beta: test test-frontend security
	@echo "--- beta control packages (cost/quota/flags/access/env) ---"
	uv run --frozen pytest packages/inference packages/platform -q
	@echo "--- beta platform packages (identity/storage/billing/jobs/observability/support) ---"
	uv run --frozen pytest packages/identity packages/storage packages/billing \
		packages/jobs packages/observability packages/support -q
	@echo "verify-public-beta: local scope complete (staging/deploy = BLOCKED_CREDENTIALS)"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
