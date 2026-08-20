# Planner Cache

`PlanCache` lets `PlannerExecutorAgent` reuse an initial structured plan when
the same normalized task is run against the same repository state. It is an
opt-in library feature; creating a planner without `plan_cache=PlanCache(root)`
does not read or write persistent state.

## Cache Boundary

Entries are stored beneath `<root>/.ronin/plans/`. The key combines:

- A SHA-256 digest of the task after whitespace normalization.
- A repository fingerprint of the current Git `HEAD` and working-tree status.
- A bounded filesystem fingerprint when the root is not a Git checkout.

The cache record stores digests, the repository fingerprint, a schema version,
and the structured `{goal, steps}` output. It never stores the raw task prompt.
The plan itself can naturally contain task-relevant information, so callers
should keep `.ronin/` local or exclude it from source control.

## Lifecycle

1. The planner computes the current repository fingerprint and looks up the
   task key.
2. A valid match is a `cache=hit` plan trace step and skips the planner-model
   request.
3. A miss calls the planner, validates its tagged JSON, and atomically records
   the result before the executor begins.
4. A changed revision or working tree changes the lookup key, so stale plans
   cannot match. Failed-execution replans always bypass the cache because their
   failure context is new planner input.

Cache writes use `os.replace`, so readers never observe a half-written JSON
record. Corrupt or incompatible records are treated as misses. Retention is
bounded (`max_entries=128` by default), and cache write failures do not fail an
agent run.

## Limits

The cache does not judge whether a cached plan is still a good idea; it only
ensures the repository and task fingerprints match. It is intentionally not a
cross-repository memory system, does not cache final answers or tool outputs,
and does not alter approval or tool-safety behavior.
