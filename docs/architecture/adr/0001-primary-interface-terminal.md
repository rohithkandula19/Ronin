# ADR 0001: Terminal Is Ronin's Primary Interface

**Status:** proposed, awaiting approval

## Decision

Keep the inline terminal REPL as Ronin's primary daily interface and invest in
the optional Textual TUI as its richer terminal presentation. The web dashboard
remains a companion surface for discovery, read-only operations, domain-pack
status, and future remote workflows; it is not a second full coding-agent
client in this phase.

## Context

Ronin's README defines the terminal agent as the product. Its intended daily
user is a developer operating inside a local repository, usually from a
terminal or editor-integrated terminal, where filesystem scope, credentials,
Git state, shell output, and approval prompts are already local.

The terminal has one real execution seam: `run_code_agent()` drives the coding
agent, approvals, streaming, MCP tools, context, and local session state. The
inline REPL uses that seam today. The Textual TUI also uses it, with a managed
input queue, live trace, streaming output, approval modal, and interrupt
control.

The web Coding world is not yet an equivalent client. It fetches world/model
metadata and honestly falls back to labelled sample data. The API exposes the
coding safety decision layer, but it does not yet provide server-backed coding
session creation, an agent-run endpoint, streamed run events, durable approval
resolution, or a browser-safe workspace execution boundary.

## Options considered

| Criterion | Inline REPL plus optional TUI | Web dashboard |
| --- | --- | --- |
| Daily developer workflow | Direct local repository and shell access | Requires a remote/local bridge and workspace ownership model |
| Existing execution maturity | Real agent, approvals, streaming, sessions, and MCP wiring | Metadata/read-only world UI; no live coding session loop |
| Safety boundary | Reuses local tool gates and approval path | Would need an equally strong server-side execution and approval protocol |
| Offline/local-first fit | Native | Requires additional local API lifecycle and browser connectivity |
| Delivery cost | Incremental improvement to one shared runtime | Full client/server session, stream, approval, and workspace stack |
| Risk of duplicate behavior | Low: TUI is a presentation over the same terminal runtime | High if web and terminal independently evolve the same coding workflow |

## Consequences

- The REPL remains the supported default for all coding-agent work.
- The TUI is the enhanced local presentation, not a distinct agent workflow.
- Production-quality work in this phase improves the terminal execution path
  first and is exercised through both REPL-compatible and TUI flows.
- The web app receives no simulated agent execution, fake streaming, or local
  approval controls. It may receive narrow companion improvements only when
  they consume real read-only API data.
- A later ADR is required before making web a first-class coding client. It
  must specify authenticated workspace ownership, server-side execution,
  streamed event transport, approval resolution, cancellation, session resume,
  and a clear local/offline story.

## Production-quality checklist proposed for approval

This checklist defines the terminal-primary phase. It is not implemented by
this ADR.

1. **One managed turn contract:** inline REPL and TUI invoke the same turn
   runner and produce the same agent result, tool events, approval decisions,
   interruption behavior, and persisted session outcome.
2. **Live work visibility:** show streaming assistant text, tool start/result
   events, failures, and bounded output without losing ordering under retries.
3. **Diff-first approvals:** render a reviewable file diff for writes and a
   command/directory/risk summary for shell actions; approval, rejection, and
   the destructive-command floor must be visible and enforced.
4. **Interruptible turns:** Ctrl-C stops an active turn without corrupting the
   conversation or leaving a dangling tool-call history; queued input remains
   predictable.
5. **Session resume:** list and reopen a saved local session, restore
   pairing-valid structured history, and visibly report any unavailable or
   non-resumable state rather than guessing.
6. **Context and budget status:** show the active provider/model, context
   budget/count provenance (`native`, `tokenizer`, or `estimated`), and budget
   exhaustion/warnings without presenting an estimate as exact provider usage.
7. **Accessible terminal operation:** every TUI command has a keyboard path;
   the REPL remains fully usable without Textual; narrow terminals retain an
   intelligible single-column layout.
8. **Failure and recovery surface:** show provider/tool failures, durable run
   identifiers where available, and a concrete recovery action when the active
   interface supports one.
9. **Verification and demonstration:** add interruption/resume tests for both
   terminal presentations and a reproducible terminal demo script or captured
   screenshots showing an end-to-end approved edit, test run, interruption,
   and resumed session.

## Secondary-surface boundary for this phase

The web dashboard may continue to show real world/pack metadata and read-only
mission or run information. It will not receive live coding-session creation,
browser-side tool approval, streamed model output, repository mutation, or a
parallel session-resume implementation in this phase.
