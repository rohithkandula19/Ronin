# Specialist Agents

Ronin has a scalable catalog of specialist *profiles*, not thousands of
always-running processes. A profile is a narrowly scoped system instruction,
description, tags, and least-privilege tool tier. At orchestration time, Ronin
keeps the core researcher, implementer, reviewer, and tester roles, then selects
only the profiles relevant to the task.

The built-in domain and discipline matrix contains 1,170 generated profiles,
including examples such as `payments-security-auditor`, `python-debugger`, and
`kubernetes-reliability-engineer`. The active team is bounded to 4-32 profiles
(8 by default), so catalog size never becomes prompt bloat, uncontrolled
parallelism, or an unbounded bill.

```bash
ronin agents "audit payment API security"
ronin orchestrate "audit payment API security" --max-agents 8
ronin orchestrate "add retry coverage for the Python client" --write --max-agents 10
```

`--write` keeps all mutating specialists inside an isolated git worktree. The
agent's tier only controls its tool subset; every write and command still uses
Ronin's normal approval and destructive-command policies.

## Project Profiles

Add repository-specific expertise in `.ronin/agents.json`. The file is parsed
as JSON data only; loading a profile never imports or executes code.

```json
{
  "agents": [
    {
      "id": "ledger-reconciler",
      "description": "Reconciles billing ledger invariants",
      "instructions": "Trace ledger invariants and report file-backed evidence.",
      "tags": ["ledger", "billing", "reconciliation"],
      "tier": "test"
    }
  ]
}
```

IDs must be lowercase kebab-case and unique across the core, generated, and
project profiles. `tier` is one of:

- `explore`: repository inspection only.
- `test`: can run existing commands in a read-only orchestration; can write only in an isolated worktree.
- `write`: can propose edits only in an isolated worktree.

Ronin rejects malformed manifests, duplicate IDs, profile text larger than 6,000
characters, manifests over 5 MiB, and manifests over 10,000 profiles before it
calls a model. Pass a different file with
`--agent-manifest path/to/agents.json` when a repository keeps profiles elsewhere.

## Provider Assignment

Every selected profile can be routed to a provider/model with the existing
roster syntax. Profiles not named in `--roster` use the base provider.

```bash
ronin orchestrate "audit payment API security" \
  --roster payments-security-auditor=gemini,implementer=anthropic \
  --max-agents 8
```

The planner sees only the selected profiles, assigns dependency-ordered
subtasks, runs independent work in bounded parallel waves, and then synthesizes
the evidence. This preserves broad specialization with small, reviewable
execution teams.

## Repository-Aware Routing

Selection is not task text alone. Ronin also uses the local repository map's
relevant files and symbols, file language, and root configuration markers such
as `pyproject.toml`, `package.json`, and `Dockerfile`. `ronin agents` shows the
reason for every choice (`task:...`, `repo:...`, or the core workflow role), so
an operator can see why a specialist was invited before any model call.

For workflow contracts, governed execution limits, durable task boards, and the
offline regression suite, see [Agent control plane](agent_control_plane.md).
