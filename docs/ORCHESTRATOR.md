# Orchestrator: provider-agnostic multi-agent core

ronin can run a single agent, a dynamic supervisor, a fixed role-team (swarm), or
a model fight (dojo). The orchestrator adds one more shape: take a high-level
goal, decompose it into concrete subtasks, assign each subtask to a specialist
sub-agent, run the independent ones in parallel, and synthesize the results into
one answer.

The honest differentiator is provider-agnostic sub-agents. Each sub-agent can run
on a different vendor's model, because every sub-agent carries its own provider
built through ronin's existing backend selection (the same path the dojo, swarm,
and consensus use). The planner can put research on Claude, bulk implementation
on a fast free model, and review on a third vendor, all in one run.

This is not a claim that nobody has built multi-agent orchestration. Plenty have.
What ronin does is make the per-subtask provider assignment first-class and fully
offline-testable, on top of the same agent loop and worktree isolation the rest
of the project already uses.

The orchestrator now selects from a scalable catalog of 1,170 generated domain
specialist profiles plus optional project profiles. It activates only a bounded,
task-relevant team; see [Specialist agents](agents.md) for the selection model
and `.ronin/agents.json` format.

## Where it fits among ronin's multi-agent primitives

- ReActAgent: one agent, one thread, one provider. The base loop.
- PlannerExecutorAgent: explicit plan, then every step on ONE executor with ONE
  provider, run sequentially.
- SupervisorAgent: one orchestrator model delegates dynamically to named
  sub-agents via delegate_to_<name> tools. Routing is implicit and turn-by-turn.
- swarm (CLI): a FIXED architect -> implementer -> reviewer -> revise pipeline,
  each role pinnable to a different provider.
- dojo (CLI): N providers each attempt the SAME change in isolated worktrees; a
  judge picks the winning diff.
- OrchestratorAgent: an EXPLICIT plan like planner/executor, where each subtask
  is routed to a named sub-agent on its OWN provider and tool subset like the
  supervisor, and independent subtasks run in PARALLEL. The planner chooses the
  subtasks and their dependencies dynamically, rather than following a fixed
  pipeline.

The orchestrator complements these; it does not replace them.

## The three roles

Every orchestration run has three roles, and each can be a different provider:

1. Planner. Decomposes the goal into a small set of subtasks, assigns each to a
   sub-agent role, and marks dependencies. Runs on the base provider.
2. Sub-agents. Do the work. Each sub-agent has a role, a system prompt, a tool
   subset, and an assigned provider/model. Independent sub-agents run in
   parallel; a sub-agent with no provider of its own falls back to the base
   provider. The CLI always includes four core roles and may add task-matching
   specialists, each pinnable to its own provider via --roster:
   - researcher: read-only investigation; reports facts (read/list/search/glob).
   - implementer: edits and creates code (full toolbelt in --write mode).
   - reviewer: read-only critique of a change for correctness and completeness.
   - tester: writes and runs tests and reports pass/fail (gets run_command so it
     can actually execute a suite).
3. Synthesizer. Combines the sub-agents' outputs into one final answer. Defaults
   to the planner's provider; can be overridden.

## Plan contract

The planner returns JSON wrapped in <plan></plan> tags (the same convention as
PlannerExecutorAgent), shaped like:

    {
      "goal": "string",
      "subtasks": [
        {
          "id": "short-unique-id",
          "description": "what to do",
          "assignee": "one of the registered sub-agent roles",
          "depends_on": ["ids of subtasks that must finish first"]
        }
      ]
    }

The plan is validated before anything runs: unknown assignees, duplicate ids,
missing dependencies, and self-dependencies are rejected. Subtasks are grouped
into dependency waves; each wave is run in parallel, and a completed upstream
subtask's output is passed as context to its dependents.

## Library API (provider-agnostic core)

The core lives in the agent-patterns package and has no CLI dependency:

    from ronin_agent_patterns import (
        OrchestratorAgent, OrchestratorSubAgent, FakeProvider, LLMResponse,
    )

    orch = OrchestratorAgent(
        provider=planner_provider,            # planner + synthesizer brain
        sub_agents=[
            OrchestratorSubAgent(
                role="researcher", description="finds facts",
                system="You research read-only.", provider=research_provider),
            OrchestratorSubAgent(
                role="coder", description="writes code",
                system="You implement.", tools=[...], provider=coder_provider),
        ],
    )
    result = orch.run("ship the widgets feature")
    result.plan            # the decomposition
    result.subtask_results # one SubtaskResult per subtask
    result.output          # the synthesized final answer

Because every provider is injectable, the whole thing runs offline with
FakeProvider: no network, no API keys. See
packages/agent-patterns/tests/test_orchestrator.py.

## CLI

    ronin orchestrate "GOAL" [--roster ROLE=PROVIDER,...] [--write] [--offline]

Examples:

    # read-only research/plan, default provider for every role
    ronin orchestrate "explain how auth and rate limiting fit together"

    # provider-agnostic roster: each role on a different vendor's model
    ronin orchestrate "add retry + tests to the http client" \
      -r researcher=anthropic,implementer=cerebras,reviewer=gemini,tester=groq --write

    # fully offline (local brain only, zero egress)
    ronin orchestrate "summarize the module layout" --offline

Flags:

- --roster / -r: role=provider[:model], comma-separated. Roles not listed run on
  the base provider. The planner and synthesizer always run on the base provider.
- --write: let implementer sub-agents edit code. The edits happen inside an
  isolated git worktree (the same git_worktree isolation the dojo uses), so the
  main checkout is never touched; the captured diff is printed for review. Needs
  a git repo. Without one, the run degrades to read-only rather than mutating the
  tree uncontrolled.
- --offline: force a local brain and strip network tools, so nothing leaves the
  machine.

## Offline and free

Every orchestration test runs with no API keys and no network by handing the
planner, every sub-agent, and the synthesizer a FakeProvider. The CLI bridge is
tested the same way by patching provider_for_spec to return mock providers. The
--offline flag routes real runs through ronin's local-brain path. No test calls a
paid API.

## Files

- packages/agent-patterns/src/ronin_agent_patterns/orchestrator.py: the core.
- packages/agent-patterns/tests/test_orchestrator.py: offline core tests.
- packages/cli/src/ronin_cli/orchestrate.py: the CLI bridge (provider assignment,
  worktree isolation, tool subsets).
- packages/cli/tests/test_orchestrate.py: offline bridge + CLI command tests.
- packages/cli/src/ronin_cli/main.py: the `ronin orchestrate` command.
