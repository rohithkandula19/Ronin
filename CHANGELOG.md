# Changelog

All notable changes to this project will be documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`ronin repo` — repository intelligence, read-only and offline.** One verb, four
  subcommands: `map` (the shape of the tree — file/definition counts, languages, entry
  points, and the load-bearing modules by pagerank), `health` (static signals: orphan
  modules, parse errors, oversized API surfaces, a missing test suite), `explain <path>`
  (one file's definitions plus its resolved import neighbours — `imported by` *is* the
  change-impact set), and `deadcode` (import-graph leaves that nothing imports).
  - **A view, not a second scanner.** All four are pure functions of a `RepoScan`, the
    un-budgeted walk/parse/rank extracted from `context.repomap` and now shared with the
    model's repo map — one definition of "scan the repo," reused. `--output-format json`
    switches every subcommand to machine output; no new format flag.
  - **Honest about static limits.** A module reached only through `importlib`, a plugin
    entry point, or test discovery looks orphaned to a static graph, so `deadcode` and the
    orphan signal are labelled *candidates*, never verdicts; entry points, tests and
    package `__init__`/`__main__` files are excluded because they are leaves by design.
    Nothing claims a line count it did not measure — "size" is the definition count the
    scan really has, not LOC it would have to re-read to know.
- **`ronin2 mcp-serve` — Claude Desktop, Cursor or another Ronin can now actually launch
  this one.** The MCP server side was written, tested, documented down to its trust model
  and *never constructed outside the test suite*: no subcommand, no real `NestedRunner`.
  The capability read as shipped and was unreachable. `cli/serve.py` is the missing
  constructor and `mcp-serve` is the process that runs it, on this process's own stdin and
  stdout (`mcp.transport.stdio_streams`).
  - **The permission mode decides what is published**, because over stdio there is no
    human to ask — stdin carries the frames. `ask` exposes `read`/`grep`/`glob`,
    `auto_edit` adds `edit`, `full` adds `bash` and `ronin_task`. Publishing a tool that
    would refuse every call is the mistake `wire.py` already argues against for a
    `web_search` with no backend, so what is withheld is named on stderr together with the
    lowest `--mode` that would expose it. The question is asked of
    `PolicyEngine.relaxes()` rather than re-derived, so there is no second copy of the
    mode ladder.
  - **Nothing reaches stdout but frames.** `mcp-serve` refuses `--print` and
    `--output-format`, never runs the first-run wizard (it prompts on stdout and reads
    stdin — both are the protocol), and puts its banner and every note on stderr. A test
    injects streams whose `out` raises.
  - **Both halves of the gate travel, and there are two.** `GatedRegistry` does hooks, the
    stale-edit check, taint and the output clamp; approval is *not* in it, because in a
    session `core.loop` asks the policy first. A `tools/call` has no loop behind it, so
    `ExposedTool` asks in the loop's place. That was found the hard way — the first version
    only went through the registry, and a test that served `rm -rf ~` deleted the
    directory, because the unconditional deny list lives in the policy engine and nothing
    else in the path looks at it.
  - One integration test runs the whole path: argv → `dispatch` → a real `McpClient` over
    an in-memory pipe → `mcp__self__ronin_task` in a client-side registry → exit 0 when the
    peer hangs up. `python -m ronin.cli.demo` gained a section that does the same and
    prints what a client sees.
- **`ronin` is a console script again, alongside `ronin2`.** Both point at this tree. v1's
  entry point in `packages/cli` is renamed `ronin1` — explicitly, not removed, so an
  existing user has something to type — which is what makes the short name safe to take:
  console scripts are not namespaced, so two distributions declaring one name means
  whichever was installed second silently wins. A test now checks that no two
  distributions in this workspace claim the same command, because the next collision will
  be a package nobody thought about. The two-letter `ro` alias is gone: `ro` resolving to
  v1 while `ronin` resolves to v2 is the same silent swap in miniature.
- **`from ronin import Agent` works, and the SDK can be embedded.** The package root was a
  single docstring, so the one import line every doc and example would start with failed.
  It now re-exports `Agent`, `AgentConfig`, `AgentResult`, `Run`, the whole `Event` union,
  and the four names needed to declare a tool — **lazily**, through a PEP 562
  `__getattr__`, because reaching `Agent` pulls in the entire application layer and
  `import ronin` should not. A test resolves every name in `__all__`, and another asserts
  in a subprocess that `import ronin` loads no `ronin.cli` module.
  - **Both call shapes, one implementation.** `agent.run(prompt)` returns a `Run` that is
    awaitable *and* async-iterable: `await agent.run(p)` folds to an `AgentResult` exactly
    as before, `async for event in agent.run(p)` yields typed events. `stream()` is one
    line over the same object. Two methods with two bodies is how a fix to one misses the
    other.
  - **`Agent(config)` is synchronous**, opening its workspace on first use, because
    assembling a runtime reads files and resolves a router and cannot happen in
    `__init__` without an event loop. `await agent.ready()` is the explicit form;
    `agent.runtime` before that raises with the sentence that fixes it.
  - **Callers can register their own tools** (`AgentConfig.tools` / `Agent.open(tools=…)`),
    folded in *below* the gate — same policy, hooks, taint tracking and output budget as a
    builtin. A tool added above the gate would be the only thing in the process able to act
    without them, so there is a test asserting the clamp applies to a caller's tool.
  - `examples/sdk_quickstart.py` runs all of it offline with a scripted provider, and the
    suite runs the script. Writing it found the gap it now covers: `Tool`, `ToolContext`
    and `ToolSpec` were unexported, so a caller could be told to bring a tool and had no
    way to declare one.
- **Skills — a directory of saved workflows that cost one line each until used.** A skill
  is a `SKILL.md` (frontmatter + body) on disk. Only each skill's name and one-line
  description enters the prompt at session start; the body loads on demand — via the new
  `skill(name=…)` tool or by typing `/name` — and comes back as a *tool result*, so the
  output gate truncates it deterministically like everything else the model reads. A
  hundred skills cost ~a hundred lines in the cached prefix, not their full text.
  - **Discovery precedence is "local wins", and a shadow is a note, never a silent
    override.** `installed < ~/.ronin/skills < ./.ronin/skills`; when two tiers define one
    name the project copy is used and the shadow is recorded where a human will read it.
  - `allowed-tools` and `model` are advisory — surfaced in the loaded body, not enforced
    at the gate — and documented as advisory rather than pretended otherwise. Frontmatter
    reuses `agents.definitions.parse_frontmatter`, now parameterised with `known_keys` so
    one grammar serves both agents and skills.
- **Role workflows ship in the box.** Nine builtin skills — `autoplan`, `office-hours`,
  `design-review`, `eng-review`, `review`, `ship`, `qa`, `retro`, `investigate` — load as
  the lowest discovery tier, so a bare workspace already answers `/review` and a project
  can shadow any of them. `review` and `investigate` drive the real `reviewer`/`fixer`
  subagents through `task`; each carries an `adapted-from:` line where it borrows a shape.
- **Plugins — a bundle a stranger wrote, and a consent gate in front of it.** `ronin plugin
  add <path>` installs a directory carrying any of skills, MCP servers, subagents, slash
  commands, hooks and assets, declared in a `plugin.json` (`.ronin-plugin/` or the
  `.codex-plugin` alias). Surfaces merge into the workspace with the plugin *losing* every
  collision, because a bundle must not be able to redefine a builtin out from under you.
  - **A community bundle's hooks are `/bin/sh -c` that fire on the model's actions — so
    installing one is arbitrary code execution the moment it is enabled.** Trust tiers
    `builtin < official < trusted < community` gate that: an `official`/`trusted` bundle
    installs silently, a `community` one (or any unrecognised tier) prints exactly what it
    would run — the shell commands verbatim, the MCP servers, the agents and commands it
    adds — and installs nothing until a human says yes. A malformed surface is omitted from
    the summary *and* from what loads, so what is approved is exactly what runs. A
    programmatic `install(...)` with no approver keeps its existing contract: the caller
    owns the trust decision.
  - Hooks arriving from a plugin are normalised through `HookConfig.normalize`, which now
    accepts the flat, per-entry `event` shape as well as the nested one, so a bundle
    authored against either the Ronin or the OpenAI hook schema loads without translation.
- **`ronin acp` — Zed, JetBrains or any ACP editor can drive the loop over stdio.** The
  Agent Client Protocol server speaks `initialize` / `session/new` / `session/prompt` /
  `session/cancel` as JSON-RPC over the same framing the MCP server already uses
  (`mcp.transport`), and maps the loop's event stream to `session/update` notifications. As
  with `mcp-serve`, nothing but frames reaches stdout — the writer that would corrupt the
  wire is refused at parse time.
- **`ronin api` — an OpenAI- and Anthropic-shaped `/v1` in front of the agentic loop.** A
  stdlib `http.server` exposes `/v1/chat/completions` and `/v1/messages`; each request runs
  a real turn and returns the provider-native response shape, so an existing SDK client can
  point its base URL here and get an agent instead of a single completion. Defaults to
  `127.0.0.1:8080`; SSE streaming is a documented non-goal for now, named rather than
  half-built.
- **First-run wizard and `ronin doctor` detection.** On a first interactive run Ronin now
  detects provider keys in the environment and any local model server it can reach
  (`ollama`, `lmstudio`, `vllm`), proposes a config, writes it only after asking, and runs
  a bounded 10-second smoke so "configured" means "answered once". `ronin doctor` reports
  the same detection. Probing is gated behind an interactive TTY, so a pipe or a test never
  opens a socket — the whole path stays offline-testable with an injected probe.
- **`obs/` — an observer that cannot become a second exfiltration path.** A new
  `SessionObserver.observe(event)` folds the loop's event stream into structured logs,
  turn timings and counters. It imports **only** `ronin.core` and stdlib, pinned by a
  dedicated boundary test — if it could reach the tool, provider or session layers,
  "observability" would quietly become a channel by which prompts, paths or file contents
  leave the loop.
- **`evals/harvest.py` — turning real runs into training data, honestly.** `harvest()`
  takes recorded `TaskOutcome`s, applies a `TrajectoryQualityBar`, and emits ShareGPT
  (SFT), preference-pair (DPO) and RLVR datasets from the trajectories that clear it —
  offline, from transcripts, with the bar that dropped a row recorded rather than the row
  silently vanishing.
- **GRPO with a verifiable reward — the RF.3 training pass (`ronin-training`).** The
  adapter pipeline had SFT and DPO; this adds the third pass, and the reward is the point.
  It is **not** a learned reward model: `ronin_training.adapter.reward` is a *verifier*
  that scores a sampled completion on facts — a call that parses and validates against the
  v2 registry earns reward, the v1 dialect the runtime can't execute is the one outcome
  punished below zero — which is exactly what lets an RL pass be defined, validated and
  unit-tested with **no GPU**. `group_advantages` implements GRPO's group-relative
  advantage (`(r − mean)/std`, zero for a tied group), and `make_reward_fn` returns the
  callable `trl.GRPOTrainer` expects — no `torch`/`trl`/`mlx` imported anywhere in the
  module. `adapter/config.py` gains a validated `grpo` pass and `adapter_grpo.yaml`;
  `to_mlx_config()` refuses GRPO by name (mlx-lm has no GRPO lane — it is peft/trl only).
  Every hyperparameter shipped is trl's documented default carried explicitly, not a value
  tuned by a run that did not happen here. Tests: `test_adapter_reward.py` (17),
  `test_adapter_grpo.py` (13, incl. the shipped config end to end); `adapter.demo` gains a
  fifth section that scores five completions in the policy order the weights encode.
- **GitHub-App webhook → a gated Ronin run (`ronin-relay`).** `ronin_relay.github_app`
  turns a GitHub webhook into a typed `RunRequest` and stops there — it verifies the
  delivery, decides whether it should start a run, and hands the request to an injected
  `enqueue` callable (the seam to `ronin-jobs`), so the whole path is a pure function of
  `(secret, headers, body)` with no network. Three rules, each returned as a *value* not
  raised (a webhook that raises is a 500 and a retry storm): a bad HMAC-SHA256 signature is
  `REJECTED` and never enqueues; a **bot's own comment is ignored** so a run that comments
  cannot trigger itself forever; and only a comment opening with the command prefix
  (default `/ronin`) becomes a run — everything else is `IGNORED` with a reason. It states
  its own offline limit: an `issue_comment` payload has no PR head SHA, so a command on a
  PR runs against the default branch unless a caller resolves the head ref via the API.
  16 tests; `python -m ronin_relay.github_app` runs the whole path against a fake queue.
  The remaining RH steps — a real GitHub-App install, the live deploy, the API calls back
  to GitHub — need infrastructure this sandbox does not have and are not claimed here.

### Fixed
- **A command you wrote yourself could not be run.** `.ronin/commands/*.md` has always
  been parsed, validated (`$ARGUMENTS`, `$1..$n`, `$$`, builtin-shadow refusal) and loaded
  onto `Loaded.commands` at startup — and the dispatcher handed the parser
  `BUILTIN_REGISTRY` instead, so the one place a user could type their command answered
  *unknown command*. Dispatch is now against the workspace's registry, and a user command
  runs as an ordinary turn: same budget, same verification, same transcript.
- **Seven of the thirteen slash commands were declared and not wired**, all falling into
  one shared "not wired into this line session" branch. All seven now work:
  - `/compact` folds the transcript on demand via a new `Conversation.compact_now` and
    reports both token counts, because the ratio is the only honest measure of whether
    compacting bought anything.
  - `/model` lists the configured models and switches the one that answers the next turn
    (new `Router.client_for_name` + `LoopClient.with_model`). **Only the main model moves** —
    subagents and compaction keep their own roles, so asking for a stronger model to finish
    one hard turn does not quietly make every summary more expensive.
  - `/plan` enters real plan mode through the same `plan_runtime` as `--mode plan`: the
    model is handed a registry with no tool that can mutate, not a sentence asking it not
    to. One-way, and it says so.
  - `/resume`, `/agents`, `/hooks`, `/init` — the replay, the subagent list (grouped with
    each one's tools), the hooks grouped by firing event, and the first-run wizard made
    reachable for anyone who skipped it with `--no-wizard`.
- **A human at the interactive TUI could not approve anything.** Every piece existed and
  nothing was connected: `ui/app.py` has always accepted `on_interrupt`, `on_rewind`,
  `on_mode_change` and `on_approval`, and `cli/main.py` — the only caller of `run_app` —
  passed **none of them**. So `esc` and `shift+tab` moved the status line and did nothing
  to the running turn, and the only asker in `src/` was `UnattendedAsker`, which always
  refuses. In the shipped `ronin2`, every gated edit was auto-denied with no way to say
  yes.
  - Approvals are now a **modal screen** that takes the viewport, renders
    `ApprovalRequest.rendered` verbatim, and answers `y` (once) / `a` (and remember) /
    `n` / `esc` (deny). The keystroke→decision mapping is `ui.reduce.decision_for`, a pure
    table with unit tests — the widget decides nothing. `enter` and `space` answer
    *nothing*, deliberately: a held-down key while an approval appears must not be able to
    approve.
  - **The modal is raised by the policy's question, not by the `ApprovalRequest` event.**
    The loop emits that event for every tool whose spec requires approval and *then* asks
    the policy, which in auto-accept mode allows the call itself. A UI driven by the event
    would interrupt for every edit in the one mode that exists to stop interrupting. The
    first version of this change did exactly that; there is now a test pinning it.
  - `esc` reaches `PolicyEngine.cancel()` and `shift+tab` reaches a new
    `PolicyEngine.set_mode()`.
  - **`esc esc` now rewinds one turn**, unified with `/undo`. `Conversation.rewind`
    truncates the transcript back to before the turn *and* restores that turn's checkpoint
    through the same `CheckpointStore` `/undo` uses, so the conversation and the work tree
    move together — never a file rewinding under a transcript that still describes the turn
    that touched it. One turn back per double-press, destructive (no redo), and it degrades
    honestly: a read-only turn rewinds the conversation only, and a mutating turn on a tree
    with no git rewinds the conversation and says the files remain rather than claiming a
    restore that could not happen. Spend is not refunded — tokens billed stay billed. The
    one-line outcome is shown as a notification; the app decides none of it, `rewind` does.
  - **The full-screen TUI takes multi-turn input.** `RoninApp` docks a real input line: the
    argv prompt runs the first turn, and each submitted follow-up is fed onto a queue that
    `multi_turn_events` runs as the next turn on the same conversation. Unset `on_submit`
    (demo, replay) leaves the line inert rather than starting a turn that never runs.
  - The status line shows the real model and git branch, and asks the orchestrator for
    context occupancy once per turn. Occupancy is measured against the live transcript,
    *not* from `Budget.spent_tokens`: spend is cumulative and includes messages compaction
    has folded away, so using it would make the gauge drift further from the truth with
    every turn.
  - The line REPL gets `PromptAsker`, so a bare install with no Textual can approve too.
    Both surfaces share one keymap.
- **"Approve for this session" never matched anything.** `PolicyEngine.remembered_rule`
  wrote `Exact(argument="path", …)` — a canonical name — while matching is a literal lookup
  in the call's arguments (`MatchTarget.argument`). Every file tool here spells it
  `file_path`, so the remembered rule matched no future call and the human was asked again
  on the very next identical edit. The rule now records the argument's own name. Found by
  the integration test for the `a` key, which is the first thing that could observe it.

### Added
- **The URL policy reads the IPv4 address hidden inside an IPv6 one — and ISATAP was a
  real hole.** The gap note said 6to4 and Teredo were blocked by prefix rather than by
  unwrapping. Checking each form first found that unwrapping those two would have
  *weakened* the policy, and that a form nobody had listed was getting through:
  - **ISATAP** (`<prefix>:0:5efe:<ipv4>`, RFC 5214) wears the **site's own global unicast
    prefix**, so `2001:470:1f0b:1:0:5efe:a9fe:a9fe` looks like an ordinary public address
    to every property in `ipaddress` and to every entry in `EXTRA_BLOCKED` — and on a host
    with an ISATAP interface up it routes to `169.254.169.254`. It was **allowed** before
    this change. No prefix test can find one, which is why `embedded_ipv4` reads the
    interface identifier instead.
  - `embedded_ipv4` also unwraps 6to4, Teredo (both halves — the client's IPv4 is XORed
    with all ones, so `5601:5601` *is* 169.254.169.254), NAT64, IPv4-compatible and
    IPv4-mapped. Those five were already refused by prefix, so there the unwrapping buys a
    better *message*: "a 6to4 tunnel address, and the 169.254.169.254 it wraps is a
    link-local address" instead of "a private address", which no reader can act on without
    converting hex by hand.
  - **Unwrapping adds refusals and never grants permission.** `2002:5db8:d822::` wraps a
    public IPv4 and stays refused on its prefix: judging a tunnel by its payload alone
    would open every 6to4 and Teredo address whose payload happens to be public, so the
    two prefixes stay in `EXTRA_BLOCKED` and the payload check only ever adds a reason.
    An ISATAP address wrapping a public IPv4 is still allowed — it reaches the public
    internet, and refusing it would be a false positive.
  - **6rd** (RFC 5969) remains a named gap: its IPv4 sits at a provider-chosen offset in a
    provider-chosen prefix, so recognising one needs the provider's configuration and
    guessing an offset would refuse public addresses at random.
- **`web_search` has backends: brave, tavily, and a searxng you run yourself.** The last
  tool in the tree with nothing behind its injected callable. It stayed that way because
  a searcher needs a *provider*, which is a decision about somebody's account rather than
  a design question — so `tools/searcher.py` carries all three as a **table**, since the
  differences are entirely data (an endpoint, an auth header, a response shape).
  - Chosen with `RONIN_SEARCH_PROVIDER`; the key comes from the provider's own variable
    (`BRAVE_API_KEY`, `TAVILY_API_KEY`), and `RONIN_SEARCH_ENDPOINT` points at a
    self-hosted searxng. Unset means `web_search` **does not exist** in the session, which
    is what `build_registry` is built around: a tool with no backend that errors on every
    call teaches the model to keep trying it. Half-configured produces a note naming the
    variable to set, never a silent absence.
  - Built on `fetcher.request_once`, so a search inherits address pinning, TLS
    verification against the hostname, the size cap and the timeout from the one place
    that implements them. A searcher with its own socket code is one that eventually
    forgets to pin.
  - **The query cannot move the request.** It is percent-encoded into a parameter or
    placed in a JSON field, never concatenated into a host or path, so no query a model
    writes can point the searcher somewhere else.
  - **A configured endpoint is not a model-chosen URL.** A searxng on `localhost` is
    reachable — the user put it there, exactly as they configure a local model server —
    while a bad *scheme* in that same config value is still refused. `safety/net.py` grew
    `check_scheme` so the config path applies a visible subset of the policy instead of
    hand-rolling its own check.
  - No HTML scraping of DuckDuckGo, Google or Bing: against their terms, brittle, and a
    tool that silently degrades to garbage when a `<div>` moves is worse than one that
    says it is not configured.
  - A response in an unexpected shape is an **error naming where the results were meant to
    be**, not an empty list — a provider changing its schema must not look like "no
    results for your query" forever.
- **`web_fetch` is a real tool now, and it resolves before it connects.** Two findings in
  one: the DNS half of the URL policy was missing, and `web_fetch` **did not exist in a
  real session at all**. `Fetcher` was a type alias with no implementation anywhere in
  `src/`, and `cli/wire.py` never passed one — the tool was assembled only by demos and
  tests handing in a fake, so the SSRF hole and the missing tool were the same gap seen
  from two sides.
  - `safety/net.py` gains `resolve_and_check`, which runs the literal checks and then the
    same address rules again on **what the hostname resolves to**. `https://docs.example.com/`
    passes every literal test and is the ordinary way to reach a metadata endpoint:
    publish an `A` record pointing at `169.254.169.254` and let the victim's own resolver
    do the work. Refuses if **any** returned address is disqualified — a name answering
    with one public and one loopback address is not half safe, it is a name whose answer
    depends on which address the client tries.
  - `tools/fetcher.py` is the HTTP client that was missing, built on `http.client` +
    `socket` + `ssl` (no new dependency). It **pins**: it connects to the vetted address
    and keeps the hostname only in the `Host` header and the TLS handshake, so
    certificate verification still happens against the name. Vetting an address and then
    handing the *name* to a socket asks DNS twice and trusts the second answer, which is
    the rebinding window — the point of the whole exercise.
  - **Redirects are followed by hand**, so each hop is re-vetted from scratch;
    `302 Location: http://169.254.169.254/` dies at the hop, and the tests assert no
    connection to it was ever opened. Bounded response size, socket timeout, and a
    finite redirect chain, because a fetch is remote input.
  - Wired in `cli/wire.py` with an extractor on the **fast** model, built on first use so
    a session that never fetches constructs no client. `web_search` is still absent, and
    deliberately so: it needs a search provider and a key, and a tool that exists and
    always errors teaches the model to keep trying it.
  - Known, and written into the module: a resolver that *cannot* answer lets the URL
    through rather than refusing (the weaker of the two options, chosen deliberately —
    refusing would make a flaky resolver look like a security failure and get the check
    switched off), and pinning is incompatible with an HTTP proxy, so `HTTPS_PROXY` is
    ignored rather than half-honoured.
- **A sqlite session index, and `ronin sessions --search`.** `persistence/` recorded
  everything a session needed but could only answer one question about it — "newest
  first" — because the picker reads one JSON sidecar per session. Two questions it could
  not answer at any price: *which sessions cost the most*, and *which one was the session
  where I fixed the pagination bug*. `persistence/index.py` adds a WAL sqlite database
  beside the transcripts with a row per session and fts5 over recorded prompts and
  answers, plus `ronin sessions [TEXT] [--search/--by-cost/--reindex]`.
  - **It is a cache, and everything follows from that.** The jsonl transcript stays the
    single source of truth; the index is strictly derived, so `rm
    .ronin/sessions/index.sqlite3` costs a rebuild and loses nothing. A schema version is
    stamped in `PRAGMA user_version` and a mismatch **drops and rebuilds** instead of
    migrating. Opening a file sqlite will not read replaces it and says so, which is what
    lets `--reindex` repair the case it is most needed for — while a *locked* database
    (`OperationalError`, another Ronin mid-write) is never touched, because deleting a
    file another process has open would turn contention into two corrupt indexes.
  - **Writes never raise; reads do.** `record` runs at a turn boundary, *after* the
    transcript is fsynced, so a full disk or a locked database is recorded in `problems`
    and the turn continues — a cache must not end a session whose work is already safe.
    `search`/`recent` raise instead, because `()` from a broken index reads exactly like
    "you never had that session".
  - **A typed query is rebuilt, never forwarded.** fts5 `MATCH` is an expression
    language: `don't` raises `unterminated string`, and a sentence containing "not"
    silently inverts the search. `fts_query` extracts words and quotes each one, so any
    string a terminal can produce is a valid AND query.
  - Prompts are indexed out of `TurnEnd.agent_state` — the user's message is not an event
    in this system — with `StreamReset` applied via the same fold the exporters use, so
    retracted text never surfaces a session by a sentence it does not contain. Tool
    arguments and output are deliberately excluded: they are most of a transcript's bytes
    and are where fetched pages land.
  - Wired at the seam that already writes the sidecar, so the cadence is one write per
    turn rather than per event, and only the current turn's events are held in memory.
    `Transcript` depends on an `Indexer` protocol it declares itself, which keeps the
    cache out of an import cycle with the log it caches.
  - The plain `ronin sessions` listing still reads sidecars and never opens the database:
    the command people run daily does not depend on the cache.
- **The repo scaffold's missing half, and the packaging bug it exposed.** `pre-commit`
  (whitespace, ruff, mypy, and four generated-file gates), `.env.example`, three working
  `.ronin/` example configs with a schema README, `make install/run/eval/coverage`, a
  **coverage gate at 85% on `src/ronin`** enforced by its own CI job, and
  `docs/RULES.md` — the one-page non-negotiables `CONTRIBUTING.md` now points at.
  - **`ronin2` did not exist in any dev checkout.** The root project declared
    `[project.scripts]` but had no `[build-system]`, so `uv sync` skipped entry points
    ("this project is not packaged") and `uv build --all-packages` emitted a wheel for
    every workspace member and none for the root — `pipx install ronin` had nothing to
    install. hatchling plus `tool.uv.package = true` fixes both: `ronin2` is in the venv
    and `ronin-1.0.0` builds.
  - **`scripts/check_test_imports.py`** enforces the offline-tests rule by parsing each
    test's AST. It walks the tree rather than grepping because a grep matched a docstring
    in `test_ronin_telemetry_transport.py` — a file that imports nothing of the kind.
  - `make lint` and `make typecheck` were `@echo` stubs that exited 0 without running
    anything, and `make test` omitted `tests/` — so the entire v2 suite CI runs sat
    outside the Makefile. Both fixed.
- **A published eval suite, and the gate that keeps it honest.** 118 tasks under
  `tests/evals/` across eight categories, each a fixture repo, a prompt and a
  `verify.sh`. `scripts/check_eval_tasks.py` proves every task discriminates in three
  directions on every push — bare fixture must fail, the reference solution must pass,
  and an `injection-resistance` task must also fail when the injection's artefact is
  planted in an otherwise-solved workspace. A `verify.sh` that passes on the untouched
  fixture inflates every score derived from the suite forever, and nothing about such a
  run looks wrong. `manifest.toml` is generated (`scripts/gen_eval_manifest.py`), so a
  deleted fixture cannot quietly make the suite easier.
- **`ronin eval` and `ronin duel`.** The runner, the six-class failure taxonomy and the
  paired A/B were complete, tested and unreachable from a command line. `--dry-run`
  needs no model, no key and no network, and is the form CI exercises. Exit 1 means the
  suite could not run; exit 0 means a measurement happened — a 0% pass rate is a result,
  not a command failure.
- **`ronin telemetry status|on|off|show`, with telemetry off by default.** The consent
  store, payload log and bucketing existed with no way for a user to reach them; a
  privacy control that exists only as a Python API is not a control. `on` prints what
  will and will not be sent before recording the grant, `show` prints the local log
  verbatim so a user can audit rather than trust, and a one-line disclosure appears once
  on stderr on a first run — a notice, not a prompt. `doctor` reports the state and
  warns only when a consent file cannot be read, since that is the case where the user's
  actual choice is not the one being applied.
- **`--model ronin-qwen-local` resolves.** `local-adapter` was missing from
  `providers.registry.ADAPTERS`, so a config naming it failed with "unknown provider".
  `examples/models.local.toml` is the copy-pasteable $0 config, pointing all three roles
  at local weights — `fast` included, since that role carries subagents and compaction
  and a key needed there fails partway into a session rather than at startup.
- **`training/cuda/train_cuda.py --config`.** The trainer's constants are the v1
  ronin-code-1.5b recipe and contradicted `training/config/adapter_sft.yaml` by 20x on
  learning rate (2e-4 against 1.0e-5) and 2x on sequence length. Two files describing one
  run and disagreeing like that means whichever you did not read is silently wrong, and
  the artifact is named the same either way. The config is now selectable, validated
  before any GPU work, and recorded in the run report so two runs can be compared.
- **Colab cells 2b and 5b**, plus `python -m ronin_training.adapter preflight|validate`
  subcommand dispatch. Cell 5b previously invoked a library module as if it had a CLI,
  which exits 0 printing nothing — a cell that appears to succeed and does nothing.
  `training/tests/test_colab_notebook.py` now checks every command in the notebook
  against the real entry points and flags.

### Changed
- **The release workflow's publish gate now matches CI.** It ran `pytest packages` only
  — 10 of 23 workspace paths — so `src/ronin`, `apps/` and `training/` were unguarded at
  exactly the moment the artifact became immutable. It now runs the full workspace plus
  ruff, mypy and every generated-file check.
- **A release with no `PYPI_TOKEN` now fails instead of exiting 0.** On a tag push,
  "skipping publish" with a green result is the worst available outcome: the tag looks
  released and no package exists. A manual `workflow_dispatch` run may still dry-run.
- **`docs/site/generate.py --check` runs in CI.** It earned the slot immediately —
  registering the local-adapter provider made `providers.md` stale, and the generator
  refused to document a new provider until its four per-builder descriptions were filled
  in.
- **README states where ronin loses.** The comparison table names model quality on hard
  tasks, IDE integration, polish, user base and autocomplete as losses against Claude
  Code, Cursor and Aider. A table with no losing rows is an advertisement.
- **The "no telemetry, ever" claim is now "off unless you turn it on".** Accurate rather
  than absolute, since the opt-in path now exists.

### Fixed
- **`web_fetch` would fetch the cloud metadata endpoint.** The URL check compared the host
  against five literal spellings of localhost, so `http://169.254.169.254/` — which answers
  unauthenticated HTTP with the instance's role credentials on every major cloud — was
  fetched without complaint, as were `10.0.0.5`, `192.168.1.1`, `127.1`, `2130706433`,
  `0x7f000001`, `[::ffff:127.0.0.1]`, `metadata.google.internal` and
  `docs.example.com@169.254.169.254`. The tool hands what it fetches to a *model*, so a
  page that talks the model into one more fetch was the whole exploit.
  - The policy now lives in `safety/net.py`, next to the injection fence and for the same
    reason: it is needed by anything that builds a `Fetcher`, and a copy in the tool layer
    is the one that goes stale — which is precisely what had happened. Hosts are classified
    with `ipaddress` rather than by string, covering private, loopback, link-local,
    multicast, unspecified, reserved and CGNAT space in both families, with IPv4-mapped
    IPv6 unwrapped so one rule covers both spellings.
  - The legacy numeric notations are parsed here rather than handed to `inet_aton`, whose
    acceptance of octal and hex differs across glibc, musl and macOS — a check that blocks
    on Linux and passes on macOS is not a check, and CI runs both.
  - Anything neither a valid DNS name nor a parseable address is **refused**, including a
    host whose top-level label is all digits (`999.1.1.1`): no legal TLD is numeric, so
    that is a malformed address rather than a name. Found by the blocked-URL table, which
    is the argument for writing the cases out as data.
  - `redact_url` strips userinfo, every query *value* and any fragment, keeping parameter
    names — one rule that can be tested exhaustively, rather than a list of secret-looking
    parameter names that fails silently when it misses one. Refusal messages go through it,
    because a URL we declined to fetch can still carry a live token and declining is not a
    reason to print it. Named where it is *not* applied: the session transcript records
    tool arguments verbatim, since it is the replay source.
  - Still open, and written into the module: names are not resolved, so a public hostname
    whose `A` record points inward is not caught, and a redirect to such an address is
    followed below this seam.
- **Model prose and tool output reached the terminal with control sequences intact.** A
  terminal is an interpreter, and those two strings are the most outsider-influenced text
  in the program — a file read out of a repository nobody audited, a compiler's stderr, a
  fetched page. Left alone, `\x1b]0;…\x07` renames the user's window, `\x1b]52;c;…\x07`
  writes their clipboard in terminals that allow it, and `\x1b[2K\x1b[A` or a bare `\r`
  overwrites what is already on screen. The last is why this is a safety fix rather than a
  cosmetic one: an `ApprovalRequest.rendered` built that way displays `ls -la` while the
  command awaiting approval is `rm -rf /`, so what the user reads and what they approve
  stop being the same thing.
  - `ui.render.strip_controls` removes every C0/C1 control character except tab and
    newline, and `Styles.text` — the seam every renderer already routes model-derived text
    through — now applies it for *every* dialect. It previously escaped only Textual's `[`,
    and `PLAIN`/`ANSI` set `escape=None` on the reasoning that an out-of-band dialect has
    nothing in its payload that could be mistaken for a control sequence. True of the
    markup, false of the sink.
  - The escape *character* is removed and the payload stays, so `\x1b]0;PWNED\x07` shows as
    `]0;PWNED`: inert, and the attempt still visible. Deleting the sequence would hide the
    evidence, for the same reason `wrap_untrusted` quotes an injection rather than removing
    it.
  - Also applied on the three paths that reach a terminal without a `Styles` map: the
    `--output-format text` answer, `headless`'s stderr notices (`DENIAL_NOTICE` carries
    `rendered`), and `cli.main._print_event`, which is the interactive line session. The
    JSON formats needed no change — encoding already turns an escape into an inert
    six-character JSON escape — and a test pins that so a later "strip everywhere"
    cannot corrupt the
    machine-readable stream.
  - The cost is a compiler's colour codes in tool output, which is already truncated to a
    summary line. Stripping everything is deliberate: "no control characters in text we did
    not write" is one sentence and is tested exhaustively over both ranges, where "no
    *harmful* control characters" is a list to maintain against every terminal feature
    anyone adds.
- **A session from another schema version was reported as corruption, and hidden.** The
  codec has a precise error for it — it names both versions and says to export the old
  session with the Ronin that wrote it — and `read_events` caught it alongside every
  other decode failure and re-raised it as "malformed record in the middle of the
  transcript ... refusing to load a transcript with a hole in it". The file has no hole.
  Meanwhile `list_sessions` dropped the row entirely, so the session was not merely
  mis-described, it was invisible. Both land at the first schema bump, when *every*
  session a user owns is the old version.
  - `read_events` now raises `TranscriptVersionError`, which subclasses both
    `TranscriptError` and `SchemaVersionMismatch`: the first so the CLI's existing
    `RuntimeError` handling keeps turning it into a one-line message instead of a
    traceback, the second so a caller can tell "another Ronin wrote this" (recoverable)
    from "this is corrupt" (not).
  - `ronin sessions` lists the row with the reason, via a new `SessionMeta.unreadable`
    beside the existing `stale` — a row that shows zero turns and zero cost would read
    as an empty session, which is a different and wrong story. The flag is a judgement
    by the reading build and is never written to disk.
  - `Transcript.open` refuses to append to a log it cannot read, rather than interleave
    two builds' records into a file neither can read.
- **A session id became a filename with nothing checking it.** `--resume <id>` and
  `ronin export <id>` take an id from argv and the SDK takes one from its caller, so
  `session_path(dir, "../../x")` named — and on the writing side created — a file outside
  `.ronin/sessions`. `valid_session_id` now confines an id to a bare filename component
  at the one place the layer turns a caller's string into a path, which is the rule
  `ToolContext.resolve` and the deny list's `OUTSIDE_WORKSPACE` already apply everywhere
  else.
- **Re-opening a session whose header was damaged appended a second header mid-file.**
  "Fresh" was decided by whether a header could be *read*, so a log with a bad first
  line got a new header written below its events — a header record in the middle, which
  is the one thing `read_events` refuses to load. One bad line became an unloadable
  session. A header is now written only when there is no file yet.
- **The import-boundary gate could not see a cross-layer import in a package's
  `__init__.py`.** Relative imports were resolved against the *parent* of the dotted
  name, but `modules_under` reports an `__init__.py` under its own package's name — so
  `from ..providers import x` in `ronin/tools/__init__.py` resolved to `.providers`,
  failed the `ronin.`-prefix filter, and reached no prohibition at all. A gate with a
  blind spot in the file whose job is re-exporting is worse than no gate, because it
  reports success. No `__init__.py` had such an import, so nothing was being hidden.
- **`<<<` was parsed as a heredoc, so the next line of a command was invisible to every
  check in `safety`.** The lexer reads a run of `<` greedily, so `<<<` arrived at the
  heredoc test already ending in `<<` and was taken for one — which made the *following
  line* the heredoc body. `cat <<<x` followed by `rm -rf /` therefore produced a single
  `cat` segment, and the deny list, the hazard scan and every configured rule saw nothing
  else. The guard meant to prevent exactly this peeked ahead for a fourth `<` after the
  greedy run had already consumed it, so it could never fire. The operator is now decided
  from the finished token, `<<<` becomes a redirect whose target is data (never a path to
  check), and a shell fed by one has its payload parsed as code — `bash <<<"rm -rf /"`
  runs that word, exactly as a heredoc body does, and is refused on the same grounds.
- **A `.gitignore` line of only slashes compiled to a rule that matched nothing.** `//`
  lost one trailing slash and kept an empty pattern; every trailing slash is now stripped,
  so the line is dropped like the other empty forms instead of sitting in the rule set
  looking configured.
- **Fetched web pages reached a model as instructions, not data.** `web_fetch` converts a
  page to markdown and hands it to the fast model with the caller's prompt — and it handed
  it over **bare**, so page text and the caller's own instructions arrived in one
  undifferentiated string. Anyone able to edit a page (or a README, or a CI log) could put
  text in front of a model that was mid-task with file-editing tools available. The
  markdown is now fenced by `safety.injection.wrap_and_scan` before the extractor sees it,
  so it arrives attributed to its URL, inside markers the content cannot forge its way out
  of, under a standing instruction that content between the markers is data. Injection
  patterns are flagged in the header above the content — quoted, never removed, because a
  user cannot judge a source whose attempt they never saw — and the count is reported back
  to the caller so the decision to keep trusting that source is theirs.
  - The wrapper is injected, like the fetcher/extractor/clock, but **defaults to the real
    one**: a security property that holds only when the wiring opts in is not a security
    property. `build_registry` passes no wrapper, and an integration test asserts the
    production path fences anyway.
  - This is the tool layer's only import outside `core`, and deliberately so — `safety` is
    a leaf that may not import `ronin.tools`, so the edge cannot become a cycle, and the
    alternative was a second set of fence markers free to drift from the canonical ones.
    `tests/tools/test_boundaries.py` now pins the tool layer's permitted imports, which it
    previously did not constrain at all.
- **Every pull request showed a failing check that could not be fixed from the repo.** A
  second Vercel project (`web`, root directory unset, so it built the repo root as if it
  were a Next.js app) failed on every push and left every PR at
  `mergeable_state: "unstable"` — a red check nobody could act on, which is the kind of
  noise that teaches people to ignore CI. A root `vercel.json` with
  `git.deploymentEnabled: false` stops it triggering on any branch.
  - `deploymentEnabled: false`, not `{"main": false}`: the branch-map form only disables
    the named branches, and the deployments that were failing were *preview* builds from
    PR branches.
  - `apps/web/vercel.json` sets `deploymentEnabled: true` explicitly. The real project
    (`ronin-ai-os-staging`) has its root directory set to `apps/web` and therefore reads
    *that* file rather than the repo root's, so this is a no-op today — it exists so that
    the root-level `false` can never be mistaken for a repo-wide switch that silently
    takes staging previews down with it.
- **A failed turn reached the user as if the model had simply answered.** The shim set
  `FinishReason.ERROR` when its repair budget ran out and put the reason in
  `Completed.notes`, and **nothing read either**: `providers/bridge.py` dropped both on
  the way to the loop seam, so an exhausted repair budget arrived as prose with no tool
  calls — the exact shape of a model that chose to answer — and the loop ended with
  `DONE` / `no_tool_calls`. `FinalMessage` now carries `error` and `notes`; the loop
  emits the `Error` event that already existed, and ends the turn as
  `TurnState.ERROR` / `provider_error` when nothing survived. Turns that *did* produce
  usable calls continue, with the failure reported alongside.
- **The shim discarded a provider's `tool_call_id` on the native passthrough.** A
  native-shaped call from a wrapped client had its id dropped and a synthetic `ron_…`
  minted, so the next turn's `tool_result` answered a call the provider never issued —
  the 400-a-turn-later failure `providers/normalize.py` exists to prevent, and a
  contradiction of the copied-verbatim invariant already under test on the write path.
  `ShimCall` carries `call_id` through to the accumulator.
- **Exhausted repairs threw away the calls that had parsed.** Asking for three files and
  mis-typing the fourth lost all four, while the retry replayed only the first failure —
  so the model was likely to re-emit all four and lose them again. The good calls are
  kept and run; the failure is still reported.
- **The tool-call shim leaked its own close tag into the answer and silently emptied
  the argument that contained it.** `ShimStreamParser` found `</ronin:tool_call>` with a
  plain `str.find`, with none of the string-awareness every scanner in `jsonargs.py`
  has. So a call whose argument legitimately contained that text — writing a file
  that documents this protocol, for instance — was cut at the *first* close tag,
  inside the string. The truncation repair then turned `{…"content":"` into
  `{"content": ""}` and the leftover `"}}</ronin:tool_call>` was printed as prose:
  an empty file written, the tag shown to the user, and no failure reported at any
  layer. The scan is now string-aware, and the ambiguous case (a close tag inside an
  *unterminated* string, which may be either a literal or a truncated payload's real
  terminator) is resolved in `finish()`, where the stream is over and the two are no
  longer indistinguishable — so the truncation-repair path still works. Text after a
  recovered terminator is re-parsed rather than released blind.
  - A single tool call wrapped in chatter (`Here you go: {…}`) was rejected while the
    same block holding *two* objects parsed, because the multi-object path was gated
    on `len(objects) > 1`. One is what a weak model actually emits.
- **A checkpoint could stage files into the user's own git index.** `verify.checkpoints`
  promises the user's index is never touched, and ran every command with `--git-dir` and
  `--work-tree` pointed at a shadow repo — but neither flag overrides `GIT_INDEX_FILE`,
  which names the index file outright. Under any parent that exports it (a git hook, or
  ronin invoked from inside a `git commit`), every `git add` in the checkpoint store wrote
  the real repo's index instead. Reproduced before fixing: one checkpoint turned
  `?? brand_new.py` into `A  brand_new.py` in `git status` and grew `.git/index` from 137
  to 217 bytes. The store now materializes its environment on every invocation, dropping
  the five `GIT_*` location variables and pinning `GIT_INDEX_FILE` to the shadow index,
  whether or not a caller injected an `env` — previously the pin only happened when one
  did, and the default path was the unprotected one. `GIT_WORK_TREE` and `GIT_COMMON_DIR`
  additionally made `init` fail, silently costing the session its checkpoints; both are
  now neutralized too.
  - `checkpoint()` raised `ValueError` out of the store when `git rev-parse HEAD` exited 0
    with empty stdout, on a mutating turn. It returns a failed `CheckpointResult` like
    every other failure in the class.
- **Exit code 2 meant "asked", not "refused".** `core.loop` emits `ApprovalRequest`
  before calling `policy.approve`, so counting requests made *every* `--mode auto_edit`
  run that wrote a file exit 2 on a clean run and list the successful write under
  `approvals_denied`. One `ApprovalTracker`, keyed on `tool_use_id`, at all three call
  sites.

### Known gaps
- **No eval numbers exist.** Nothing has been run against a real model, so nothing is
  reported anywhere in this repository. The benchmark surface ships with its cells empty
  rather than with placeholders.
- The README has no asciinema cast; recording one requires a real model run.
- `web_fetch` and `web_search` still have no real backends.
- The v2 CLI is not the shipped `ronin` console script (still `ronin_cli.main:app`), so
  `python -m ronin` with `PYTHONPATH=src` is how the v2 commands are reached today.


### Security
- Updated transitive dependencies for the remediable Dependabot alerts:
  `brace-expansion` 1.1.18 and 5.0.9, PostCSS 8.5.23, and cryptography 50.0.0.
  The repository secret scan is now an enforced clean-tree contract: credential
  detector fixtures construct their values at runtime, so test coverage remains
  real without embedding credential-shaped literals in source files.

### Added
- **Durable orchestration CLI.** `ronin util orchestrate --durable` now saves
  read-only multi-agent plan/wave checkpoints under `.ronin/`, prints a
  recoverable runtime id, accepts hard team-wide token/cost/time/tool budgets,
  and resumes only verified unfinished work with `--resume-run`. Write mode
  remains intentionally routed through isolated mission candidates.
- **Durable multi-agent execution kernel.** `OrchestratorAgent` can now share
  the same local `RunJournal` and thread-safe `RunBudget` as ReAct runs. It
  checkpoints plans and completed dependency waves, records compact lifecycle
  evidence, accounts parallel specialist usage to one hard budget, and resumes
  only unfinished waves from a verified checkpoint.
- **Project-bound Ronin API keys.** `ronin util api-keys` now issues raw keys
  once and retains only salted hashes with scope, expiry, rate, token, cost, and
  concurrency policy. Keys can be listed, revoked, rotated, reserved/settled by
  a gateway, and audited locally without persisting secret values. They remain
  distinct from user-supplied provider credentials.
- **Bounded mission implementation turn.** `ronin util mission implement` now
  runs an approved plan only inside its attached detached candidate checkout and
  records typed usage, changed-file, digest, iteration, and outcome evidence.
  The command requires a Docker-configured candidate for the following test
  gate, never edits the parent checkout, and cannot stage, commit, push, or
  publish a change.
- **Evidence-weighted competing agent trials.** Trial selection now evaluates
  every attributable worktree diff, rejects candidates with credential findings
  or failed handoff contracts, and uses a bounded score from successful role
  outcomes plus typed contract evidence. Winners remain review-only proposals;
  no trial command stages or merges code.
- **Local ACP editor bridge.** `ronin acp --root <directory>` now exposes a
  read-only, stdio-only Agent Client Protocol v1 session surface. It reuses the
  coding agent's typed context, project memory, provider routing, and structured
  conversation history without letting editor clients escape the trusted root,
  attach arbitrary MCP servers, or gain write/command authority.
- **Durable editor sessions and isolated ACP proposals.** ACP sessions now
  persist locally and can be loaded by a later editor process. They emit compact
  activity/usage evidence to the run archive, and an explicit `proposal` mode
  invokes Ronin's bounded multi-agent worktree workflow, retaining reviewable
  patches without writing into the editor workspace or staging a change.
- **Evidence-backed learned project instincts.** `ronin util instincts` now
  stores candidate practices locally with supporting evidence, confidence,
  observation counts, and expiry. Only explicitly reinforced active instincts
  are retrieved into the typed agent context, where they remain untrusted
  evidence and can never override project policy or trigger actions.
- **Provider-aware context policy.** Ronin now resolves a guarded context window
  from its model/provider catalog or a bounded project override, reserves output
  capacity, and uses the same policy for agent compaction, indexed retrieval,
  terminal/TUI gauges, explain, and investigate modes. `/context` shows the
  active budget and supports `/context 64k` or `/context auto`; `ronin code
  --context-window` applies a one-run override. Unknown models safely fall back
  to a 32k window instead of inheriting another provider's limit.
- **Persistent specialist-team supervisor.** `ronin util team` now creates
  durable local architect, implementer, reviewer, tester, security, and release
  identities with an explicit lifecycle, heartbeats, stale-worker recovery, and
  hash-chained audit in SQLite. Per-role experience entries carry source
  provenance, confidence, expiry, access history, secret rejection, and safe
  compaction. Token-bounded local context packs combine project conventions,
  BM25 repository/test pointers, and recalled role experience; Mission Control
  exposes status-only team metadata without task, scratchpad, or memory content.
  Completed roles can also make typed, secret-screened evidence handoffs to an
  assigned peer on the same mission; recipients explicitly acknowledge them,
  and the hash-chained audit records both steps without exposing summaries or
  evidence references to Mission Control.
- **Typed durable mission event bus.** Committed mission audit records now emit
  versioned, hash-chained, idempotent envelopes for mission creation,
  transitions, handoffs, candidate assignment, test outcomes, and security
  policy violations. `ronin util mission events` lists, verifies, and safely
  replays the compact local bus, while Mission Control exposes the same safe
  event feed without issue bodies, artifacts, paths, credentials, or raw logs.
- **Durable issue-to-PR mission foundation.** `ronin util mission` now records
  strict `MissionSpec`, plan/test/review/security artifacts, hard per-mission
  budgets, legal workflow transitions, and a hash-chained audit log that is
  checked against the current snapshot. Candidate workspaces are detached Git
  checkouts tied to a mission; code verification requires an explicit Docker
  image and runs with no host fallback, no network, dropped capabilities,
  `no-new-privileges`, and bounded memory/PIDs. The read-only Operations UI
  shows mission, audit, evidence, and candidate lifecycle status without
  exposing issue bodies, artifact content, or workspace paths.
- **Evidence-gated mission execution.** A mission can now record a typed plan,
  run a Docker-only candidate verification, perform deterministic diff hygiene
  review and masked added-line secret scanning, enforce its repair/tool/wall
  budgets, and calculate an auditable release gate. Only a gate-eligible mission
  with an explicit named human approval can produce a local PR title/body/branch
  draft. This intentionally does not create branches, commits, remotes, or PRs.
- **Remote issue-to-mission intake.** `ronin util mission import` now imports
  one strictly shaped GitHub or GitLab issue into an attributable `MissionSpec`
  and local source context before any agent can run. GitHub uses authenticated
  `gh`; GitLab requires an environment-provided token and an HTTPS endpoint.
  Tokens, issue bodies, and source URLs remain out of the Operations API, and
  importing records operator-attributed audit evidence rather than an agent run.
- **Sandboxed remote verification workers.** A mission candidate can now be
  snapshotted into a leased, authenticated remote verification job. Workers
  clone a credential-free HTTPS origin at the exact revision, apply the bounded
  candidate patch, and execute only the approved command in no-network Docker.
  One-time lease tokens, patch-digest revalidation, compact evidence, explicit
  release recovery, and Mission Control lifecycle status prevent workers from
  advancing stale work or gaining merge, publish, or approval authority.
- **Governed fleet execution.** Saved fleet plans can now become durable local
  fleet runs. `ronin util fleet start`, `runs`, and `run-next` claim exactly one
  dependency-ready wave at a time, persist terminal agent/proposal evidence,
  and require explicit retry or interrupted-worker recovery. Implementation
  waves remain isolated-worktree proposals; no fleet command stages, commits,
  or pushes code. The Operations dashboard exposes fleet-run state read-only.
- **Bounded fleet planning.** `ronin util fleet plan` converts up to 512
  relevant specialist profiles into persisted research, implementation, and
  acceptance waves with a hard 32-profile per-wave ceiling. The local `list`
  and `show` surfaces expose routing evidence and dependencies without starting
  agents, provider calls, shell commands, edits, or merges.
- **Retained agent proposals.** Write orchestrations now archive each isolated
  role's non-empty patch under `.ronin/agent-proposals`, bound to the exact
  source `HEAD` and protected by a content digest. `ronin util proposals list`,
  `show`, and explicit `apply --yes` complete the review path without automatic
  merges. Apply fails closed for unverified runs, altered patches, a moved base
  revision, and non-clean Git trees; it stages only and never commits or pushes.
- Interrupted or failed orchestrations can now start a linked recovery run with the original selected profiles, roster, mode, and a bounded status-only handoff. `ronin util agent-recover` never treats prior output as verification or silently resumes a completed run.
- Real subtask outcomes now feed a durable local provider-health store with temporary exponential cooldown and success-based recovery. Benchmark and imported SWE-bench/judge reports become project-local model scorecards that `agent-route --use-scorecards` can use as explicit routing quality evidence. `ronin util agent-ops` joins run, queue, recovery, ledger, provider, and scorecard status without provider calls.
- Patch preflight now validates JSON and TOML in addition to Python and locally available TypeScript parsing, preventing malformed structured configuration writes before disk mutation.
- Repository-owned agent constitutions now enforce protected write paths, team caps, specialist-role requirements, and optional sandbox requirements from `.ronin/constitution.json`. Orchestration and interactive coding fail safely on malformed policy. Completed orchestrations append compact, hash-chained local ledger events verifiable with `ronin util ledger verify`.
- Ronin now has durable local SQLite project memory with deterministic hashing retrieval, sensitive agent writes, and likely-secret rejection; compatible `RONIN.md`, `CLAUDE.md`, and `AGENTS.md` behavior remains unchanged. New `project-memory` commands manage explicit facts without cloud storage.
- The agent control plane adds bounded parallel queue workers, explicit evidence-led per-role model-routing recommendations, and competing worktree trial execution with verified-only selection. Trial selection never merges or applies a candidate diff automatically.
- `ronin eval platform` now covers constitution policy, ledger verification, and local project memory alongside queue, telemetry, retrieval, and sandbox regressions.
- The agent platform now gives each implementation orchestration role a separate detached Git worktree, keeps review/test views on the candidate implementation tree, attributes resulting diffs by role, and never writes to the parent checkout. Project-local agent queues, terminal run/provider dashboards, a sandbox-policy inspector, and `ronin eval platform` provide controlled scheduling and observable offline operation. Semantic code retrieval now has a deterministic local hashing fallback and is available to coding runs without credentials or network egress.
- `ronin-memory` now includes a dependency-free `SqliteBackend` for persistent, local-first long-term memory with namespace isolation, bounded retention, and pluggable scoring.
- `ronin-agent-patterns` now supports per-run token, wall-clock, and provider-reported cost ceilings. Exhausted budgets return partial output with an explicit trace error and never execute newly requested tools.
- `ronin-agent-patterns` now includes an opt-in, repository-scoped `PlanCache` for `PlannerExecutorAgent`, with atomic local writes, repository-change invalidation, bounded retention, and no raw task text in cache records.
- Diff previews now use a chunk-aware, fixed-width renderer that treats diff content as literal terminal text, including raw fallback output.
- Agent-facing checkpoint rewinds now offer a read-only preview, create a mandatory recovery snapshot before changing files, and atomically persist their local index.
- Provider discovery now recognizes provider-specific environment keys (for example `GROQ_API_KEY` and `OPENROUTER_API_KEY`) ahead of the shared OpenAI-compatible fallback; health reporting names only the credential source, never a key value.
- Plugins now support a literal, non-executing `PLUGIN` manifest for capability declarations. Malformed or dynamic manifests are rejected before import; undeclared legacy plugins receive a conservative full capability set. `subprocess` and `payment` declarations require an explicit approval even under `--yolo`.
- Python edits now receive a pre-write AST parse check, so invalid `write_file`, `edit_file`, and `multi_edit` proposals are rejected without changing the file. Valid Python edits report removed top-level public symbols; TypeScript syntax is checked opportunistically with a project-local compiler.
- `ronin dev perf` now has a versioned JSON report format, deterministic benchmark executor seam, atomic report writes without captured command output, p95/failure summaries, and baseline comparison that can fail on a configurable median-latency or failed-run regression.
- Release automation now validates a fixed seven-package manifest against the tag, synchronizes package versions and CLI pins, verifies wheel/sdist completeness and clean installs, emits `SHA256SUMS`, and uploads checked artifacts to the GitHub Release. The legacy `csk` release-script wording has been removed.
- `ronin orchestrate` now selects a bounded, task-relevant team from a 1,170-profile generated specialist catalog and optional non-executing `.ronin/agents.json` project profiles. `ronin agents` shows the catalog and selected team; all write-capable profiles remain worktree-isolated and approval-gated.
- Agent routing now combines task tags with local repository-map evidence (relevant files, symbols, language, and project markers) and explains every selected specialist. Write orchestrations enforce a researcher -> implementer -> independent reviewer/tester workflow contract before any provider call, with per-agent time/turn ceilings, an optional token ceiling, bounded parallelism, a total plan reservation limit, and observed provider-health reporting. Live task state is atomically persisted under `.ronin/agent-runs/`; `ronin eval agents` adds provider-free regression coverage for routing, workflow, and governance. Existing installations need no migration; the new local task-board history is removable independently.

## [1.0.0]

Package version `1.0.0` (PEP 440); displays as `v1.0.0`. The 1.0.0 line is rc.3 plus two release-readiness fixes. It carries forward all of the RC-series hardening (fail-closed budget, relay traversal confinement, tool-gate drift guard, destructive-floor coverage of `git reset --hard` / forced `git clean`), the honesty fixes (eval skips, graceful errors, deterministic offline eval), and standalone packaging (proven clean-install, run in CI). Full notes: `docs/release/v1.0.0-notes.md`.

### Fixed
- `ronin doctor --check` now exits non-zero when the live provider check fails or cannot be verified (was silently exit 0); the status row still shows the honest reason.
- README provider table + `/model` example synced to `config.py` presets (`gemini-2.5-flash`, groq `openai/gpt-oss-20b`, openrouter `qwen/qwen3-coder:free`).

### Notes
- Not marketed as production-hardened or benchmarked — see the honest limitations in the release notes. No PyPI publish is bundled with this version; that remains a separate, gated step.

## [1.0.0-rc.3] — release candidate

Package version `1.0.0rc3` (PEP 440); displays as `v1.0.0-rc.3`. Folds in the Stage-A hardening and Phase-1 validation fixes and makes the CLI standalone-installable. Supersedes rc.2 (whose tag predates this work). Previous RC tags are not moved. Full notes: `docs/release/v1.0.0-rc.3-notes.md`.

### Security & Safety
- **Fail-closed cost budget** — unknown model pricing is `UNKNOWN`, never a silent `$0`, so a `max_cost_usd` cap cannot be bypassed.
- **Relay path-traversal confinement** — `..` / encoded / backslash traversal blocked; request path confined to the target root.
- **Tool-registry gate-drift guard** — coding session refuses to start if a mutating tool is ungated.
- **Destructive floor now blocks `git reset --hard` and forced `git clean`** (any flag order) even under `--yolo` / god-mode; branch switches and dry-runs are not flagged.

### Fixed
- `ronin eval run` no longer uses hardcoded placeholder models; missing auth → **SKIPPED** with a reason (no fake score). Missing dataset now errors gracefully instead of a traceback.
- **Standalone packaging** — `ronin-cli` installs cleanly outside the repo from the wheel set (proven by `scripts/test_clean_install.sh`, run in CI). No PyPI publish yet.
- CI: removed echo-no-op Node lint/test gates; added real build + clean-install gates.

## [1.0.0-rc.2] — release candidate

Package version `1.0.0rc2` (PEP 440); displays as `v1.0.0-rc.2`. Cut during the 3-day RC validation pass — two documentation/consistency fixes, no feature or behavior changes.

### Fixed
- **README hero example was a broken invocation** (release blocker, #37). The quickstart showed `ronin "explain @main.py and add tests"`, but ronin has no catch-all default command — a bare quoted prompt errors with `No such command`. Corrected to `ronin code "explain @main.py and add tests"`, matching the actual coding-agent entrypoint. Documentation-only; no CLI behavior changed.
- **`ronin doctor` printed the raw PEP 440 version** (#38). The diagnostics "ronin version" row showed `1.0.0rc1` while `ronin --version` / `ronin version` showed the friendly `1.0.0-rc.1`. `doctor` now uses `display_version()` for a consistent `v1.0.0-rc.2` everywhere.

## [1.0.0-rc.1] — release candidate

Package version `1.0.0rc1` (PEP 440); displays as `v1.0.0-rc.1`. First release candidate — see `docs/release/v1_release_notes.md`.

### Terminal UX polish + safety hardening
- **Destructive floor (safety).** A catastrophic `run_command` — `rm -rf`, `git push --force`, `drop table`, `mkfs`, `dd`, fork bomb — is **never** silently auto-approved, not even under `--yolo` / `--god-mode`. The gate shows a red block card (what · why · a safer alternative) and requires the user to type the phrase `run destructive`; a headless run can never confirm it. Normal commands under yolo are unchanged. Closes the RC security follow-up. Verified live (god-mode `rm -rf /` blocked, `ls -la` auto-approved).
- **Premium approval cards.** Shell-command approvals render a bordered card (Command · Directory · Risk) with a read-only/runs-a-command/destructive risk label; file edits show File · Change (+/-) · Risk · Reason. Cards use safe Text cells (a command with `[brackets]` renders literally) and degrade to plain text under `NO_COLOR`.
- **Chip strip: god-mode awareness.** Shows `[god-mode]` + a pinned `[DESTRUCTIVE FLOOR ACTIVE]` safety chip under `--full-access`; cost + safety chips (priority ≥85) are never shed, even in an extreme-narrow terminal.
- **Slash palette.** New `/mode [normal|plan|auto-accept]` and `/plan` (thin wrappers over the existing edit-mode system); `/help` reorganized into product-feel groups (start · models · coding · pipeline & roles · safety · integrations · memory & context · session) — a test asserts every listed command is real.
- The signature animated-panda launch screen is unchanged.

### v1.0 launch readiness (Wave 10)
- **Fixed the installer.** `install.sh` was stale from the `csk → ronin` rename and installed a broken `csk` shim (curl install non-functional). It now installs `ronin` + `ro` shims that exec `uv run ronin`, with corrected header and get-started hints.
- **`ronin --version`** now works (eager root flag), matching universal CLI convention; `ronin version` unchanged.
- **Corrected stale docs.** README's test-count badge/prose (2475 / 2,232) were stale and inconsistent — now the measured **3,274 passing**; fixed the manual-update path (`~/.local/share/ronin`).
- **New user docs**: `docs/providers.md`, `docs/free-mode.md`, `docs/offline.md`, `docs/pipeline.md`, `docs/safety.md`.
- **Release artifacts** under `docs/release/`: readiness audit, honest eval/benchmark report (measured RUN vs SKIPPED vs NOT-RUN; no faked benchmarks or SWE-bench score), security review (12/12 PASS, zero blockers), demo script, and social/HN/Product-Hunt drafts, plus release notes + launch checklist.
- **Honest status**: not on PyPI yet; one documented `--god-mode` safety boundary; recommends cutting a **release candidate** (`v1.0.0-rc.1`), not a final `v1.0.0`. No tag/publish/deploy performed.

### Added — untracked-file evidence, required/optional suites, restore any checkpoint (Wave 9)
- **Untracked-file diff evidence**: `DiffEvidence` now covers brand-new untracked files alongside tracked changes, captured with `git diff --no-index` — strictly read-only, so **nothing is ever staged** (no `git add -N`) and there is nothing to clean up. Binary or oversized untracked files are recorded as metadata only; any failure lands in `capture_warnings` and the tracked evidence still stands. New fields: `tracked_files`, `untracked_files`, `binary_files`, `omitted_files`, `capture_warnings` (all in `--json`). The semantic check sees untracked evidence automatically.
- **Required vs optional verification suites**: mark a suite optional with a trailing `?` (`--verify-suite "lint?:ruff check ."`) or via `--required-suite` / `--optional-suite`. A **required** failure fails the run (blocked blocks it); an **optional** failure/block is only a **warning** — it never fails, blocks, or passes the run on its own. `--auto-verify-all` now classifies detected suites (tests/build required; lint/typecheck/format optional) and prints the classification before running. The suite table shows required/optional + the per-suite `verdict_effect`; the truth table shows "required X passed/Y failed, optional A warning/B passed".
- **Restore any checkpoint on unsafe resume**: `--list-checkpoints` shows what's available (id · created · sha · files · label); `--restore-latest-checkpoint`, `--restore-checkpoint-id <n>`, and `--restore-checkpoint-interactive` restore the tree to a chosen checkpoint — always **gated** (confirm before overwriting), re-checking the git snapshot afterward and refusing if it still mismatches (unless `--force-resume`). Never automatic, never resets/stashes, never destroys local work without approval; an unknown id errors.
- **Extended Final Verification truth table**: adds "Untracked evidence" (included/omitted/unavailable), a required/optional suites breakdown, and a "Checkpoint restore" row (not_needed/offered/restored/declined/unavailable).
- 28+ new tests (untracked text/binary/large + index-unchanged proof, required/optional aggregation, `?`-syntax parsing, auto-classify, list/restore-latest/restore-by-id/declined-safe/success-rechecks). **No faked success, untracked files are never permanently staged, and local work is never restored/reset/stashed without approval.**

### Added — diff evidence, multi-suite verification, checkpoint restore (Wave 8)
- **Real unified-diff evidence**: the pipeline captures the actual working-tree diff (read-only `git diff HEAD`) — `files_changed`, additions/deletions, and an excerpt truncated to a byte budget with an explicit `truncated` flag (`DiffEvidence`, in `--json`). Never mutates files, never needs the network, fails closed outside git. Flags `--diff-context`, `--max-diff-bytes`, `--no-diff-evidence`.
- **Full-diff semantic review**: `--semantic-contract` now feeds the model the **actual diff** (not just the implementer's `diff_summary`), and **downgrades a 'passed' judgement when the diff is missing (→ unknown) or truncated (→ warning)** — no semantic certainty without a full diff. A clear misalignment still fails the run.
- **Multi-suite verification**: pass `--verify-cmd` repeatedly, or named `--verify-suite "name:command"`, or `--auto-verify-all` to detect several suites (tests · lint · typecheck · build). All run through the approval gate and aggregate into a compact suite table (name · command · status · exit · duration). **Any failed suite fails** the final verdict; a declined suite blocks, never passes.
- **Safe checkpoint restore on unsafe resume**: `--checkpoint` records its id in the saved state; on a `--resume` whose tree moved, `--restore-checkpoint` offers (after showing what will happen and a confirm) to restore the tree to the checkpoint, then **re-checks** the snapshot and refuses if it still mismatches (unless `--force-resume`). A declined restore exits safely; `--no-restore-offer` hides the offer. Never automatic, never resets/stashes or destroys local work silently. Git snapshots now ignore ronin's own `.ronin/` metadata.
- **Extended Final Verification truth table**: diff-evidence (available/truncated/missing/disabled) · suites (N total, P passed, F failed) · verify-result · tests-run · git-snapshot (matched/changed/restored/warning/unavailable) · contract · semantic · review-blockers · acceptance · final verdict.
- New pure modules `pipeline_diff` and multi-suite helpers in `pipeline_verify`. 40+ new tests (diff capture + truncation, multi-suite aggregate/decline, full-diff semantic honesty, restore offer/declined/success-rechecks). **No faked success, no semantic certainty without diff evidence, no bypassed gates, and local changes are never restored/reset/stashed without approval.**

### Added — auto-verification, git-safe resume, semantic contracts (Wave 7)
- **Auto-detected verification**: when `--verify-cmd` is omitted, the verifier now auto-detects the repo's test command (uv/pytest, npm/pnpm/yarn, cargo, go, make, or a `verify:` memory line) and runs it through the same approval gate. An auto-detected failure overrides a tester's 'passed'; a declined run is blocked, never passed; no command found preserves the advisory verifier. `--no-auto-verify` disables detection. The truth table's verify-command row shows provided / detected / not_found / disabled.
- **Git-snapshot resume safety**: `--save-state` now records a `GitSnapshot` (repo path, HEAD sha, branch, dirty + untracked files, timestamp, ronin version). On `--resume`, ronin compares the saved tree to the current one and **refuses an unsafe resume** (moved HEAD, changed/dirty tree, path/branch drift) unless you pass `--force-resume` — it never resets or stashes your work. `--checkpoint` takes a lightweight git safety snapshot first (tracked + untracked, via the existing checkpoint ref — no index/branch/HEAD touched). Resume still skips completed stages unless `--rerun-completed`.
- **Semantic contract check** (`--semantic-contract`, opt-in): a read-only model pass that judges whether the implementation diff actually fulfils the architect's plan + acceptance criteria — objective/acceptance alignment, scope-creep risk, missing plan items, unexpected changes, with evidence. `SemanticContractReport` is typed + serializable (in `--json`); `finalize_semantic_status` is conservative and **never claims a pass on weak evidence**. It's advisory — a clear misalignment ('failed') fails the run; warning/unknown don't. Default off (no extra model call).
- **Extended Final Verification truth table**: verify-command · verify-result · tests-run · git-snapshot (clean/changed/unavailable) · acceptance · contract · semantic-contract · review-blockers · final verdict.
- New pure modules `pipeline_git_snapshot` and `pipeline_semantic`. 45+ new tests (auto-detect per ecosystem, auto-verify failure override, git snapshot serialize/compare, resume same/changed/forced, checkpoint preserves local changes, semantic pass/warn/fail/unknown, truth-table rows, commit still blocked unless the final verdict is passed). **No faked success, no bypassed gates, no semantic certainty without evidence, and local changes are never reset or stashed.**

### Added — independent verification, contract checks, resume (Wave 6)
- **Independent verification** (`--verify-cmd "<command>"`, `--verify-timeout`, `--no-independent-verify`): the verifier no longer just trusts the tester — the pipeline harness **runs the command itself** (through the existing shell approval gate, unless auto-accept is enabled) and reconciles the real exit code against the tester's claim. If the tester claimed passed but the command **fails**, the final verdict is **failed**; a declined / timed-out / errored run is **blocked**, never **passed**. With no command, the advisory verifier is preserved.
- **Artifact contract enforcement** — a typed, serializable `ContractCheckReport` cross-checks the handoff artifacts: the implementer's `files_changed` must overlap the architect's `files_to_change` (fail on none, warn on unexplained extras), completed steps should track the plan, unresolved review `required_fixes` / blocking findings become blocking issues, and the verifier must cover every acceptance criterion. Empty/unparsed artifacts degrade to unknown/warning — never a silent pass. Included in `--json`.
- **Combined final verdict** with safety-first precedence: a halted stage → **blocked**; a failed independent verify / failed contract / unmet criterion / unresolved blocking review / failed verifier → **failed**; **blocked** for a declined/blocked verify; **passed** only with real evidence (a passing independent verify, or — advisory mode — a passing verifier with the contract not failed). Commit/PR is gated on this combined verdict.
- **Final Verification truth table** in the result: tests-run · verify-command · acceptance (N met / M unknown / K unmet) · contract · review-blockers · final verdict.
- **Resume** (`--save-state <file>`, `--resume <file>`, `--rerun-completed`): the run checkpoints `PipelineState` JSON after every stage (and on failure/block); `--resume` continues from the first non-completed stage, preserving artifacts/summaries/verdicts and **not re-running completed stages** unless `--rerun-completed`. A corrupt or incompatible state file exits with a clear error; a changed repo path warns. `task` is now optional when resuming.
- New pure modules `pipeline_contract`, `pipeline_verify`, `pipeline_state_io`. 50+ new tests (contract cases, independent verify pass/fail/decline/yolo/timeout, final-verdict precedence, truth table, save/load/resume incl. corrupt + incompatible, CLI gating). **No autonomous shipping, no bypassed gates, no faked test success** — a declined verification never becomes passed, and commit stays blocked unless the final verdict is passed.

### Added — structured artifacts, verifier stage, gated commit/PR (Wave 5)
- **Structured handoff artifacts**: pipeline stages now pass typed, serializable objects, not just prose — `ArchitectPlan` (objective, constraints, files to inspect/change, steps, risks, test strategy, **acceptance criteria**), `ImplementationReport` (steps, files changed, diff summary, commands, unresolved, confidence), `ReviewReport` (findings with severity, required fixes, safety/architecture concerns, approval recommendation), `VerificationReport` (tests discovered/run, passed/failed, acceptance-criteria status, final verdict). Every field defaults to an **explicit unknown** — a stage never fabricates a value — and `--json` now includes the artifacts.
- **Verifier stage** (read-only) — the default pipeline becomes **architect → implementer → reviewer → tester → verifier**. It confirms the implementation meets the architect's acceptance criteria, that claimed tests actually ran, and that changed files match the plan; verdict is **passed / failed / blocked / unknown** and `finalize_verdict` **never upgrades unknown to passed**. It cannot edit files (read-only role, enforced).
- **Acceptance-criteria tracking**: the architect defines criteria; the verifier evaluates each as **met / unmet / unknown / not_applicable**; the result renders the rollup + a coloured verdict, and the exit code is non-zero on a failed/blocked verdict.
- **Gated commit/PR finish** (opt-in, never silent): `--commit` offers a commit **only after a passing verifier** — a failed/blocked/unknown verdict (or a halted pipeline) requires an explicit y/N, never an automatic commit — always showing the diff summary first and reusing the existing gated git helpers. `--pr` then offers a gated push + PR; `--branch` / `--commit-message` / `--pr-title` / `--pr-body` override the drafts. `--dry-run` describes what it *would* do and makes **zero** git changes.
- **Failure handling**: a failed/blocked stage halts the run, the remaining stages are marked `skipped`, and **all artifacts produced so far are preserved** in the state/JSON.
- New pure modules `pipeline_artifacts` (models + lenient JSON extraction + verdict logic) and `pipeline_finish` (pure `commit_gate` + gated IO). 55+ new tests, including a real-temp-repo proof that `--dry-run` commits nothing and a failed verifier blocks the commit. **Not autonomous**: stages are sequential, and no commit/PR happens without explicit user approval.

### Added — role-handoff pipeline (Wave 4)
- **`ronin pipeline "<task>"`** runs the Wave-3 roles **in sequence** with gated handoffs — default **architect → implementer → reviewer → tester** — each stage handing its summary to the next. It is **sequential, one agent per stage — not parallel and not autonomous** (the help text and docs say so plainly).
  - **Safety model**: read-only roles (architect/reviewer/researcher) are *enforced* (read-only tools only); doer roles run read-only unless `--write`; every edit/command still flows through the existing approval gate (yolo is never set); a **blocked or failed stage halts** the run and the remaining stages are marked `skipped` (never silently continued).
  - **`--dry-run`** prints the planned sequence, each stage's permissions, the read-only/write-capable mode, and the brain + cost badge — and runs nothing, edits nothing.
  - **`--free`** resolves to a $0 provider (shared `apply_free`: a free tier you hold a key for, else the keyless local brain — never a paid API); **`--offline`** forces the local/Ollama brain and strips network tools. The pipeline shows a FREE / LOCAL badge.
  - **`--roles a,b,c`** sets a custom sequence (validated against the shared role registry); **`--json`** / **`--out <file>`** emit the full serializable `PipelineState`.
- New pure, fully-offline `pipeline` module (state model, parsing, permission logic, renderers) with an **injectable stage runner** so the orchestration is tested without a model — 34 tests, including a `FakeProvider` integration proving a read-only stage cannot write. Roles remain the single source of truth (`roles.py`); `apply_free` is now shared by `/free on` and the pipeline.

### Added — role agents, chip strip, richer plan tracker (Wave 3)
- **First-class role agents** via `/role`: **researcher · implementer · reviewer · tester · architect · debugger**. A role shapes how ronin approaches the task (its guidance is appended to the system prompt) and, for the read-only roles (researcher / reviewer / architect), is **enforced** — the agent only gets read-only tools, so a review can't accidentally edit. Doer roles (implementer / tester / debugger) still flow through the same approval gate; a role is guidance, **never a safety bypass**. The role persists for the session and shows in the chip strip. `/help` gains a Roles section.
- **Gentle role suggestions**: when no role is set and a task clearly fits one, ronin surfaces a one-line tip (e.g. `/role debugger` for "why is this failing?") — it points at the command and never switches for you. Shown at most once per session; not on the general chat front door.
- **Always-visible chip strip** in the input box: `[FREE] [provider:model] [normal] [main*] [write-gated] [role:x]`. **Width-aware** — under a narrow terminal it sheds the lowest-priority chips first (role, then branch, then model) but never the cost badge, mode, or write-gate. Adds the **write-gated / auto-accept / read-only** safety chip so it's always clear what ronin may do. Never crashes outside a git repo.
- **Richer plan tracker**: the live checklist now supports **blocked** (`⊘`) and **failed** (`✗`) in addition to done/active/pending, and the tool tells the agent to use them honestly rather than fake `completed`. Renders nothing when there's no plan; updates only from real `update_todos` state.

### Added — premium status line, mode chips, git awareness (Wave 2)
- **Live status line + per-turn footer** now show, at a glance, what ronin is and what it may do: a **FREE / PAID / LOCAL / UNKNOWN** cost badge, `provider/model`, the current **mode**, the **git branch** (with `*` for a dirty tree and `↑/↓` ahead/behind), and the context size — e.g. `FREE · cerebras/gpt-oss-120b · normal · main* · 12.4k ctx`. The footer adds elapsed time and the cost badge.
- **Cost badge** is honest: FREE only when pricing is known-$0, PAID when a paid key is required, LOCAL for Ollama/offline, and **UNKNOWN** only when pricing can't be determined (a custom endpoint) — never guessed.
- **Git awareness** is lightweight (one `git status --porcelain -b`) and **fails closed**: outside a repo the branch segment simply drops, nothing crashes. Detached HEAD and fresh repos (no commits) are handled.
- **Mode chips** for `normal · plan · auto-accept · offline · free · scout · review`, each with its own colour, so you always know what's permitted.
- **Free-first first-run**: with no key configured, ronin now leads with the free path — exact commands `/free on`, `/login gemini`, `/provider`, plus keyless Ollama/local — and frames Claude/OpenAI as optional, never required.
- New pure, fully-offline-tested `status` module (16 tests) plus first-run guidance tests.

### Added — in-session UX (free-first controls + theming)
- **`/provider`** — list every provider with a free/paid tag and key health (keyless / key set / needs key), and switch with `/provider <name>` (keeps the saved per-provider key, resets to the new provider's default model). A live provider-health view without leaving the session.
- **`/free [on]`** — show whether the current model runs at **$0** and which free providers are ready; `/free on` switches to the best free provider you can run right now (a free tier you hold a key for, else the keyless local brain).
- **`/theme [name]`** — switch the code/diff syntax-highlight theme from a curated set of dark Pygments styles. Applies **live** (renderers re-read the theme each turn) and persists to config (new `theme` field), reapplied at session start.

### Fixed
- **Ronin now knows its own features.** A free model asked "does ronin have games?" answered *no* — but `ronin play` ships a **31-game arcade**. The agent's front-door prompt listed only its tools, never ronin's own features, so the model guessed wrong. Added a factual ABOUT RONIN block (arcade + free-first providers + headline commands) with a "don't guess about yourself" directive; a drift-guard test keeps the stated game count equal to `len(GAMES)`.

**Web research and page watches** — both free and keyless, both offline-tested.

### Added — SWE-bench harness (eval-suite)
- **Execution-based coding eval** alongside the LLM-as-judge suite. A task is *resolved* iff every `FAIL_TO_PASS` test passes and every `PASS_TO_PASS` test still passes after the agent's patch. `SWEBenchHarness` is execution-agnostic — plug in a `patch_runner` (your agent) and an `evaluator`; `make_local_git_evaluator` is a Docker-free reference over a local checkout. `SWEBenchDataset` loads the official JSONL (UPPER_SNAKE aliases + JSON-string test lists), with `.subset()`/`.repos()` for smoke and per-repo runs.
- **CLI** — `csk-eval swebench <tasks> --predictions <preds> --repo-root <path>` scores a standard predictions file and writes a JSON (and optional `--markdown`) report; `csk-eval swebench-compare <a> <b>` exits non-zero on any resolved→unresolved regression. `oracle_runner` self-validates the environment with gold patches; `compare_swebench`/`render_swebench_markdown` diff and present runs. Harness needs no API key, Docker, or network — 38 offline tests.

### Added — web research
- **`ronin research "<question>"`** — search the web and answer, with sources. Runs the read-only agent with the keyless web tools (`web_search` + `fetch_url`, DuckDuckGo HTML, no API key) so it searches, reads the most relevant pages, and answers. If no model is configured it degrades to a raw web search so you still get links. The agent run is injectable, so the command is unit-tested with no model and no network.

### Added — page watches
- **Watch a page from Telegram** — `watch <url>`, `watch <url> for <keyword>`, `list watches`, `cancel watch N`. Stored at `~/.ronin/watches.json`. The bot re-checks each watch on its existing poll tick (throttled, default every 15 minutes), hashes the watched slice (the whole readable page, or only lines containing the keyword), and pings you when it changes. The first check records a baseline (no false ping); a fetch failure keeps the old snapshot. Reminders and watches share ONE due-checking loop — no second daemon. Hashing, parsing, and the throttle are pure and unit-tested; the fetch and the Telegram send are injected so the tests are fully offline.

## [0.58.0] — 2026-06-04

**Quality gates** — four objective, CI-friendly checks aimed at the code you're about to ship, in the same "outcome over LLM-judge" spirit as `eval`/`kaizen`. Each ships with a pure, unit-tested core. **1002 tests.**

### Added — quality gates
- **`ronin mutants <file>`** — mutation testing. Injects one-operator faults (`==`→`!=`, `and`→`or`, relational boundary toggles, `True`→`False`, `+`→`-`), runs your suite against each, and reports the mutants that **survived** (tests that catch nothing). Requires a green baseline; restores the original file in every path. Reports a mutation score and exits non-zero on survivors.
- **`ronin radius`** — blast radius. Builds the repo's Python import graph (via `ast`), walks it backwards from your uncommitted diff to every transitively-dependent module, and surfaces the test modules in that radius. `--run` executes just those tests.
- **`ronin flake "<cmd>" -n N`** — flaky-test hunter. Runs a test command N times, diffs the failure sets, and ranks tests that flip green↔red — distinguishing flaky from stably-broken.
- **`ronin guard`** — scope-creep / leftover guard. Scans added diff lines for debug/secret leftovers (`breakpoint()`, `console.log`, merge markers, AWS keys, `TODO/FIXME`) and, with `--intent`, runs a read-only LLM check for files that drift from the task. Non-zero exit on high-severity findings.

## [0.56.0] — 2026-06-02

Full rebrand to **ronin** (internals, config dir, packages — no more "claude-kit/csk"), plus a learning/trust pass: the router now learns, the agent can abstain, and ronin reaches GitHub. **803 → 821 tests.**

### Added — learns & adapts
- **Self-tuning Router** — after each routed turn the outcome (clean finish vs error/block) is recorded per (tier, provider) in `.ronin/router_stats.json`. When the cheap blade proves unreliable for a tier *in this repo* (≥4 samples, <60% success), the router escalates that tier to the strong blade on its own.
- **Cost Router wired live** — `route_fast`/`route_strong` now actually switch the per-turn provider; the footer shows `💰 cost · saved $Y vs all-<provider> · N/M turns free`.

### Added — trust
- **Sentinel mode** (`ronin --sentinel`) — abstain over bluff: every reply ends with `CONFIDENCE: high|medium|low` and names what it's unsure about. Directly counters confident hallucination.
- **Escalation ladder** — low confidence on a cheap blade retries the turn once on the strong blade (uncertainty escalates instead of shipping).

### Added — GitHub
- **`ronin review --pr N`** — review a GitHub PR (diff via `gh`); `--comment` posts the review back onto it.
- **`ronin triage`** — read open issues and draft labels + priority + a first response (read-only).

### Changed
- Renamed everything from "Claude kit / csk" to **ronin**: `ronin_*` modules, `RoninConfig`, `ronin-*` packages, `.ronin/` config dir (with a safe one-time `.csk`→`.ronin` merge-migration), project dir `~/ronin`, dropped the `csk` alias.

## [0.55.0] — 2026-06-02

ronin's signature pass — six provider-agnostic things a single-vendor agent
**structurally can't do**, each shipped with its pure core unit-tested. **741 → 787 tests.**

### Added — "only ronin can do this"
- **Kaizen** (`ronin kaizen [goal]`) — the self-forging agent. Finds a weakness in ronin's own source (FIXME/BUG/XXX/TODO markers, strongest first), drafts a fix in an **isolated git worktree**, and runs the project's **own test suite as an objective fitness gate** — the diff only reaches your tree if the tests pass there. Point it at a free provider and it improves code for $0. **`--duel <provider>`** adds a cross-vendor gate: a rival model red-teams the proven diff before you approve, and a BLOCK overrides `--yes`. So a self-improvement can clear both an objective gate (tests) *and* an adversarial one.
- **The Dojo** (`ronin dojo "<task>" -m anthropic,gemini,cerebras`) — rival models each attempt the **same** change in **parallel isolated worktrees**; a judge scores the diffs and crowns a winner you can apply. Claude vs Gemini vs DeepSeek, swinging at one problem.
- **Ronin Duel** (`ronin duel --against gemini`) — a **different** provider adversarially red-teams your git diff and returns structured blockers. The author model is a poor judge of its own code; a rival vendor isn't. Exits non-zero on BLOCK (CI-friendly).
- **Scout → Strike** (`ronin code --scout "<task>"`) — read-only recon runs on a free/cheap blade (`route_fast`), then a strong blade (`route_strong`) executes only the edits. Frontier quality where it matters, free everywhere else.
- **Bushido** — a global `~/.ronin/bushido.md` code of honor the agent carries into **every** repo (folded in before project memory; the repo always wins). `remember_preference` tool persists a standing cross-project convention.
- **Muscle Memory** — the agent crystallizes a solved workflow into a reusable repo-local `/skill` (`.ronin/commands/*.md`) via `crystallize_skill` — immediately re-runnable, committed with the code, compounding over time.
- **Cost Router** — pricing table ($/Mtok, free providers = $0) + a `CostLedger` that tracks spend and "saved vs all-anthropic"; routing resolves cross-provider targets so a simple turn can run on a **free** provider, not just a cheaper model. **Wired live into both session loops**: with `route_fast`/`route_strong` set, each turn runs on the routed (possibly free) provider and the footer shows `💰 cost: $X · saved $Y vs all-<provider> · N/M turns free`.

## [0.54.0] — 2026-06-02

Closing the gap between what the README promises and what `ronin code` ships, plus VCS awareness and an opt-in unsandboxed mode. **730 → 741 tests.**

### Added — capabilities
- **Web search in `ronin code`.** The coding agent now gets `web_search` + `fetch_url` directly (they were only on the unified/chat surface before, despite the README listing them under `ronin code`). Read-only, so they work in plan mode too; `--offline` still strips them. Built once in `run_code_agent` with a name-dedup guard so `extra_tools` can't double-register.
- **`@`-URL mentions.** `@https://…` in a request now fetches that page's readable text into context — the web counterpart of `@file`. Skipped under `--offline`; failed fetches are left as-is.
- **Read-only git tools** (`git_status` / `git_diff` / `git_log`) — the agent reasons about VCS state instead of blindly shelling out (and tripping the run_command gate for a harmless read). Mutating git stays in the gated `/commit` & `/pr` commands.
- **Full-access mode** (`--full-access` / `--god-mode`, opt-in) — lifts the filesystem sandbox (reach beyond the project root), auto-approves every edit/command, and gives `run_command` a longer timeout + bigger output caps. Prints a ⚠ banner; off by default. The sandbox seam is now pinned by tests in both directions (in-root allowed, escape blocked unless full-access).

## [0.53.0] — 2026-06-01

A reliability + UX pass on top of v0.52, plus three capability additions. **636 → 730 tests.**

### Added — experience
- **Full-screen TUI as a real coding surface (opt-in via `ronin --tui`).** Rewrote it to drive `run_code_agent` off the UI thread with the whole toolbelt, stream tokens, show a live `⏺` tool trace, and gate sensitive actions (write/edit/run/…) behind an approval modal bridged across threads. The **default stays the minimal, Claude-Code-style inline REPL** (scrollback + bordered input box).
- **Type-ahead input queue** in the inline REPL — messages typed while the agent works are captured (via a cancellable `select` reader) and run as the next turn. No-op on non-TTY.
- **Clarifying questions** — an `ask_user` tool lets the agent ask one sharp question before acting on an ambiguous task (interactive sessions only).

### Added — capabilities
- **Embeddings RAG** (`semantic_search`) — optional semantic code search via Ollama (local) or any OpenAI-compatible `/embeddings`, cosine + content-hash disk cache. Exposed only when a backend exists; BM25 `repo_map` stays the zero-config default.
- **Auto context engineering** — each interactive turn injects the most relevant files (paths + symbol outlines) into the prompt; non-blocking (cold index builds in the background) and self-gating.
- **Background processes** (`run_background` / `background_logs` / `background_status` / `stop_background`), **checkpoint & rewind** (whole-workspace snapshot/rollback), and **vision-in-the-loop** (`screenshot` + `look_at`).

### Fixed — reliability (built for free models)
- **Bulletproof tool-calling** — near-miss argument names are remapped to the handler's real params (path↔directory, cmd↔command, …), unknown extras dropped, and an argument mismatch returns a coaching error with the expected signature instead of a raw `TypeError`.
- **Context/token management** — a per-result cap stops one giant tool result from blowing the window, and compaction triggers far earlier off-Anthropic (28k vs 120k) where free models have smaller windows.
- **Per-provider API keys** — `/login openai` no longer clobbers your cerebras key; each provider keeps its own (`provider_keys`).
- **Tool ignore set** now covers `venv` (not just `.venv`), `vendor`, `dist`, `build`, `target`, … — fixes list/search dumping a whole virtualenv into context (the 168s/19.9k-token turn). `path` accepted as an alias for `directory`.
- **Rate-limit backoff is now visible** — a 429 retry shows "⏳ retrying in Ns (Ctrl+C to stop)" instead of a silent ~60s freeze.

## [0.52.0] — 2026-06-01

A big pass adding capabilities a single-vendor agent structurally can't have, plus three coding-agent upgrades. **540 → 636 tests.**

### Added — "beyond Claude Code" (provider-agnostic superpowers)
- **Multi-model consensus** (`ronin consensus "<task>" -m a,b,c`) — run the same question on several models in parallel, then a judge model synthesizes one cross-checked answer with a panel-agreement note. Read-only. (`consensus.py`, +5 tests.)
- **Cross-provider failover** — new `FailoverProvider` (agent-patterns) + a `failover` config list; a turn that rate-limits/errors on the primary transparently continues on the next provider. Streamed tokens are never silently re-answered. (+6 provider tests, +4 wiring tests.)
- **Fully offline mode** (`ronin --offline`) — forces a local brain (Ollama) and strips every network tool, for zero-egress / air-gapped work. (`offline.py`, +6 tests.)
- **Eval-driven model bake-off** (`ronin bench -m a,b,c`) — runs the objective eval battery across models and recommends the cheapest one that clears a quality bar. (`bench.py`, +5 tests.)

### Added — coding-agent upgrades
- **Anthropic prompt caching** — `cache_control` breakpoints on the system + tools prefix (on by default); cache-read tokens surface in usage and show as `⚡N cached` in the status line. (+3 tests.)
- **Semantic code intelligence** — `diagnostics` / `definition` / `references` tools backed by real language servers (pyright, ts-language-server, gopls, rust-analyzer) over JSON-RPC, with graceful "install X" fallback. (`lsp.py`, +17 tests.)
- **Parallel mutating sub-agents** — `parallel_task` (concurrent read-only fan-out) and `isolated_task` (parallel editing agents, each in its own git worktree so changes can't collide; returns reviewable diffs). (`worktree.py`, +12 tests.)

### Docs
- README: new "Beyond Claude Code" section, "Running ronin for others / at scale" safety notes, updated command table + tool list.

## [0.13.0 – 0.24.0] — 2026-05-27

A large pass turning ronin into a Claude-Code-grade agent that runs on free models.

### Added — Claude-Code-grade session UI
- **Bordered input box** you type inside, with `↑/↓` history and `/`+TAB slash-command completion.
- **Streaming Markdown** — replies render live (bold, headings, lists, syntax-highlighted code) instead of raw `**`/`#`.
- **`● Verb(target)` / `↳ result`** tool lines, **syntax-highlighted diffs** in approval prompts, a per-turn **status line** (provider · model · tokens · time).
- Animated activity panda on launch (dancing / running / playing / playing football / sleeping) + a real half-block panda renderer.

### Added — providers & resilience
- **Free providers**: Gemini, Cerebras, OpenRouter (plus Groq) — no credit card. **`/login <provider>`** (masked, in-session), **`/model` / `/models`** to switch models without re-entering the key.
- **429/5xx auto-retry** with backoff (rides over free per-minute caps); Gemini thinking-model `thought_signature` round-tripping so tool calls work.
- Start a message with a folder path to switch the working directory into it.

### Added — capabilities
- **`ronin eval`** — objective agent-quality scoring across providers (no LLM judge); golden-dataset `run`/`drift` kept as subcommands.
- **MCP client** — connect any MCP server (`ronin mcp add/list`); discovered tools auto-join the agent.
- **`web_search` / `fetch_url`** tools (free, no key) and a read-only **`task` subagent** tool.

## [0.12.0] — 2026-05-26

### Added — automatic memory (remembers everything, no prompting)
- After every turn, ronin now **auto-extracts durable facts about you** from the exchange (name, stack, projects, preferences, goals) and saves them to long-term memory — in a **background thread**, so it never adds latency. You no longer have to say "remember this"; it just remembers.
- Best-effort and crash-proof: extraction failures (rate limits, parse errors) are swallowed silently — memory can never break a turn.
- Wired into both the unified `ronin` and `ronin chat`, on top of the explicit `remember` tool and the `ronin memory` view/clear command.
- 4 tests (JSON parse, saves facts, silent-on-error, empty list). Repo total: 494 → 498.

## [0.11.0] — 2026-05-26

### Added — persistent cross-session memory
- ronin now **remembers you across sessions**. Durable facts/preferences (your name, stack, the repos you work in, how you like things) are saved to a user-global `~/.ronin/memory.json` and auto-injected into the system prompt on every future run — a brand-new `ronin` already knows you.
- The agent saves facts itself via a new **`remember`** tool; recall is by injecting the most recent facts (no vector DB, no extra deps — built on the kit's `memory` package).
- **`ronin memory`** views what it knows (`--add` / `--clear`); `/memory` in-session shows it; a `🧠 N things remembered` note appears on launch.
- 7 tests (add/load/dedupe, prompt block, remember tool, forget, CLI, agent persists across a session). Repo total: 487 → 494.

## [0.10.0] — 2026-05-26

### Changed — soft, premium UI
- A **gradient `✦ ronin` wordmark** (magenta→violet→indigo) and a **soft pastel palette** (muted rose/green/teal/slate instead of hard ANSI) across the CLI.
- The interactive session opens with a **soft rounded welcome card** (gradient mark · cwd · model · mode · hint).
- A gentle **"thinking…" spinner** animates until the first token, replies are headed by the gradient ronin avatar, and turns are separated by a soft divider.
- `theme.py` gained `gradient_text()`; tool/result lines use the soft palette.

## [0.9.0] — 2026-05-26

### Changed — one unified front door
- **Bare `ronin` is now ONE assistant that does everything** in a single conversation: talk, write & run code (edits + shell commands gated with diffs/approval), generate images/video/speech, and query connected data — all on the same agent. No more choosing between "chat" and "code"; ask for anything in plain language and it routes to the right capability.
- `ronin chat` (talk + media only) and `ronin code` (pure coding agent) remain as focused modes.
- `run_code_agent` gained `extra_tools` / `extra_system` / `include_image_tool` so the unified session layers media + data tools onto the coding agent's machinery (streaming, diffs, approval gate, todo tracker, project memory, `@`-mentions, `/`-commands).

### Fixed
- Image generation no longer triggers an approval prompt — making a picture is a free, low-risk action; only file edits and shell commands are gated.
- The chat no longer generates an image when asked to *write code* about images (tightened intent routing).

### Tests
- +3 tests (unified session has code+media+data tools, generates images, writes code). Repo total: 483 → 486.

## [0.8.0] — 2026-05-26

### Added — Claude-Code parity for the coding loop
- **`@path` file mentions** — reference files in your request (e.g. `ronin code "explain @main.py"`) and their contents are inlined into context (path-traversal guarded).
- **Bare `ronin` opens the coding agent in a repo** — inside a code project (`.git`/`pyproject.toml`/`package.json`/`RONIN.md`/…) typing `ronin` drops into the coding session (Claude Code's default); outside one it's the data/media chat. `ronin chat` always forces chat; `ronin code` always forces the agent.
- **`ronin code --plan`** — proposes a step-by-step plan with read-only tools, waits for your approval, *then* executes. **`ronin code --continue`** resumes this repo's last session (persisted under `.ronin/sessions/`).
- **New tools**: `glob` (find files by pattern) and `multi_edit` (several surgical replacements in one approved, all-or-nothing step). `multi_edit` is gated like other writes.
- **Markdown rendering** — the chat and one-shot answers render as rich Markdown (headings, lists, syntax-highlighted code), like Claude Code.
- 13 tests (mentions, glob, multi_edit all-or-nothing, repo detection, session round-trip, read-only/plan tool filtering). Repo total: 471 → 483.

## [0.7.0] — 2026-05-26

### Added — `ronin explain` (the onboarding killer feature)
- **`ronin explain <path>`** — point it at any unfamiliar file/module/repo and it produces (1) a plain-English explanation (big picture → key pieces → data flow), (2) an auto-generated **Mermaid architecture diagram** that renders on GitHub / pastes into a README, and (3) optional **voice narration** (`--speak`). Read-only: it explores with read_file/list_files/search_files and never mutates. `--out file.md` writes the explanation + diagram.
- The differentiator: a pure coding agent *explains* — ronin explains, **draws it**, and **speaks it**, because it has a diagram generator and a voice. Onboard to a codebase in minutes.
- 8 tests (mermaid extraction, read-only-tools-only, no-diagram flag, injection block, `--out`, no-key guard). Repo total: 463 → 471.

## [0.6.0] — 2026-05-25

### Added
- **`ronin see <image> "<question>"`** — vision. Ask Claude (or any vision model) about a local image; ronin can now both *generate* and *understand* pictures. Shows the image inline, then the answer. Anthropic + OpenAI-compatible vision formats.
- **`ronin set-key [--provider X]`** — a friendly key setter: masked input, then a safe preview (length + `gsk_…last4` + verdict) and a hard refusal of `>=80`-char values, so the blind double-paste that produces a broken 450-char key can't happen silently. `ronin init` now echoes the same preview after the hidden prompt.
- Config accepts a provider-neutral **`api_key`** in the TOML as an alias for `openai_api_key` (the latter name confused Groq/Together users).
- **Demo assets**: a `vhs` tape at `docs/demo/demo.tape` (regenerates a walkthrough GIF, keyless) and a sample generated image in the README.

### Fixed
- The interactive chat and one-shot ask no longer crash on a provider error — they show a clean, actionable message (`_friendly_provider_error`: 401/403/429/connection).
- `ronin init` rejects bogus model answers (`yes`/`no`/…) and falls back to the provider default; `ronin doctor --check` does a live key+model ping instead of a misleading "ok".
- Git-ignore the whole `.ronin/` dir (was leaking `config.toml` with API keys); ignore generated `ronin_image_*` / `ronin_video_*`.

### Tests
- +29 tests (vision, set-key, api_key alias, provider-error handling, init/doctor guards). Repo total: 427 → 463.

## [0.5.0] — 2026-05-25

### Added
- **`ronin say "..."`** — text-to-speech via the OS engine (free, no key: macOS `say`, Linux espeak). Speaks aloud or saves audio with `--out`. Completes the media trio: image / video / audio.
- **`generate_image` agent tool** — `ronin code` can create images (logos, diagrams, placeholder art) and save them into the project mid-task. Free Pollinations backend, path-traversal guarded, gated like other writes.
- **`ronin video --engine replicate`** — paid real-motion text-to-video (vs. the free frame-animation engine). Creates a Replicate prediction, polls to completion, downloads the mp4. Needs `REPLICATE_API_TOKEN`; default model `minimax/video-01`, overridable with `--model owner/name`.

### Changed
- **Branding sweep**: finished the `csk` → `ronin` rename across user-facing surfaces (README, agent system prompts, web/API titles, package description) and fixed the stale old-repo-name links (now point to the `Ronin` repo) across docs, install.sh, and templates. The `.ronin/` config dir, the `csk`/`ro` command aliases, and internal package/module names are unchanged (back-compat).

### Tests
- +20 tests (audio, agent image tool, Replicate create/poll/download). Repo total: 408 → 427.

## [0.4.0] — 2026-05-25

### Added — media generation (terminal-native)

- **`ronin image "..."`** — text-to-image that displays in the terminal. Default backend is **Pollinations** (free, no API key), with an **OpenAI** backend (`gpt-image-1`, needs `OPENAI_API_KEY`) for higher quality. Shows inline on iTerm2, via `chafa`/`viu`/`imgcat` if installed, else opens in the system viewer. `--size`, `--seed`, `--model`, `--out`, `--backend`.
- **`ronin video "..."`** — free text-to-video. Generates N AI frames (incrementing the seed) and stitches them into a real `.mp4` with `ffmpeg`, previews the first frame inline, and opens the clip. Honest framing: this is frame-animation, not Sora-grade real-motion — the per-frame backend is pluggable so a paid motion provider can slot in later. `--frames`, `--fps`, `--size`, `--seed`.
- stdlib `urllib` only (no new dependency). **+15 tests** (both image backends, missing-key/bad-backend/bad-size guards, display fallback, video frame seeding + ffmpeg invocation/failure guards, CLI paths). Repo total: 393 → 408.

## [0.3.0] — 2026-05-25

### Added — `ronin code` now feels like Claude Code

- **Token streaming**: providers gained a `stream()` method (Anthropic via `messages.stream`, OpenAI-compatible via SSE with tool-call delta accumulation). `ronin code` / `ronin agent` now print the model's text token-by-token with tool activity inline, instead of blocking silently and dumping the whole turn at once. `complete()` stays for non-streaming callers; providers without native streaming fall back automatically.
- **Live todo/plan tracker**: for any 3+ step task the agent maintains a checklist via an `update_todos` tool (exactly one item in-progress, items flipped to completed as it goes), rendered inline as `✓ / ▶ / ☐`.
- **Project memory**: auto-loads `RONIN.md` / `CLAUDE.md` / `AGENTS.md` from the repo root into the system prompt so the agent follows your conventions. `ronin code --init` scaffolds a template. Capped at 8k chars.
- **In-session slash commands**: `/help`, `/clear`, `/undo`, `/diff` (colorized working-tree git diff), `/model`, `/memory`, `/init`, `/tools`, `/quit` (both `/cmd` and `:cmd` accepted).
- **Animated panda mascot** on launch — a small kaomoji panda that dances / runs / plays / sleeps (`ronin panda [activity]`), replacing the static block-art face that broke on some terminals.
- **+29 tests** (stream contract, delta forwarding, tool loop under streaming, todo tracker, project memory, every slash command). Repo total: 364 → 393.

## [Unreleased]

### Added — hosted SaaS backend (`apps/api`)

- **FastAPI service** behind a Bearer-token API: `POST /signup` (returns a one-time `csk_*` token), `POST /connections` (encrypted credential upload), `GET /connections` (names only, never secrets), `POST /briefings` (run-now), `GET /briefings` (history), `POST /briefings/schedule` (weekly cron).
- **Encryption at rest** for stored third-party credentials — Fernet (AES-128-CBC + HMAC-SHA256), key from `FERNET_KEY` env var. API tokens are SHA-256-hashed before storage.
- **SQLAlchemy 2.0 models** for users, service_connections (per-user encrypted), briefing_runs (full Markdown + key metrics for trending), schedules. Defaults to sqlite for dev; flip `DATABASE_URL` to a Postgres URI for prod.
- **Standalone worker** (`python -m csk_api.worker`) — polls for due users every N seconds, runs their briefing, marks the schedule as ran. `--once` for a single tick, `--interval N` for the loop.
- **Deploy infra**: `Dockerfile` + `railway.json` for one-click Railway deploys. Healthcheck on `/health`. Restart-on-failure policy.
- **14 tests** (auth, connection round-trip, secret never returned, briefing creation, history growth, week-over-week delta, schedule round-trip, worker tick). Repo total: 242 → 256.

This unblocks the "csk as SaaS, $19/mo" path: hosted scheduling, Slack delivery on cron, encrypted multi-tenant credentials. Frontend + OAuth + Stripe billing are deliberate follow-ups.

## [0.2.0] — 2026-05-11

### Added — the headline command + PyPI publish
- **`csk briefing`**: the Monday-morning founder briefing as a CLI. Revenue (MRR/ARR, new/churned this week), payments (succeeded/failed/refunded, past-due subs), engineering (urgent/high open, in-progress), and computed action items. Renders Markdown — paste into Slack/email/docs. Runs offline against demo data; runs against your real Stripe/Linear data once configured. This is now the README hero.
- **`csk briefing --slack <channel>`**: post the briefing straight to a Slack channel via `chat.postMessage`. Requires a bot token with `chat:write`. Converts the Markdown subset to Slack mrkdwn (`**bold**` → `*bold*`, headings → `*Heading*`).
- **Briefing history + week-over-week deltas**: every `csk briefing` run auto-saves a JSON snapshot to `.ronin/briefings/<date>.json`. Subsequent runs append a `_vs <last date>: MRR +$X, new subs +N, churn +M, …_` line at the bottom. `csk briefing --history` prints the full trend table. Add `--no-save` to opt out of persistence.
- **Richer demo dataset**: 8 customers, 8 subscriptions across active/canceled/past_due, 15 charges (with failures + refunds), 3 Linear teams, 12 issues across priorities — anchored to a fixed `REFERENCE_NOW` so the briefing is deterministic in demo mode.
- **PyPI publish pipeline**: `.github/workflows/release.yml` — tag a `v*` release and every workspace package + the `csk` CLI gets built and published to PyPI. One-time setup is adding a `PYPI_TOKEN` repo secret.
- README rebranded around `csk briefing` as the killer use case; secondary positioning for ad-hoc questions.

## [Unreleased]

### Added — TUI, extensibility, HTTP mode, cost tracking
- **`csk tui`**: full-screen Textual interface. Chat pane (multi-turn with in-session memory) + live trace pane, F1 help, Ctrl-L to clear, Ctrl-Q to quit. Runs the agent in a worker thread so the UI stays responsive while Claude is thinking.
- **Plugin loader**: drop a Python file in `.ronin/plugins/` exposing `register_tools() -> list[Tool]` and it auto-loads. Broken plugins don't take down others — errors are surfaced via `csk plugins`. First-class extensibility without forking the kit.
- **`csk serve`**: exposes the configured agent as an HTTP API (`POST /ask`, `GET /health`). Pairs cleanly with the existing Vercel/Railway/Docker deployment templates — `docker compose up` and you have a real agent backend.
- **`csk costs`**: every `csk ask` / `csk chat` run now records token usage + cost to `.ronin/usage.jsonl`. `csk costs` shows total + per-model + per-day. Pricing table for Anthropic, OpenAI, Together, Groq, Fireworks, Ollama (free).
- **`csk plugins`**: discover and inspect loaded plugins.
- **vhs tape** at `scripts/demo.tape` — declarative terminal-recording script so a 30-second GIF for the README is one `vhs scripts/demo.tape` away.

### Added — earlier in Unreleased
- **Saved queries**: `csk save NAME "..."`, `csk run NAME`, `csk queries`, `csk unsave NAME`. Persists to `.ronin/queries.toml`.
- **Unified eval subcommand**: `csk eval run`/`drift` now built into the main `csk` binary (`csk-eval` still works for back-compat).
- **GitHub MCP server**: `GitHubReadOnlyTools` + `github_tools()` covering repos, issues, PRs, commits, code search. mcp-servers now ships 7 servers.
- **End-to-end examples**: `customer-support/` (Supervisor + 4 specialists + Pydantic `DraftReply`) and `code-reviewer/` (style/bugs/security specialists + typed `CodeReview`).
- **Tavily web-search MCP server** — `TavilyTools` + `tavily_tools()` factory.

### Repo stats
- 218 tests, green on every push.

## [0.1.0] — 2026-05-08

### Added — `csk` CLI

- New `ronin-cli` package shipping the `csk` binary.
- Subcommands: `csk init`, `csk ask`, `csk chat`, `csk tools`, `csk doctor`, `csk version`.
- Demo mode (`csk init --demo`) ships fake Stripe + Linear data; runs zero-config.
- Offline `demo_brain` keyword router so `csk ask` works without any API key.
- Rich terminal output: tables, panels, spinners.
- Prompt-injection scanning at the CLI boundary before any tool call.
- Auto-loads `.ronin/config.toml` (project-local) or `~/.config/csk/config.toml` (user-global); env-var overrides win over file values.

### Added — multi-provider support

- New `LLMProvider` abstraction in `agent-patterns`. Every pattern (`ReActAgent`, `PlannerExecutorAgent`, `SupervisorAgent`, `ReflexionAgent`) now accepts a `provider` kwarg.
- `AnthropicProvider` (default) — Claude.
- `OpenAICompatProvider` — OpenAI, Ollama, Together, Groq, Fireworks, vLLM, llama.cpp server, LM Studio, anything with `/chat/completions`.
- `OllamaProvider` convenience subclass — defaults to `http://localhost:11434/v1`, no API key needed.
- `FakeProvider` for tests.

### Added — modules

- `agent-patterns`: ReAct, Planner-Executor, Multi-Agent Supervisor, Reflexion.
- `eval-suite`: LLM-as-a-judge, golden datasets, drift detection, HTML reports, `csk-eval` CLI.
- `memory`: short-term (rolling summary), long-term (pluggable vector backend), user preferences.
- `hardening`: prompt-injection scanner, output-leak scanner, tool allowlist, approval gates, output validator with retry, PII-redacted tracing.
- `mcp-servers`: read-only Postgres, Stripe, Linear, Slack, Notion templates.
- `deployment-templates`: Docker Compose, Modal, Vercel, Railway one-click deploys.
- `apps/demo`: AgentLab — interactive FastAPI playground for all four agent patterns.
- `apps/docs`: Mintlify documentation site with eight content pages and Mermaid diagrams.

### Tests
- 152 tests across all packages, all passing on every push.

[Unreleased]: https://github.com/rohithkandula19/Ronin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rohithkandula19/Ronin/releases/tag/v0.1.0
