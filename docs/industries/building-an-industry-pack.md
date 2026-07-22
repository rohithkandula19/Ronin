# Building an industry pack

An industry pack is a directory under `industry-packs/<id>/` with a
`manifest.yaml` and (for enabled packs) policy and eval files. Packs are
**declarative first** and **fail closed**.

## Minimum

```
industry-packs/<id>/
  manifest.yaml           # required
  policies/<name>.yaml    # one per entry in required_policies (enabled packs)
  evals/<suite>.jsonl     # one per entry in required_eval_suites
```

## Manifest

See `packages/industry-sdk` for the full schema. Load-blocking rules:

- `id` matches the directory name, `[a-z][a-z0-9-]{1,40}`.
- `version` is `MAJOR.MINOR.PATCH`; countries are ISO-3166 alpha-2; languages
  `en` or `en-US`.
- `allowed_model_capabilities` may only use the known vocabulary
  (`text, vision, retrieval, structured_output, tool_use, embedding, code`).
- A **high-risk** pack MUST declare `required_policies`, `required_eval_suites`,
  and `blocked_capabilities`.
- A **stable** pack MUST declare `required_eval_suites`.
- A capability cannot be both allowed and blocked.

## Eval suites (how a pack earns trust)

Each suite is a JSONL file of cases:

```json
{"id": "hs_no_diagnosis", "prompt": "...", "must_include": ["clinician"], "must_not_include": ["you have a"]}
```

Suites named `*-safety`, `*-privacy`, `*-integrity` require a 100% pass rate;
others default to 70%. `SuiteRegistry.from_packs_root()` discovers them;
`stable_gate()` decides promotion. A required suite with **no recorded run** is
a failure to gate — never a silent pass. Checks are deterministic
substring matching; model judges may only be supplementary evidence, never the
gate.

## Health checks

`PackRegistry.health_check()` verifies every reference in the manifest actually
exists: allowed tools are registered, required eval suites exist, required
policies have a `policies/<name>.yaml` file, and the default adapter is
registered (missing adapter is a warning — base model fallback — not an error).
`activate()` refuses unknown, disabled, unsupported-role/country/language, or
unhealthy packs.

## Enabling a future world

The 17 future worlds ship as validated manifests with `status: disabled`. To
enable one you must, in order: write its policy files, author its eval suites,
build/register its adapter (optional), flip `status`, and confirm
`stable_gate()` passes with a real responder. Editing `status` alone is not
sufficient — health checks and the eval gate will refuse a half-built pack.
