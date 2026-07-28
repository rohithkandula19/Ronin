# Safety model

ronin can write files and run shell commands. Its core promise: **nothing outward-facing happens without your approval**, and reads are free.

## The approval gate

Every action is classified (`approvals.py`) into one of three levels:

- **auto** — reversible and internal (a file edit inside the project). Applied without a prompt *only* if you've opted into auto-accept; otherwise you still get a `y/N`.
- **confirm** — external or irreversible (a shell command, a network/API call). **Always** asks `y/N`.
- **block** — moves money or is catastrophic (payments, `rm -rf`, `drop table`, `git push --force`, `mkfs`, fork bombs). **Never** auto-approvable — always asks, with a loud warning first.

The prompt is **default-deny**: an empty answer, EOF (no interactive stdin), or any read error returns "no". Only an explicit yes proceeds.

## Modes

- **normal** (default) — edits/commands are gated with a diff preview.
- **plan** (Shift+Tab, or `--plan`) — read-only: explore, don't mutate.
- **auto-accept** (Shift+Tab) — applies edits without a per-action `y/N` (still no payments/destructive-block bypass in the outward path).
- **`--full-access` / `--god-mode`** — lifts the guards for a trusted, unsandboxed run. Entered only via an explicit flag **and a loud on-screen warning**.

### `--god-mode` and the destructive floor

Under `--full-access`/`--god-mode`, the coding session auto-approves normal edits and commands without a per-action `y/N`. **But a destructive floor still stands:** a catastrophic `run_command` — `rm -rf`, `git push --force`, `drop table`, `mkfs`, `dd`, a fork bomb — is **never** silently auto-approved, even here. The gate shows a red block card (what · why · a safer alternative) and requires you to **type the phrase `run destructive`** to proceed; anything else is refused, and a headless (non-interactive) run can never confirm it. Normal commands under god-mode are unchanged. The chip strip shows `[god-mode] [DESTRUCTIVE FLOOR ACTIVE]` so the residual guarantee is always visible. Still: only run `--god-mode` in a directory you trust.

## Roles

Read-only roles — **researcher, reviewer, architect, verifier** — are *enforced*: the agent is filtered to read-only tools, so a review literally cannot edit. (They can still do read-only web lookups unless you're `--offline`.) Doer roles (implementer, tester, debugger) may act, still through the gate.

## The pipeline

`ronin pipeline` is sequential and gated. A commit/PR happens **only after a passing verdict** and an explicit `y/N`; `--dry-run` changes nothing; checkpoint restore always confirms before overwriting and never resets/stashes silently.

The agent-facing checkpoint tools are equally conservative: `preview_rewind` shows
the files a rewind would restore or remove without changing the tree, and every
actual `rewind` first creates a recovery checkpoint. The rewind result names
that checkpoint so the restore itself can be reversed.

## Patch Preflight

Before the coding tools write Python source, Ronin parses the complete proposed
file and refuses a syntax error without touching the existing file. It also
reports Python public API removals. TypeScript is parsed when the project has a
local compiler; otherwise Ronin says it was not checked. This is structural
guarding only, not proof of behavior: use the repository's test suite for the
actual verification signal. See [coding_engine.md](coding_engine.md).

## Keys & data

Provider keys live in a local TOML (`.ronin/config.toml` or `~/.config/ronin/config.toml`), plaintext, never sent anywhere except the provider you chose. There is **no telemetry**. `--offline` strips every network tool so nothing leaves the machine.

See `docs/release/v1_security_review.md` for the full audited checklist.
