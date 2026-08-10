# taskq

A tiny in-process job queue, split so each concern is testable alone.

* `taskq/model.py` — the `Job` record and the `State` enum.
* `taskq/errors.py` — the exception types the package raises.
* `taskq/config.py` — `key=value` config text into a mapping.
* `taskq/policy.py` — retry policy, built from the config.
* `taskq/store.py` — where jobs live; insertion order is `Job.seq`.
* `taskq/scheduler.py` — which job runs next.
* `taskq/runner.py` — runs one job through an injected executor.
* `taskq/format.py` — the human-readable status lines.
* `taskq/cli.py` — argument handling for `bin/taskq`.

The executor is injected everywhere so the queue can be driven in a test
without a subprocess, a socket or a clock.
