# VERIFY.md — run-it-yourself checklist for ronin's new commands

The headless test harness
(`packages/cli/tests/test_integration_smoke.py`) already proves, automatically,
that every new command's **code path runs without crashing** when the LLM, the
network, the terminal, and `~/.ronin` are mocked. Run it any time with:

```bash
cd "$(git rev-parse --show-toplevel)"
uv run pytest packages/cli/tests/test_integration_smoke.py -q
```

What a machine **can't** judge is the live UX — colours, arrow-key handling,
whether a rendered heatmap actually looks right, whether a grounded "do" run
feels safe in real time. That's this document. Run each command in a **real
terminal** and check it against the "✅ good" line.

> Assumes `ronin` is on your PATH (or use `.venv/bin/ronin`). All commands are
> safe: the new surfaces are read-only or stop before any money/destructive
> action. Nothing here pays, deletes, or sends.

---

## Group A — Should just work (no model, no network, no tokens)

These need nothing configured. They render locally and read only your own
machine.

### 1. `ronin play` — the arcade

```bash
ronin play            # the picker menu
ronin play 2048       # jump straight into a game
ronin play snake
```

- ✅ **good:** the menu lists **26+ games** with emoji + names. Picking one (by
  number or key) launches it. **Arrow keys / WASD move**, the board redraws in
  place (no scroll spam), and **`q` / Ctrl-C quits cleanly** back to the prompt
  with no traceback.
- 🔎 watch for: a frozen board, keys that don't register, or the screen scrolling
  instead of redrawing in place.

### 2. `ronin stats` — usage dashboard

```bash
ronin stats
```

- ✅ **good:** a header wordmark, a **row of stat cards** (Sessions, Messages,
  Total tokens, Active days, Current/Longest streak, Peak hour, Favorite model),
  a **GitHub-style contribution heatmap** (teal squares, "less → more" legend),
  and a playful "you've written N tokens ≈ Xx <book>" line + the red-panda
  wordmark.
- ✅ on a **fresh machine** (no history): a friendly welcome panel with the panda
  and "go run `ronin code`!" — **never** a wall of zeros or a crash.
- 🔎 watch for: misaligned heatmap columns, cards that wrap badly at narrow
  widths (try resizing the terminal).

### 3. `ronin profile` / `ronin xp` — gamified coding

```bash
ronin profile                 # the dashboard (level, XP bar, streak, badges)
ronin xp test_passed          # award XP for an action, see what changed
ronin xp bug_fixed
ronin profile                 # confirm the XP/streak/badges moved
```

- ✅ **good (`profile`):** a rounded "ronin · the way" panel with **⚔ LV n +
  title**, a teal/green **XP bar**, a **🔥 streak** gauge, **🏅 Badges (earned /
  total)**, and a recent-events list.
- ✅ **good (`xp`):** prints the XP gained, the new total/level, whether you
  **leveled up**, the streak, and any **newly-unlocked badge** (e.g. first
  `test_passed` → "Green Thumb"). Re-running the same day must **not** grow the
  streak; a *new* day must.
- 🔎 note: writes to `~/.ronin/gamify.json` (or `$RONIN_HOME`). This is the one
  Group-A command that persists state — it's yours, local, and safe to delete.

### 4. `ronin privacy` — data-safety audit

```bash
ronin privacy
```

- ✅ **good:** a table of everything ronin stores locally (credentials / memory /
  sessions / cache) with size, **encrypted vs plaintext**, and a **"plaintext
  key?"** column. A one-line verdict: **`✓ SAFE`** if no raw API keys are sitting
  unencrypted, or **`⚠ AT RISK`** with remediation tips if one is.
- ✅ **critical:** it must **flag a plaintext key** if you have one in
  `config.toml` — but it must **never print the key's value**, only the prefix
  (e.g. `sk-…`). Verify no secret bytes appear anywhere in the output.
- 🔎 to exercise the "AT RISK" path: temporarily put a dummy
  `api_key = "sk-AAAAAAAAAAAAAAAAAAAAAAAA"` line in a `.ronin/config.toml` and
  re-run; confirm it's flagged and the value is **not** echoed. Remove it after.

### 5. `ronin vault lock` / `unlock` — encryption at rest

```bash
echo 'api_key = "sk-test-DELETE-ME"' > /tmp/secret.toml
ronin vault lock /tmp/secret.toml      # prompts for a passphrase
ronin privacy                          # (optional) see it as encrypted
ronin vault unlock /tmp/secret.toml    # same passphrase → restored
cat /tmp/secret.toml                   # back to plaintext
rm /tmp/secret.toml
```

- ✅ **good:** `lock` prompts for a passphrase and rewrites the file as an opaque
  blob. `unlock` with the **same** passphrase restores the **exact** original
  bytes. A **wrong passphrase is rejected** (clear error, file left untouched) —
  never silent garbage.
- 🔎 try the wrong passphrase deliberately and confirm the file is unharmed.

### 6. `ronin recall` — search past sessions (local, redacted)

```bash
ronin recall "retry http client"
```

- ✅ **good:** ranked matches from your past sessions with a snippet + session id,
  best first, and "replay one with `ronin replay <id>`". On a fresh machine: a
  calm "no matching sessions found." — no crash, no network.
- 🔎 the index is built locally and **redacts secrets before writing** — snippets
  should never contain a raw `sk-`/`xoxb-`/email.

### 7. `ronin skill search` — shareable skill registry

```bash
ronin skill search            # list everything in your local registry
ronin skill search endpoint   # filter by name/summary/author
```

- ✅ **good:** a table of registry skills (name · author · summary), or an empty
  list with no error when the registry is empty. No network — it reads
  `~/.ronin/skills/` (or `$RONIN_SKILL_REGISTRY`).
- 🔎 to see a full round-trip: crystallize a skill in a repo
  (`.ronin/commands/<name>.md`), `ronin skill publish <name>`, then
  `ronin skill search` should show it. `skill install` **refuses** anything that
  looks like an executable payload (the safety gate) — that rejection is the
  feature.

---

## Group B — Needs a model configured (a provider key / local model)

These actually call the LLM. Configure a provider first (e.g.
`ronin set-key anthropic …`, or point at a local `ollama`). Without creds they
should **fail gracefully with a clear message**, not hang or stack-trace.

### 8. `ronin route "<task>"` — cost-aware auto-router

```bash
ronin route "rename a helper function"                 # → a cheap blade
ronin route "refactor the auth architecture for thread-safety"   # → a strong blade
```

- ✅ **good:** prints a **routing decision line** first, e.g.
  `→ routing a [cheap] task to cerebras:… — cheapest above the bar (reliability
  ~90%, $0.00/Mtok)`, then runs the read-only ask agent on the chosen blade, then
  a **cost line** (`cost: $… · saved $… vs <strong baseline>`).
- ✅ the **task class matches intuition**: cosmetic/short → `[cheap]`; deep /
  correctness-critical / long → `[hard]`; in-between → `[standard]`.
- ✅ **no creds for the chosen provider** → a clean
  `✗ no credentials for provider … — run ronin login …`, **never** a crash.
- 🔎 run it a few times; the router **learns** per-repo reliability, so the blade
  it picks for a class can shift as it gathers samples.

### 9. `ronin do "<real-world task>"` — universal action engine (the safety star)

```bash
ronin do "order me a pizza"
```

- ✅ **good — the full safe flow:**
  1. Prints the task + vertical + a loud **"ronin never pays — you confirm and
     pay yourself."** line up front.
  2. **Researches real options (grounded)** and shows them with sources/links.
  3. If the options **can't be grounded** (possible made-up price/availability)
     it **REFUSES to act** and stops — this is the anti-hallucination guard.
  4. If grounded, it shows the plan and **asks your approval on EVERY step**
     (research → choose → fill cart). Each step is a y/N gate.
  5. It **HARD-STOPS at the payment step** — prints `── STOP: payment step ──`,
     repeats "ronin never pays…", and hands you the **checkout link to finish
     yourself**. It must **never** click pay.
- 🔎 the must-not-happen list: it auto-pays, it acts after the payment step, or it
  proceeds on ungrounded options. Any of those is a bug. Decline a step midway
  and confirm it stops cleanly.

### 10. `ronin swebench -m <models>` — objective coding leaderboard

```bash
ronin swebench -m anthropic
ronin swebench -m anthropic,gemini,cerebras       # compare several
```

- ✅ **good:** each model fixes a battery of bundled bug "issues"; every fix is
  graded by **actually running the test** (exit code, **no LLM judge**), then a
  **leaderboard** (`model · pass-rate · avg-cost · avg-time`) sorted by
  pass-rate, with a "▶ best:" line.
- 🔎 this makes **several real model calls per model** — it costs tokens and takes
  a bit. The *grading* itself is local/Docker-free. A dead/misconfigured model
  shows as an `err` row and sinks to the bottom rather than crashing the board.

### 11. `ronin mcp serve` — ronin as an MCP server

```bash
# It speaks JSON-RPC 2.0 over stdio. Drive it by hand:
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ronin mcp serve
```

- ✅ **good:** replies with a JSON line advertising exactly **three read-only
  tools** — `ronin_ask`, `ronin_consensus`, `ronin_research` — each with an input
  schema. **No** edit/write/shell/exec tool is ever exposed.
- ✅ a `tools/call` for `ronin_ask` (needs a model) returns a text answer;
  unknown methods return a JSON-RPC error, not a crash.
- 🔎 the realistic test is wiring it into an MCP client (Claude Desktop / Cursor /
  another ronin) and confirming the three tools appear and are callable. The
  `tools/list` handshake above needs **no** model; actually *calling* a tool needs
  a provider configured.

---

## Group C — Needs Docker / a bot token (infra)

### 12. `ronin gateway` — drive ronin's read-only agent from chat

```bash
export RONIN_GATEWAY_SECRET="$(openssl rand -hex 16)"   # required to mint pairing codes
export TELEGRAM_BOT_TOKEN="<your @BotFather token>"      # required for the telegram channel
ronin gateway --channel telegram --once                 # poll once and exit (for testing)
```

- ✅ **good:** with the token + secret set, it polls and reports
  `gateway polled telegram; handled N message(s)`. Message a non-allowlisted
  account → it replies with a **pairing code** to read to you, and runs the agent
  for **nobody** until you add an id (fail-closed). An allowlisted sender's
  message routes to the **read-only** ask agent only — **never** edit/shell.
- ✅ **missing token/secret** → it **refuses to start** with a clear message
  (`TELEGRAM_BOT_TOKEN is not set…`), not a silent no-op.
- 🔎 must-not-happen: a chat message triggering any write/edit/shell action, or an
  empty allowlist running the agent for anyone. The gateway opens **no inbound
  port** (outbound long-poll only).

### 13. `ronin vault` + Docker/SSH backends — isolation (advanced)

The pluggable execution backends (`backends.py`: `local` / `docker:<container>` /
`ssh:user@host`) let the coding agent's shell run **inside a disposable Docker
container or a remote host** instead of on your machine. There's no standalone
CLI for it here (it wires through the coding agent's `--backend`), but if you use
it:

- ✅ **good:** with `docker:<name>` the agent's commands run via
  `docker exec` inside that container; with `ssh:user@host` they run on the remote
  host. Your **host filesystem stays private** unless you explicitly mounted it.
- 🔎 needs Docker running (or SSH reachable). The host field is validated against
  injection (`box; rm -rf /` is rejected). The default is always `local` — nothing
  changes until you opt into a non-local backend.

---

## One-glance summary

| Command | Group | Needs | "Good" in one line |
|---|---|---|---|
| `ronin play [game]` | A | nothing | 26+ games, arrows move, q quits clean |
| `ronin stats` | A | nothing | stat cards + heatmap render; empty state is friendly |
| `ronin profile` | A | nothing | level/XP bar/streak/badges panel |
| `ronin xp <event>` | A | nothing | XP/level/streak/badge changes print |
| `ronin privacy` | A | nothing | flags plaintext keys, never prints the value |
| `ronin vault lock/unlock` | A | nothing | exact round-trip; wrong passphrase rejected |
| `ronin recall "<q>"` | A | nothing | ranked redacted snippets, or calm empty state |
| `ronin skill search [q]` | A | nothing | registry table; install refuses unsafe skills |
| `ronin route "<task>"` | B | a model | routing line → run → savings; no-creds fails clean |
| `ronin do "<task>"` | B | a model | grounded options, gate each step, **stops before paying** |
| `ronin swebench -m …` | B | a model | objective leaderboard graded by running tests |
| `ronin mcp serve` | B | model (to call) | exposes 3 read-only tools; no write/shell tool |
| `ronin gateway` | C | bot token + secret | pairing-code, read-only, fail-closed |
| docker/ssh backends | C | Docker/SSH | agent shell runs in a disposable sandbox |
