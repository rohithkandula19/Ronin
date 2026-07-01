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

### ⚠️ `--god-mode` boundary (read this)

Under `--full-access`/`--god-mode`, the coding session's `run_command` **auto-approves any command, including `rm -rf`**, unless you've added a standing `deny` rule. The "destructive is always BLOCK" guarantee applies to the outward-action path, not the yolo coding shell. **Only run `--god-mode` in a directory you fully trust.** (Non-yolo sessions gate every command.) A future release will add a destructive hard-floor even under yolo.

## Roles

Read-only roles — **researcher, reviewer, architect, verifier** — are *enforced*: the agent is filtered to read-only tools, so a review literally cannot edit. (They can still do read-only web lookups unless you're `--offline`.) Doer roles (implementer, tester, debugger) may act, still through the gate.

## The pipeline

`ronin pipeline` is sequential and gated. A commit/PR happens **only after a passing verdict** and an explicit `y/N`; `--dry-run` changes nothing; checkpoint restore always confirms before overwriting and never resets/stashes silently.

## Keys & data

Provider keys live in a local TOML (`.ronin/config.toml` or `~/.config/ronin/config.toml`), plaintext, never sent anywhere except the provider you chose. There is **no telemetry**. `--offline` strips every network tool so nothing leaves the machine.

See `docs/release/v1_security_review.md` for the full audited checklist.
