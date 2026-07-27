# Performance Benchmarks

`ronin perf` measures a local command repeatedly and records a structured report
for later comparison. It is for startup, repository scan, test, and build timing;
it does not measure model quality or replace `ronin eval`.

```bash
ronin perf "uv run pytest packages/cli -q" --runs 7 --warmup 2 \
  --json-out .ronin/benchmarks/cli-tests.json

ronin perf "uv run pytest packages/cli -q" --runs 7 --warmup 2 \
  --baseline .ronin/benchmarks/cli-tests.json \
  --max-regression-percent 10
```

Each report records the command, working root, run configuration, per-run
durations and exits, summary statistics (mean, median, p95, standard deviation),
and basic environment facts. Captured command output is never persisted in a
report. Reports are written atomically, so an interrupted benchmark never
leaves partial JSON.

## Comparison Policy

Ronin compares timed-run medians. A candidate is marked `regressed` when it has
more failed runs than the baseline, or when its median rises above
`--max-regression-percent` (10% by default). An improvement is reported, but
does not prove a universal speedup.

Compare reports only on comparable hardware, operating-system load, repository
state, dependencies, and command arguments. The report preserves those inputs
for review, but timing noise remains real; use several warmups and timed runs
before treating a small delta as a release blocker.

The legacy `ronin perf` terminal summary remains available. `--json-out` and
`--baseline` are additive, so existing scripts can adopt persisted reports when
they are ready.
