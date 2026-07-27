<!-- Thanks for the PR. Fill out everything below. Evidence beats assertion. -->

## Summary

<!-- One paragraph: what changed? -->

## Motivation

<!-- Why is this needed now? Link the issue or roadmap area when possible. -->

## Design

<!-- Walk through the approach. Mention anything subtle or non-obvious. -->

## Tests

<!-- Include exact commands and results. If something was not run, say NOT-RUN and why. -->

- [ ] Added / updated tests covering the change
- [ ] `uv run pytest packages/agent-patterns packages/eval-suite packages/memory packages/hardening packages/mcp-servers packages/cli apps/demo -q` passes locally
- [ ] (If user-facing) Tried it in the CLI / demo app and confirmed the output is right

## Documentation

<!-- Docs, README, CLI help, examples, changelog, and migration notes. Use N/A only when truly not applicable. -->

- [ ] Docs updated
- [ ] CHANGELOG updated if user-facing
- [ ] Migration notes included if persisted state, config, CLI flags, or public APIs changed

## Risks

<!-- Safety, compatibility, performance, reliability, and release risks. -->

## Rollback plan

<!-- How can this be reverted, disabled, or safely rolled back? -->

## Checklist

- [ ] No vendor SDK added as a hard dependency (use `httpx` + an optional extra if needed)
- [ ] No write paths added to MCP servers (gate via `ApprovalGate` in user code instead)
- [ ] Destructive/payment approval floor is not weakened
- [ ] No secrets, credentials, caches, or `.env*` files committed
- [ ] Commit message is scoped and imperative (`feat(agent): add planner cache`)

## Related issues

<!-- Closes #XX, refs #YY -->
