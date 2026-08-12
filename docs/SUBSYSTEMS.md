# The subsystems, and the joins between them

`docs/ARCHITECTURE.md` is the contract: the types, the turn state machine, the
dependency graph. This document is the layer above it — what each subsystem in
`src/ronin/` actually does, which design forks were taken and why, and, most
importantly, **the joins**.

The joins deserve their own document because they are the only part of this system
that no test below `cli/` can catch. Every subsystem takes its model, its
subprocess runner and its summarizer as an injected callable, which is what let
seven of them be built in parallel and tested offline with no provider and no
network. The cost is exact: a subsystem cannot tell whether it was wired up
correctly. Three of these have already been observed to fail silently rather than
loudly, and they are listed in §2 with the code that closes each.

Detail per subsystem lives in the package's own `__init__.py` docstring, which is
the authority — this document does not restate it, it maps it. Where the two
disagree, the docstring is right and this file is stale.

---

## 1. The subsystems

| Package | What it answers | Depends on |
|---|---|---|
| `core/` | The shared vocabulary: frozen types, the `Event` union, the ReAct loop. | nothing in `ronin` |
| `providers/` | Which model, at what price: adapters, router, stable prefix, retry/failover, ledger. | `core` |
| `tools/` | What the model can do: read/write/edit, glob/grep/ls, persistent bash, task, todo, net. | `core` |
| `context/` | Why the session does not die of context exhaustion. | `core` |
| `safety/` | Why a wrong model cannot do damage, and why the gate is cheap enough to leave on. | `core` |
| `agents/` | Plan mode, subagents from markdown, hooks — all three by narrowing the registry, not the prompt. | `core`, `tools` |
| `verify/` | Why a turn does not end because the model said "done". | `core` |
| `persistence/` | The session that survives the process. | `core` |
| `ui/` | One event stream, three consumers: TUI, headless, and the reducer they share. | `core` |
| `mcp/` | Ronin as a client of other servers, and as a server itself. | `core`, `tools` |
| `ext/` | What a user or a stranger adds: skills, plugins, and the trust gate in front of them. | `core`, `agents`, `mcp`, `tools`, `ui` |
| `obs/` | An observer that folds the event stream into logs, timings and metrics — and can reach nothing else. | `core` |
| `session.py` | The orchestrator seat: the only module importing all three of `core`/`providers`/`tools`. | all three |
| `cli/` | The application, and the only place the joins exist. | anything |

The package root re-exports the SDK — `from ronin import Agent, AgentConfig` — lazily,
through a PEP 562 `__getattr__`: reaching `Agent` pulls in the whole application layer, and
`import ronin` must not. `agent.run(p)` returns one object that is both awaitable (folds to
an `AgentResult`) and async-iterable (yields typed events), so the two shapes cannot drift.
A caller's own tools go in through `AgentConfig.tools` and are folded in *below* the gate,
which is what makes them gated like a builtin rather than the one thing in the process that
escapes policy. `examples/sdk_quickstart.py` runs the whole surface offline.

### `context/` — the model never sees the whole repo

Five modules, each answering one way a long session dies. A pagerank over the
import graph picks the important files and emits **signatures only**, inside a hard
2k-token budget that degrades by dropping leaf files first and reports how many it
dropped. RONIN.md is walked from `~/.ronin/RONIN.md` down to the innermost project
file, most local winning, every section labelled with its origin. At 80% of the
window the middle of the transcript folds into a five-part summary, with the system
prompt, repo map, RONIN.md and last three turns pinned — and the most recent tool
result *per unique file path* kept in full, which is what makes "what did we edit in
turn 3" answerable two hundred turns later. Every file read is hashed over bytes and
re-checked before an edit. One deterministic truncator, head 60% / tail 40%, marker
naming the true elided count.

Every token figure this package produces is a `len(text) / 4` estimate and is
labelled as one everywhere it is rendered. A number that looks measured and is not
is worse than no number.

### `safety/` — the gate has to be cheap to say yes to

The premise is the constraint: a gate people disable protects nothing. So:

- **`command.py` parses; it does not regex the raw string.** Rules match a segment's
  *resolved argv*, never the line, because `^echo\b` matches
  `echo safe; rm -rf /` and a whole-line allow would approve the exact thing this
  package exists to stop. `shlex` is deliberately unused: it gets quoting right and
  throws away the operator structure, which is the part that matters.
- **A specific `allow` beats a tool-wide `deny`.** Specificity dominates severity,
  because that is the only way to express "gate the shell except these twelve
  commands" — the config everyone actually wants. The unconditional floor lives in
  `denylist.py` and no rule at any specificity lifts it.
- **An `ask`-severity hazard is met by an `Exact` allow**, so "remember for this
  session" is not a dead feature. Deny-list hits and taint escalations are never
  lifted.
- `.env` writes denied and reads allowed; key material denied both ways. `/tmp` is a
  writable scratch root, because a waiver people always grant protects nothing.
- **All tool output is data.** `injection.py` wraps it in a delimiter block, flags
  findings inline rather than stripping them, and escalates any call whose arguments
  derive from freshly fetched untrusted content to `ask` regardless of the allowlist.
- The sandbox is off by default and **reports honestly when no backend exists**,
  rather than returning a fallback that pretends to isolate.

Test bar: ≥50 command strings that must be blocked, each a distinct technique and
asserted distinct; ≥50 benign ones that must not prompt, asserted disjoint from the
blocked set; and an injected HTML fixture that must be flagged and not obeyed.

### `agents/` — narrow the registry, not the prompt

One idea: a model told not to do something will eventually do it; a model handed a
registry without `write` in it cannot. Plan mode is a registry of read-only tools
with `assert_read_only` as the check — hard-disabled at the registry level. Subagents
are `.ronin/agents/*.md` with stdlib-parsed frontmatter, four builtins, and a
path-restriction wrapper that is why `test-writer` cannot edit `src/`. Hooks fire on
`PreToolUse`/`PostToolUse`/`UserPromptSubmit`/`Stop`/`SessionStart`, and **exit code
2 blocks the action with its stderr handed to the model as the reason**.

A model *role* arrives here as a name, never as a client — nothing in `agents/` may
see `providers/`.

### `verify/` — the loop must not end because the model said "done"

`spec.py` detects what "verified" means from evidence in the tree, keeping the
provenance of every command. `checkpoints.py` commits to a shadow git repo with a
separate git-dir before every mutating turn and **never touches the user's index**,
which is what makes accepting edits cheap. `runner.py` runs the relevant subset for
the files that changed — a changed Python file gets ruff and mypy on that file plus
pytest on the matching test path, with the full suite only at the end — and hands
failures back **as a `ToolResult`, an observation, not as a user message, an
instruction**. `repair.py` retries under a hard cap of 3, detects "the same failure
again" through a noise-stripped signature, and produces an honest report when it
cannot fix it. `critique.py` is one cheap fast-model pass worth exactly one more
iteration.

### `persistence/` — the session that survives the process

Append-only `.ronin/sessions/<id>.jsonl`, fsynced at turn boundaries, one versioned
discriminated record per event. It refuses another schema version *by name* instead
of decoding half of it. A torn final line loads with a report; a hole in the middle
does not load. Resume folds the recorded stream back into an `AgentState`, honours
`StreamReset`, reconciles against the recorded `TurnEnd.agent_state`, and repairs a
tool call the crash left unanswered — because a transcript with an unpaired tool use
is one the provider rejects with a 400. Export is pure `(events, metadata) -> str`:
Markdown, and one self-contained HTML file with tool calls collapsed.

Beside the logs sits a WAL sqlite index — a row per session for the `ORDER BY cost` the
filesystem cannot do, and fts5 over recorded prompts and answers for `ronin sessions
--search`. It is strictly **derived**: deleting it costs a rebuild and loses nothing,
which is what keeps the jsonl the single source of truth. So its writes never raise (it
runs at a turn boundary, after the fsync, and a cache must not be able to end a session)
while its reads do — an empty result from a broken index reads exactly like "you never
had that session". A typed query is rebuilt into an fts5 expression rather than
forwarded, because `MATCH` treats `don't` as a syntax error and "not" as an operator.

### `ui/` — one event stream, three consumers

Textual, committed to (it brings Rich transitively, so it is one dependency tree,
not two) and imported lazily, so the reducer, the renderers, the command parser and
the headless path all work on a bare install — which is why almost the entire UI
suite runs with no extra installed. `reduce.py` folds events into a frozen
`ViewState`; `render.py` turns that into strings; both pure. Slash commands include
user-defined ones from `.ronin/commands/*.md` with `$ARGUMENTS` — dispatched against the
*workspace's* registry, not the builtin one, which is the difference between a command being
loaded and a command being runnable. A builtin acts; a user command is a prompt, so it is
handed back to the session and runs as an ordinary turn. `headless.py` owns
the `--output-format=stream-json` schema and the exit codes **0 done / 1 error /
2 needs approval**.

An approval takes the whole screen as a modal, and it is raised by the *policy's question*
rather than by the `ApprovalRequest` event: the loop emits that event for every tool whose
spec requires approval and only then asks the policy, which in auto-accept mode allows the
call itself — so a UI driven by the event would interrupt for every edit in the one mode
that exists to stop interrupting. `cli/approve.py` is the join, and it answers *no* until a
UI attaches, so a script, a subagent or a dead front end refuses rather than waits. Which
keystrokes approve is a table in `reduce.py` (`decision_for`) shared by the modal and the
line prompt; the widget decides nothing.

### `mcp/` — both directions

JSON-RPC 2.0 over the standard library rather than a dependency: it is a few hundred
lines, exactly testable offline, and the alternative would put the `mcp` SDK on the
import path of every session for a protocol whose whole surface is five methods.
Three rules carry the weight:

1. **A collision between two servers' tools is impossible by construction.** A
   server name may not contain `__` and server names are JSON object keys, so
   `mcp__<server>__<tool>` is unique and parses back unambiguously. There is no
   de-duplication pass because there is nothing to de-duplicate.
2. **An MCP tool never defaults to un-gated.** A server that declares no danger level
   is treated as `MUTATING` and requiring approval; a server's own annotations may
   raise that and never lower it. Waiving the gate takes two explicit config keys.
3. **A dead server does not end the session.** Its tools stay in the registry and
   return `ToolResult(ok=False)` naming what is gone and what to do instead, after
   one bounded reconnect. Everything else keeps working.

The server side exposes read/grep/glob/edit/bash plus one high-level
`ronin_task(prompt)` that runs a full nested agent loop and returns the summary. It is
launched by `ronin2 mcp-serve`, which is `cli/serve.py` — the whole of that module is
consequences of one fact, that **over stdio there is no human**, because stdin carries the
frames:

* **The permission mode decides what is published.** `ask` exposes the read-only tools,
  `auto_edit` adds `edit`, `full` adds `bash` and `ronin_task`. A gated tool with nobody
  to approve it would appear in `tools/list` and then refuse every call, which is the same
  mistake as shipping a `web_search` with no backend. What is withheld is named on stderr
  along with the flag that would expose it.
* **Both halves of the gate travel.** `GatedRegistry` is hooks, the stale-edit check,
  taint and the clamp — approval is *not* in it, because in a session `core.loop` asks the
  policy before it touches the registry at all. A `tools/call` has no loop, so
  `cli/serve.ExposedTool` asks in its place. Without that the deny list, which lives in
  the policy engine and nowhere else, would never see a served command.

### `cli/serve.py` — the constructor that was missing

Worth stating separately because it is the shape of defect this tree keeps producing: the
MCP server, its trust model, its error codes and its fail-closed approval default were all
written, tested and documented, and **nothing in production ever constructed one**. Only
the tests and the demo supplied a `NestedRunner`. So "Claude Desktop, Cursor or another
Ronin can drive this one" was true in the suite and false on a machine.

### `ext/` — what a user or a stranger adds

Three things a session can grow: skills, plugins, and the trust decision in front of a
plugin. Each is a *discovered surface*, wired the same way every other one is — a constant,
a `Paths` property, a `Loaded` field, a `_load_*` in `wire.py`, a registration — so adding
the next surface is the same five edits and not a new architecture.

* **Skills cost one line each until used.** A skill is a `SKILL.md` on disk; only its name
  and one-line description enter the cached system prefix, and the body loads on demand —
  through the `skill(name)` tool or a `/name` slash — and returns *as a tool result*, so the
  same output clamp truncates it as truncates a file read. That is the whole point:
  a large catalog is priced at its index, not its contents. Discovery is
  `installed < ~/.ronin/skills < ./.ronin/skills` and a shadow is recorded as a note, never
  a silent override. Nine role workflows (`review`, `ship`, `qa`, `investigate`, …) ship as
  the lowest tier, so a bare workspace already answers `/review` and a project can replace
  any of them.
* **A plugin is a bundle someone else wrote, and its hooks are `sh -c`.** So the surfaces a
  plugin contributes — skills, MCP servers, subagents, commands, hooks, assets — merge into
  the workspace with the plugin *losing every collision*: a bundle cannot redefine a builtin
  out from under the user. Installing one is arbitrary code execution the moment it is
  enabled, which is why trust is not advisory.
* **Trust tiers gate the install, not the load.** `builtin < official < trusted <
  community`. An `official`/`trusted` bundle installs silently; a `community` one (or any
  tier the loader does not recognise) is inspected first and the human is shown *exactly*
  what it would run — the shell commands verbatim, the servers, the agents and commands —
  and nothing is written until they agree. A surface that will not parse is shown in neither
  the consent summary nor the loaded set, so what a human approves is precisely what runs.
  Hooks from either the Ronin or the OpenAI schema normalise through one
  `HookConfig.normalize` rather than two readers that could disagree.

The layer rule for `ext/` is that it may compose the things above the tool line
(`agents`, `mcp`, `ui`, `tools`) but must never see `providers` or `cli` —
`tests/tools/test_boundaries.py` walks the import graph and fails on a lazy import that
would slip past it.

### `cli/` protocol surfaces — one loop, four front doors

The agentic loop now has four ways in besides the terminal, and every one of them reuses a
part rather than re-implementing it. `ronin acp` (Zed, JetBrains, any Agent Client Protocol
editor) and `ronin2 mcp-serve` speak JSON-RPC over the *same* `mcp.transport` framing, and
both refuse the stdout writer that would corrupt the wire at parse time, because over stdio
the protocol *is* stdin and stdout. `ronin api` puts an OpenAI-shaped `/v1/chat/completions`
and an Anthropic-shaped `/v1/messages` in front of the loop on the standard-library
`http.server`, so an existing SDK client gets an agent where it expected a single
completion; SSE streaming is named as a non-goal rather than half-built. The first-run
wizard and `ronin doctor` share one `detect()` — provider keys in the environment, local
model servers (`ollama`/`lmstudio`/`vllm`) it can reach — and the wizard writes a config
only after asking and a bounded ten-second smoke, with probing gated behind an interactive
TTY so a pipe or a test never opens a socket.

### `obs/` — an observer that can reach nothing

`SessionObserver.observe(event)` folds the loop's event stream into structured logs, turn
timings and counters. Its whole design is a *negative* dependency: it imports `core` and
stdlib and nothing else, pinned by `test_obs_depends_on_core_only`. If it could reach the
tool, provider or session layers, "observability" would quietly become a second path by
which prompts, paths or file contents leave the loop — so it is kept unable to reach them.

---

## 2. The joins — where a correct part becomes a wrong whole

Each row is a place where two independently-correct subsystems do nothing useful
until something connects them, **and where the failure is silent**. That is the
whole reason `cli/` exists and the reason it is the thinnest package in the tree.

| Join | What is silently wrong without it | Where it is made |
|---|---|---|
| `TaintTracker` ← tool output | Nothing ever calls `register()`, so the tracker has nothing to compare against and the "untrusted content escalates to ask" rule never fires. The tests pass; the protection is absent. | `cli/gate.py` |
| `TaintTracker` ← `Settings.taint_min_span` | The setting parses and is discarded. A user tuning it changes nothing. | `cli/wire.py` |
| `PolicyEngine.sandbox` ← `Settings.sandbox` + `detect()` | Sandbox mode can be turned on in config and have no effect. Worse than off: the user believes commands are contained. | `cli/wire.py` |
| `should_compact(pinned_prefix_tokens=…)` ← repo map + RONIN.md | Defaults to 0, so compaction believes the window is emptier than it is by exactly the size of the one part of the prompt that never shrinks. | `cli/spine.py` (`Loaded.pinned_prefix_tokens`) |
| `FileStateTracker` ← `read`, then → `edit` | Hashes are recorded by nobody and checked by nobody, so the "user edited it in their editor while the model was thinking" guard is inert. | `cli/gate.py` |
| Verify failures → the transcript | Fed back as a user message instead of a tool result, they read as an instruction from the human rather than an observation — which changes what the model does with them. | `cli/stream.py` |
| `McpServer` ← a real `Agent` and a real `NestedRunner` | The whole server side is exercised, documented and unreachable: nothing outside the tests builds one, so a client cannot launch Ronin at all. The feature reads as shipped. | `cli/serve.py` + `mcp-serve` |
| `PolicyEngine.approve` ← a tool call that arrives without a loop | The gated registry does hooks, taint and the clamp but not approval, so a served `bash` reaches the shell with the unconditional deny list never consulted. | `cli/serve.py` (`ExposedTool`) |
| Settings project layer → git | `.gitignore` ignored all of `.ronin/`, and git does not descend into an ignored directory, so the committed layer of a four-layer config was untrackable. Fixed by default-deny plus an allowlist. | `.gitignore` |
| System prompt ← `system_suffix()` | RONIN.md and the repo map land outside the provider's cached stable prefix, so the expensive, never-changing part of every request is re-billed every turn. | `cli/wire.py` |
| Skill catalog → the cached system prefix | Names and descriptions folded into the *stable* prefix, not the suffix — otherwise either the model cannot see a skill exists, or the catalog is re-billed every turn like the gap above. One home reads it for both `/name` and the `skill` tool. | `cli/spine.py` + `cli/wire.py` |
| Plugin surfaces → the workspace, plugin losing every collision | Merge the naïve way and a community bundle silently redefines a builtin agent or command out from under the user; the intended tool is quietly gone. | `cli/wire.py` (`collect_surfaces`, `_merge_servers`) |
| Consent asker ← `install()` of a community bundle | Skip the asker and a stranger's `sh -c` hooks install with nobody shown what they run — the trust tier is decorative. The gate lives between `inspect_for_consent` and the write. | `cli/main.py` (`ConsentAsker`) |

The rule this table encodes: **a join is made in exactly one place.** Two callers
computing `pinned_prefix_tokens` separately is two chances to forget.

---

## 3. Honest status

Things that are built and gated, versus things that are named gaps. No number in
this section came from anywhere but a real run.

**Gated by CI.** Every subsystem above is mypy-strict and ruff clean, has unit tests
in the same commit that introduced it, at least one integration test wiring the real
objects, and a `python -m ronin.<pkg>.demo` that runs offline. The dependency graph
in `docs/ARCHITECTURE.md` §3 is executable: `tests/tools/test_boundaries.py` walks
the import graph with `ast`, so a lazy import inside a function — the exact place a
boundary quietly dissolves — is caught too. Hard dependencies are zero; every extra
(`tui`, `repomap`, `http`) is reached through a lazy import and degrades with a named
error rather than an `ImportError` traceback.

**Named gaps, `safety/`.** Symlink escape out of the workspace is not caught: a
symlink inside the workspace pointing out of it passes the path check. There is no
variable expansion beyond `$HOME`, so `rm -rf "$TARGET"` is judged on literal text.
The fork-bomb pattern is textual, not structural, and is kept byte-identical to
`tools/shell.py`'s so the two cannot disagree. Heredoc bodies are parsed as commands
only for shell binaries. A long *shared command prefix* taints: two
`curl -fsSL https://…` lines overlap by 19 characters, and this is not special-cased.
The URL policy unwraps the IPv4-in-IPv6 forms — 6to4, Teredo, NAT64, `::a.b.c.d`,
`::ffff:a.b.c.d` and ISATAP, the last of which wears an ordinary public prefix and was
reachable before that — but **6rd** hides its IPv4 at a provider-chosen offset inside a
provider-chosen prefix and cannot be recognised from the address alone.

**Named gaps, elsewhere.** `esc esc` (rewind to an earlier turn) is bound, reaches
`ui.reduce.press_escape`, and is **not wired to anything** — the callback is deliberately
left unset rather than pointed at an approximation, because restoring a conversation to a
state it has already left needs a decision about where snapshots live and what "undo"
means next to `/undo`'s checkpoints. Both web tools now have real backends. `tools/fetcher.py`
resolves, vets every address the name answers with, connects to the vetted address rather
than the name, and re-vets every redirect hop. `tools/searcher.py` carries three search
providers (brave, tavily, self-hosted searxng) as a table, on the same pinned transport;
`web_search` is absent from the registry unless one is configured. `read` returns images as an artifact string
rather than a provider-native image block. The two `ModelClient` protocols are
bridged by `providers.bridge.LoopClient` rather than unified. The test trees under
`tests/` are deliberately not packages, so every module there is importable by bare
basename and two directories cannot share one; `scripts/sync_typecheck_paths.py`
fails loudly on a collision, and `test_ronin_*` is the one prefix that cannot
collide with the ~280 generically-named modules in `packages/cli/tests/`.

**Decided since.** The v2 CLI *does* take over the `ronin` console script — `ronin` and
`ronin2` both point here, and v1's entry point is renamed `ronin1` (explicitly, not
removed, so an existing user still has something to type). Console scripts are not
namespaced, so the two-letter `ro` alias is gone and a test asserts no two distributions
in this workspace claim one command, because the next collision would be silent — whichever
distribution installed second wins.
