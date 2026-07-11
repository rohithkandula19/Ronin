# Secret Rotation Checklist

Standing procedure for keeping credentials out of the Ronin repo and rotating
anything that could have leaked. **This document never contains secret values,
and rotation is always performed by a human, never automatically.**

## Current status (verified during RC hardening, Phase 0)

A read-only audit of the repository at the `hardening/rc-phase0` HEAD found:

- **`.env.local` is NOT tracked** and was **never committed** in any branch's
  history (`git log --all -- '*.env.local'` returns nothing).
- **`csk_saas.db`, `job_bot.db`, and `.ronin/` are NOT tracked.**
- No non-example `.env` file is tracked.
- A tracked-file secret scan (provider keys, GitHub tokens, AWS keys, Slack
  tokens, private keys) found **no real secret**. The only pattern match is
  `packages/cli/src/ronin_cli/secret_guard.py`, which is the secret *scanner's*
  own regex definitions, not a credential.
- `.gitignore` covers `.env`, `.env.local`, `.env.*.local` (with
  `!.env.example`), `csk_saas.db`, `csk_saas.db-journal`, and `.ronin/`.

**Conclusion:** no committed secret was found, so no emergency rotation is
required right now. The earlier Stage A note about a "committed `.env.local`"
did not reproduce against HEAD or history. Keep this checklist for the general
case below.

## Where Ronin secrets live (all local, git-ignored)

| Secret | Location | Rotate at |
|---|---|---|
| Provider API keys (Anthropic / OpenAI / Gemini / Groq / Cerebras / OpenRouter) | `~/.ronin/config.toml`, `.env` | the provider's console |
| Telegram bot token | `TELEGRAM_BOT_TOKEN` env / `telegram.env` | @BotFather (`/revoke`) |
| Relay shared token | `RONIN_RELAY_TOKEN` env | regenerate (>= 24 chars) on both ends |
| Gmail / OAuth tokens | local token store | Google Cloud console, revoke + reissue |

## If a secret is ever committed

Order matters. **Rotate first — a `git rm` alone leaves the value live in
history and in every clone.**

1. **Rotate the credential at its provider immediately.** Treat the old value as
   compromised the moment it touched a commit, even a private repo. This is the
   only step that actually protects you; everything below is cleanup.
2. **Remove it from the working tree** and confirm the path is in `.gitignore`.
3. **Verify it is gone from HEAD:** `git ls-files | grep -i <name>` returns
   nothing; a fresh secret scan is clean.
4. **History scrub (APPROVAL-GATED, human-run, never automatic):** rewriting
   history (`git filter-repo` / BFG) is destructive and force-pushes over
   published history. Do it only with explicit owner approval and coordinate
   with anyone who has a clone. Ronin's tooling will not rewrite history on its
   own (Absolute Rule 12).
5. **Invalidate downstream:** revoke sessions/derived tokens that used the old
   secret; rebuild any cache or artifact that embedded it.

## Prevention

- Keep all real values in `~/.ronin/` or a git-ignored `.env`; commit only
  `*.example` templates.
- Run `ronin hook` to install the pre-commit secret-scan hook, so a secret is
  blocked before it can be committed.
- Never paste a real key into an issue, PR, log, or screenshot.
