# Ronin AI OS — local development entry points.
# Zero-cost, local-first: no paid APIs required for dev or the test path.

.PHONY: help install run eval dev test test-frontend lint typecheck coverage security verify e2e clean

help:
	@echo "Ronin AI OS make targets:"
	@echo "  make install      - sync every package + dev groups, install pre-commit hooks"
	@echo "  make run          - run the agent (ronin2, the v2 tree)"
	@echo "  make eval         - the eval suite, --dry-run by default (no model, no cost)"
	@echo "  make test         - backend test suite (uv, all packages + tests/)"
	@echo "  make lint         - ruff over the configured tree"
	@echo "  make typecheck    - mypy --strict over the configured tree"
	@echo "  make dev          - print how to run API + web locally"
	@echo "  make test-frontend- frontend logic tests (node --test, no install)"
	@echo "  make verify       - backend + frontend tests + secret scan"
	@echo "  make security     - secret scan of the working tree"

# `--all-groups` is what pulls in mypy/ruff/pytest-cov; without it `make lint` and
# `make typecheck` fail on a fresh clone with a confusing "command not found".
install:
	uv sync --all-packages --all-groups
	@# pre-commit is optional tooling, not a build dependency: a clone that cannot reach
	@# github should still install and test. So this is best-effort and says what it did.
	@uv tool install --quiet pre-commit 2>/dev/null && pre-commit install \
		&& echo "pre-commit hooks installed" \
		|| echo "note: pre-commit not installed (offline?); run 'pre-commit install' later"

# The v2 agent. `ronin2` works in a dev checkout only because this change added a
# `[build-system]` and `tool.uv.package = true` to the root pyproject — before that
# `uv sync` skipped `[project.scripts]` entirely and the venv had `ronin`, `ronin-eval`
# and `ronin-relay` but no `ronin2` at all.
#
# ARGS passes a prompt through: make run ARGS='"fix the failing test"'
run:
	uv run ronin2 $(ARGS)

# Dry-run by default: selects and prints the tasks without calling a model, so it costs
# nothing and needs no key. `make eval ARGS='--regression-gate'` to narrow it.
eval:
	uv run ronin2 eval --dry-run $(ARGS)

dev:
	@echo "1) API:  uv run uvicorn csk_api.main:app --app-dir apps/api --reload --port 8000"
	@echo "2) Web:  cd apps/web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev"
	@echo "   (web needs a one-time 'npm install'; the backend + tests need no install beyond 'uv sync')"
	@echo "3) World Navigator: http://localhost:3000/worlds"

# `tests` is in the list because it was NOT, and that meant `make test` ran the v1
# packages while skipping the entire v2 tree the eval suite measures — 3122 tests that
# CI runs and the Makefile did not. This now matches ci.yml exactly.
test:
	uv run --frozen pytest packages apps training tests -q

test-frontend:
	cd apps/web && node --test lib/*.test.mjs

# These two were `@echo` stubs that printed advice and exited 0, so `make lint` "passed"
# without linting anything. They run the real tools now, over the same scope as CI.
lint:
	uv run ruff check src/ronin tests scripts
	@echo "frontend: cd apps/web && npm run lint"

typecheck:
	uv run mypy
	@echo "frontend: cd apps/web && npx tsc --noEmit (needs npm install)"

# The same gate CI enforces: 85% over all of src/ronin, threshold in pyproject.toml so
# there is one number rather than one per caller. Only `tests` is collected because that
# is the suite exercising src/ronin; adding the v1 packages would dilute the measurement
# with code this gate does not cover.
coverage:
	uv run pytest tests -q --cov --cov-report=term-missing

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
	uv run --frozen pytest packages/platform -q
	@echo "--- beta platform packages (identity/storage/billing/jobs/observability/support) ---"
	uv run --frozen pytest packages/identity packages/storage packages/billing \
		packages/jobs packages/observability packages/support -q
	@echo "verify-public-beta: local scope complete (staging/deploy = BLOCKED_CREDENTIALS)"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
