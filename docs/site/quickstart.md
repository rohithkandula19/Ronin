# quickstart

Five minutes from a clone to a turn you can read. Hand-written, and every flag on this
page comes from `ronin.cli.main.build_parser` — run `python -m ronin --help` if you
want the machine's copy.

## 1. install

```sh
pip install ronin
```

Zero hard dependencies, on purpose: a bare install is a working agent. Three extras
each *add* a capability rather than repair a broken one.

| extra | what it enables | when you need it |
|---|---|---|
| `ronin[http]` | real sockets: provider HTTP/SSE streaming, MCP network transports | any hosted model, and any local model behind an HTTP server |
| `ronin[tui]` | the interactive Textual view | you want the full-screen session; the line session works without it |
| `ronin[repomap]` | tree-sitter repo maps for languages other than Python | your repo is not mostly Python (the stdlib `ast` parser is exact for Python and needs nothing) |

If an extra is missing, the feature that needed it says so by name and the session
continues. It does not raise an `ImportError` traceback at you.

## 2. point it at a model

Ronin ships **no** default provider, model id or price. That is deliberate: a built-in
default would have to state a per-million-token price, and a made-up price puts a
wrong number in your cost report.

```sh
mkdir -p .ronin
cp examples/models.toml .ronin/models.toml
$EDITOR .ronin/models.toml
```

The config is searched in this order, first hit wins:

1. `$RONIN_MODELS`, if set, pointing at a file
2. `<workspace>/.ronin/models.toml`, then `<workspace>/models.toml`
3. `~/.ronin/models.toml`, then `~/models.toml`

The smallest config that works — one role, one model:

```toml
[roles]
main = "my-model"

[models.my-model]
provider = "ollama"          # a local server; no key needed
model = "<whatever `ollama list` shows>"
native_tools = false
```

Keys are never written in this file. `api_key_env` names an environment variable
instead. See [providers.md](providers.md) for every provider, including both local
paths.

## 3. check the wiring before spending anything

```sh
python -m ronin doctor
```

It reports every path, which settings layer set each scalar, whether the configured
role resolves to a defined model, and whether the named key variable is actually
present. It does **not** call the provider — a diagnostic that hangs for thirty
seconds is a diagnostic people stop running — so it costs nothing and needs no
network.

Every finding that is not `OK` carries a remedy. That is enforced in code: a `Check`
with a non-ok status and no remedy cannot be constructed.

## 4. run one turn

Interactive, in a terminal:

```sh
python -m ronin "why does test_resume fail on windows"
```

Headless, for a script or a CI job:

```sh
python -m ronin -p "why does test_resume fail on windows" --output-format text
```

Three output formats, and the difference matters when something is consuming the
output:

| `--output-format` | behaviour |
|---|---|
| `text` | the final answer on stdout, nothing else. Notices — a denied approval, an error — go to **stderr**, so a consumer piping stdout gets the answer and no warnings mixed into their data. |
| `json` | nothing until the end, then one `result` object. |
| `stream-json` | one JSON object per line, flushed per event, so `python -m ronin -p … \| jq` shows progress while the turn is still running. A final `result` record closes the stream. |

Passing `--output-format` implies headless: an explicit machine format is a statement
that something is parsing this output, which only makes sense with no terminal in the
way.

## 5. read the exit code

This is the contract a script depends on, and it comes from `ronin.ui.headless`:

| code | meaning |
|---|---|
| `0` | the turn finished with no errors and no approval requests |
| `1` | an error — including a stream that ended without a `TurnEnd`, because a truncated stream is a failure, not a success |
| `2` | no error, but at least one approval was requested and **denied** |

An error outranks a denial: a script checking `!= 0` must see both, and the errored
run is the more severe outcome.

`2` is the one people misread. A headless run has no human attached, so it denies
every gated call and exits `2` rather than guessing. There is no flag that turns that
off — an unattended run that auto-approves a destructive tool is the failure the whole
permission layer exists to prevent. If you get `2`, either the task genuinely needs a
human, or you need a rule in `.ronin/settings.json` that allows the specific call (see
[config.md](config.md)).

A usage error also exits `1`, not argparse's conventional `2`, because `2` already
means "needs approval" to every script consuming this stream, and one number meaning
two things is worse than deviating from argparse.

### the `2` that is not a denial

**In this build, `2` is also returned when an approval was requested and then
*granted*.** `ronin.core.loop` yields the `ApprovalRequest` event before it consults the
policy — a UI has to render the prompt in order to ask — and
`ronin.ui.headless.exit_code_for` counts requests rather than refusals. So a clean
`--mode auto_edit` run that writes one file exits `2`, and the `result` record lists that
write under `approvals_denied` even though its own `tool_end` says `"ok": true`. Verified
on this checkout:

```json
{"type":"approval_request","tool_use_id":"t1","name":"write","danger_level":"mutating"}
{"type":"tool_end","tool_use_id":"t1","name":"write","ok":true,"error":""}
{"type":"result","exit_code":2,"stop_reason":"no_tool_calls","errors":[],
 "approvals_denied":[{"tool_use_id":"t1","name":"write"}]}
```

Until that is fixed, a script that must distinguish the two reads the stream instead of
the code. A genuine refusal is a `tool_end` whose `error` starts with `DENIED:`:

```sh
python -m ronin -p "…" --output-format stream-json > run.jsonl
jq -r 'select(.type=="tool_end" and (.error|startswith("DENIED:"))) | .name' run.jsonl
jq -r 'select(.type=="result") | "\(.stop_reason) errors=\(.errors|length)"' run.jsonl
```

`no_tool_calls` is the clean finish. `max_iterations`, `cost_budget`, `token_budget`,
`stalled` and `interrupted` all mean the turn ran out of something rather than finished.
`examples/workflows/ci-failure-fixer/` does exactly this, and keeps working either way —
a refusal stays a `DENIED:` result whatever the exit code becomes.

## 6. permission modes

```sh
python -m ronin -p "explain the retry ladder" --mode plan
```

| `--mode` | what it allows |
|---|---|
| `plan` | read and reason. Every mutating tool is **removed from the registry**, not merely discouraged — a model told not to edit edits three turns later; a model with no `write` in its registry cannot. |
| `ask` | the default. Anything gated asks first. |
| `auto_edit` | file edits proceed without asking; shell and destructive calls still ask. |
| `full` | ask for as little as the rules permit. |

`--yolo` is different in kind from a mode, and the CLI's own one-line help understates
it. A mode can only *relax an `ask` that came from the rules* — it cannot touch a
floor, so the deny list, structural command hazards, taint escalation and plan mode all
survive `--mode full`. `--yolo` **waives the deny list itself**
(`ronin.safety.denylist.Denylist.yolo` makes every check return no hits), which means
`rm -rf /`, a fork bomb and reading private key material stop being refused. It is a
decision to make once, loudly, about a throwaway container — not a convenience for a
repository you care about.

Prefer a narrow allow rule in `.ronin/settings.json`: a rule is attributable to a file,
reviewable in a diff, and visible in `python -m ronin doctor`. Rules marked
`always_ask` keep asking under every mode, which is the right tool for "let it run the
test suite, keep asking before it touches the database".

## 7. budgets, so a runaway turn is bounded

```sh
python -m ronin -p "fix the failing test" \
  --max-turns 12 --max-usd 0.50 --max-seconds 300
```

`--max-tokens`, `--max-usd` and `--max-seconds` are session ceilings; `--max-turns` is
how many iterations one turn may take. All four are off unless you pass them — "the
user set no limit" and "the user set a limit that happens to be unbounded" stay
distinguishable.

Cost figures come from the prices *you* wrote in `models.toml`. A model with no prices
set is recorded as unpriced and flagged, not treated as free.

## 8. sessions, resume, export

```sh
python -m ronin sessions                     # what is recorded here, newest first
python -m ronin -c                           # continue the newest session
python -m ronin --resume <id>                # continue a specific one
python -m ronin export <id> -o run.md        # markdown; --format html for html
```

A resumed session reports every caveat the replay carried — a synthesized tool result,
a repaired record — because a resumed session with three synthesized results is not
the same thing as a clean one, and a user who is not told reads the difference as the
model forgetting.

`--no-record` skips the transcript for a run you do not want on disk.

## 9. let something else drive it (MCP)

Ronin is an MCP *server* as well as a client, so Claude Desktop, Cursor or a second Ronin
can launch this one and call its tools:

```sh
python -m ronin mcp-serve                    # read, grep, glob
python -m ronin mcp-serve --mode auto_edit   # … and edit
python -m ronin mcp-serve --mode full        # … and bash, and ronin_task
```

It speaks JSON-RPC on stdin and stdout, which is why `--mode` decides what is exposed
rather than a separate flag: **there is no human on the other end of stdin to approve
anything**, so only the tools this mode does not need one for are published. A gated tool
that appeared in the list and then refused every call would just teach the client's model
to keep trying it. Whatever is withheld is named on stderr, with the flag that would
expose it.

`ronin_task(prompt)` is the interesting one: it runs a full nested Ronin loop — planning,
searching, editing, verifying — and returns the summary, so the client gets a whole task
done rather than one file read.

In Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ronin": {
      "command": "ronin",
      "args": ["mcp-serve", "--mode", "auto_edit", "--cwd", "/path/to/your/repo"]
    }
  }
}
```

Everything a served tool does still goes through this session's permission rules, deny
list, hooks and output budget. `--mode full` waives the *prompt*; it does not waive the
unconditional refusals.

Ronin as a *client* of other servers is `.ronin/mcp.json`, and its tools arrive named
`mcp__<server>__<tool>`.

## 10. what to configure next

* `.ronin/settings.json` — permission rules and modes: [config.md](config.md)
* `.ronin/hooks.json` — shell commands on lifecycle events, and the exit-code-2 block:
  [hooks.md](hooks.md)
* `.ronin/agents/*.md` — subagents with their own tools and prompts:
  [subagents.md](subagents.md)
* `examples/workflows/` — three copyable workflows (pre-commit reviewer, CI failure
  fixer, repo onboarding explainer)

## a first run writes nothing without asking — almost

The first time you run in a workspace with no `.ronin/`, Ronin shows what it *would*
create and asks. Answering no is respected and the session continues on defaults.
`--no-wizard` skips the question entirely.

Two things are created regardless of the wizard, and both are worth knowing about if the
repository is not yours. A single `python -m ronin doctor --cwd <target>` on a fresh git
repo produced:

```
<target>/.ronin/cache/repomap-<hash>.json   the repo-map parse cache
<target>/.ronin/checkpoints/                a shadow git repository (git init)
```

The repo map caches its parse when the workspace loads; the checkpoint store initialises
a *separate* git repository so it can snapshot before a mutating turn, and it does that
on probe. Neither is inside the target's own history, but both show up as untracked in
`git status`. `--no-record` is why `.ronin/sessions/` is not in that list. If touching
nothing matters, work on a clone.

Telemetry is off by default and sends nothing. The one-line disclosure appears once;
[telemetry.md](telemetry.md) shows the entire payload, field by field, and how to read
the local log of anything that was sent.

## known rough edges, named rather than hidden

* **`python -m ronin`, not `ronin`.** The `ronin` console script belongs to
  `packages/cli` (the v1 CLI). Repointing a shipped command at a different program
  would silently change what people already have installed, so the v2 app is reached
  as a module.
* **Slash commands are only partly wired in the line session.** `/help`, `/doctor`,
  `/clear`, `/cost`, `/diff` and `/undo` work. Any other command — including
  user-defined ones in `.ronin/commands/*.md` — is reported by name as not wired
  rather than silently doing nothing. Those markdown files still load (and
  `python -m ronin doctor` counts them), so the way to use one today is to pass its
  body as a prompt: `python -m ronin -p "$(cat .ronin/commands/onboard.md)"`.
* **The Textual view is one turn.** `ronin.ui.app.Session` consumes a single event
  stream and has no input widget, so a prompt on argv gets the full-screen view for
  that turn, and a session with no prompt gets the line REPL, which can actually read
  the next request.
* **Exit code `2` over-reports.** See [above](#the-2-that-is-not-a-denial): it fires for
  approvals that were granted, not only refused.
* **No web tools on the command line.** `web_fetch` and `web_search` exist but are only
  built when a fetcher, extractor and searcher are injected, which `ronin.cli.wire` does
  not do. A CLI session has 11 tools, not 13 — [tools.md](tools.md) derives the list
  rather than asserting it. Reaching the web today means an MCP server.
* **Telemetry is off, and there is nowhere for it to go.** `ronin2 telemetry
  status|on|off|show` is wired now, but **no endpoint is configured in this build**:
  `record()` returns `no_sender` and never opens a socket, and `status` says so rather
  than claiming outcomes are being sent. `show` prints the local audit log verbatim, so
  the claim is checkable rather than merely stated. See [telemetry.md](telemetry.md).
* **`python -m ronin doctor` prints a Python repr for enum scalars** — `mode =
  <Mode.ASK: 'ask'>` rather than `mode = 'ask'`. Cosmetic, but it is the first output a
  new user sees.
* **`--yolo`'s one-line help understates it.** `--help` says "the unconditional deny list
  still applies"; `ronin.safety.denylist.Denylist.yolo` makes every deny check return no
  hits. Trust the code, not the help text, until they agree.
