# Use Ronin from your phone (Telegram)

Status: working feature with offline tests. It is a thin bridge you run yourself,
not a hosted product. There is no server to deploy and no inbound port to open.

`ronin telegram` lets you message a Telegram bot and get answers from a
READ-ONLY file agent. It can read and search your files to answer questions about
your projects, but it cannot edit files or run commands. The laptop dials OUT to
Telegram and long-polls for messages, so it works behind NAT with no public
hostname.

## How it works

1. You make a bot with @BotFather and get a token.
2. You run `ronin telegram` on the laptop. It calls getUpdates in a long-poll
   loop (outbound only).
3. For a message from an allowed chat id, it runs a read-only code agent
   (`run_code_agent(..., read_only=True)`, the same path consensus uses) rooted
   at a configured directory, then sends the answer back with sendMessage.

The laptop opens no inbound port. The bot needs no public URL.

## What it can see

The bot answers with a read-only file agent, not a blind chat model.

- Read-only file access. The agent gets the read tools only: `read_file`,
  `list_files`, `search_files` (grep), and `glob`. It has NO write, edit, or
  shell tool. It cannot change anything or run a command.
- Rooted at a directory. The agent is confined to one root, set by the
  `RONIN_TELEGRAM_ROOT` env var. Default is your home directory (`~`). The path
  is resolved once at startup. Paths outside the root are refused.
- Secret-path guard. Even inside the root, reads of obviously sensitive paths are
  refused so a stray request cannot dump a secret into your Telegram history.
  Blocked: anything under `~/.ssh`, `~/.ronin`, `~/.aws`, `~/.gnupg`,
  `~/.config/gh`, `~/.netrc`; any path component named like a dotfile-secret
  directory; and any filename matching `*.pem`, `*.key`, `*_rsa`, `id_*`,
  `*.env`, `.env`, `credentials*`, or `*secret*token*`. A blocked read returns
  `refused: that path is protected` instead of the contents.
- Still no edits or commands. Read-only means read-only. There is no write/edit/
  shell tool reachable from a message.
- Still allowlisted. Only a chat id in the allowlist can drive the agent at all
  (see below).

To use a narrower root than your whole home directory:

```bash
export RONIN_TELEGRAM_ROOT="$HOME/projects"
```

## Make a bot with @BotFather

1. In Telegram, open a chat with `@BotFather`.
2. Send `/newbot`. Pick a name and a username (the username must end in `bot`).
3. BotFather replies with a token that looks like
   `123456789:AAExampleSecretTokenValue`. Keep it secret; it controls the bot.

## Configure

Two settings. The token is required. The allowlist decides who can drive the
agent.

```bash
export TELEGRAM_BOT_TOKEN="123456789:AAExampleSecretTokenValue"
# comma-separated chat ids that are allowed to run the agent:
export TELEGRAM_ALLOWED_CHAT_IDS="42,777"
```

You can also set the allowlist in the config file as `telegram_allowed_chat_ids`
(a list of ints). The env var and any `--allow` flags are merged on top of it.

If you do not know your chat id yet, leave the allowlist empty, start the bot,
and message it. It replies with `your chat id is <id>` and runs the agent for
nobody. Add that id to `TELEGRAM_ALLOWED_CHAT_IDS` and restart.

## Run

```bash
ronin telegram                       # long-poll forever; Ctrl+C to stop
ronin telegram --allow 42            # add a chat id just for this run
ronin telegram --once                # one poll then exit (for testing)
ronin telegram --poll-timeout 50     # longer long-poll window
```

On start it validates the token, calls getMe, and prints:

```
ok bot @your_bot ready; allowed chats: [42, 777]
Read-only file access, rooted at /Users/you. I can read and search your files but cannot edit or run commands.
```

## Safety model

This is remote control of a laptop, so the boundary is the whole point.

- Token required, fails closed. Without `TELEGRAM_BOT_TOKEN` (or with an
  obviously malformed one) the command exits non-zero and never polls.
- Allowlist gates every message. The agent runs only for a chat id in the
  allowlist. Any other chat is ignored. An EMPTY allowlist runs the agent for
  nobody; the bot only replies with the chat's numeric id so you can add it
  (safe onboarding), and logs it.
- Read-only file access only. Messages go through `run_code_agent(...,
  read_only=True)`, the same read-only path consensus uses. The agent gets the
  read tools (read/list/grep/glob) and NO write, edit, or shell tool. No
  `--full-access`. A Telegram message cannot trigger a destructive action.
- Confined to a root. The agent only sees files under `RONIN_TELEGRAM_ROOT`
  (default: your home dir). Paths outside it are refused.
- Secret-path guard. Reads of obviously sensitive paths (`~/.ssh`, `~/.aws`,
  `*.pem`, `*.key`, `id_*`, `*.env`, `credentials*`, ...) are refused, so a stray
  request cannot leak a secret into your chat history. See "What it can see".
- No arbitrary shell. There is no shell, eval, or exec path reachable from a
  message. The only outbound calls are to api.telegram.org.
- Token never logged. The token is part of every request URL, and HTTP errors
  (a 429/401/5xx) carry that URL. The bridge masks the token as `<redacted>`
  before any error is logged or printed, so a rate limit or auth failure does
  not leak the secret to logs or the terminal.

## Notes

- Replies longer than ~4000 chars are split across multiple messages.
- Telegram HTTP errors get a short backoff and the loop keeps polling, so a
  transient network blip does not kill the bridge.
- Tests are fully offline: the Telegram HTTP calls are mocked and never hit the
  network. See `packages/cli/tests/test_telegram_bot.py`.
