# ronin — v1.0 Evaluation & Benchmark Report

**Version:** 0.59.0 · **Run:** 2026-07-01 · **Environment:** macOS (Darwin 25.5.0), Python 3.14, `uv run --frozen`.

Honesty rules for this report: measured results are labeled **RUN**; anything not executed is labeled **NOT RUN** or **SKIPPED** with the reason. No resolved-rate or benchmark number is published unless it was actually measured here.

## 1. Test suite — RUN ✅

Full repository test suite (all packages + apps), offline, no API keys (a `FakeProvider` makes agent tests deterministic):

```
uv run --frozen pytest packages apps -q
→ 3274 passed, 45 warnings in 44.73s
```

Per-area collected counts:

| Area | Tests |
|---|---|
| `packages/cli` | 2812 |
| `packages/agent-patterns` | 188 |
| `packages/hardening` | 60 |
| `packages/mcp-servers` | 67 |
| `packages/eval-suite` | 45 |
| `packages/relay` | 35 |
| `packages/memory` | 11 |
| `apps/api` | 51 |
| `apps/demo` | 5 |
| **Total** | **3274** |

All green. CI runs the same suite on Python 3.12 on every push.

## 2. Eval suite — HARNESS PRESENT (no scored run here)

`packages/eval-suite` ships an LLM-as-a-judge eval harness **and** a SWE-bench **execution harness** (golden datasets, drift detection, HTML reports). Its 45 tests pass offline.

- **SWE-bench:** the harness exists and is tested, but **no resolved-rate was measured** in this environment (requires a provider key + the SWE-bench dataset). **Status: NOT RUN.** ronin publishes no SWE-bench score.
- **`docs/BENCHMARK.md`** contains a scoped, reproducible 6-task battery (a small illustrative eval, labeled as such, with an explicit note that free-tier rate limits confound timing). It is illustrative, not a headline benchmark.

## 3. Pipeline dry-run verification — RUN ✅

```
uv run --frozen ronin pipeline "explain this repo" --dry-run
→ shows the architect→implementer→reviewer→tester→verifier plan, per-stage
  permissions, read-only mode, and the resolved brain/badge. Nothing runs, exit 0.
```

The pipeline's verification layer (independent verify, contract checks, semantic contract, multi-suite required/optional, diff evidence, resume/restore) is covered by the CLI test suite above.

## 4. Free-provider smoke — SKIPPED (no key in this environment)

Running a real turn on Cerebras/Groq/Gemini/OpenRouter needs a provider key, which is **not present** in this environment. **Status: SKIPPED — no API key.** Free-first routing, cost/free classification, and `apply_free` are covered by unit tests (`test_status.py`, `test_pipeline.py`).

## 5. Offline / Ollama smoke — SKIPPED (no local server)

A real offline turn needs a running Ollama server (or the keyless in-process `local` brain, which downloads a model on first use). Neither was exercised here. **Status: SKIPPED — no Ollama daemon.** `apply_offline` (forces a local brain, strips network tools) is unit-tested.

## 6. Latency / cost notes

- **Test-suite wall time:** ~45s for 3,274 tests (offline, no network).
- **Per-turn latency/cost:** provider-dependent; not measured here (no keys). ronin's cost ledger tracks $0 free turns vs a strong-model baseline at runtime.

## Summary

| Item | Status |
|---|---|
| Full test suite (3,274) | **RUN — all passed** |
| eval-suite tests (45) | **RUN — passed** |
| SWE-bench resolved-rate | **NOT RUN** (no score published) |
| Pipeline `--dry-run` | **RUN — works** |
| Free-provider live turn | **SKIPPED** (no key) |
| Offline/Ollama live turn | **SKIPPED** (no daemon) |

**Bottom line:** the code is proven by 3,274 offline tests; live provider/offline smokes and a SWE-bench score were not measured here and are explicitly not claimed.
