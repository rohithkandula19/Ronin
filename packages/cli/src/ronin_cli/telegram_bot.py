"""Drive Ronin from your phone over Telegram, outbound-only and allowlisted.

This is the laptop side of a Telegram bridge. It long-polls the Telegram Bot
API for messages and, for messages from an allowed chat, runs the SAME
read-only / ask agent path that `ronin ask` uses, then sends the answer back.

Why this shape:
- OUTBOUND ONLY. The bot dials OUT to api.telegram.org via getUpdates long-poll.
  It opens no inbound port and needs no public hostname, so it works behind NAT.
- ALLOWLISTED. It acts only on messages whose chat id is in an explicit
  allowlist. Any other chat is ignored. An empty allowlist runs the agent for
  nobody; it only replies with the chat's numeric id so the owner can add it.
- READ-ONLY. It runs the SAME read-only code-agent path consensus uses
  (`run_code_agent(..., read_only=True)`), so it can read and search files under
  a configured root but has NO write / edit / shell tool. No `--full-access`. A
  Telegram message cannot trigger a destructive action. There is no shell / eval
  / exec path in this module.
- SECRET-GUARDED. Reads of obviously sensitive paths (~/.ssh, ~/.aws, key/env
  files, ...) are refused so a stray request cannot dump a secret into the chat.

httpx is imported LAZILY inside function bodies so importing this module (and
the core CLI) stays light and offline.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("ronin.telegram")

API_BASE = "https://api.telegram.org"

# Telegram caps a single sendMessage text at 4096 chars. Stay under it so we
# never get a 400 for an over-length message; chunk anything bigger.
MAX_MESSAGE_CHARS = 4000

# Long-poll timeout (seconds) passed to getUpdates. The HTTP read timeout is set
# a little higher so the socket does not time out before Telegram answers.
DEFAULT_POLL_TIMEOUT = 30

ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_ALLOWED = "TELEGRAM_ALLOWED_CHAT_IDS"
# Directory the read-only file agent is rooted at. Defaults to the user's home.
ENV_ROOT = "RONIN_TELEGRAM_ROOT"

# Read-only agent: cap the number of tool-use iterations per message so a single
# question cannot spin forever (and run up cost) over the file tools.
DEFAULT_MAX_ITERATIONS = 24

# What a redacted token looks like in any log or terminal line.
REDACTED = "<redacted>"


def redact_token(message: str, token: str) -> str:
    """Return ``message`` with every occurrence of ``token`` masked.

    The bot token is embedded in every request URL (``/bot<token>/<method>``).
    httpx error strings (e.g. on a 429/401/5xx) include that full URL, so the
    token can land in logs or on the terminal. SECURITY.md scopes token leakage
    via logs or error output as in scope, so we scrub the token before anything
    is logged or printed. Empty/short tokens are left alone (nothing to hide and
    we must not blank out unrelated text).
    """
    if not token or not message:
        return message
    return message.replace(token, REDACTED)


class TelegramConfigError(ValueError):
    """Raised when required Telegram config is missing or malformed."""


def get_bot_token(env: dict[str, str] | None = None) -> str:
    """Return the bot token from the environment, or fail closed.

    A valid Telegram token looks like ``<digits>:<rest>`` (e.g.
    ``123456:ABC-DEF...``). We reject anything missing or obviously malformed so
    the command can refuse to poll rather than silently doing nothing.
    """
    source = env if env is not None else os.environ
    token = (source.get(ENV_TOKEN) or "").strip()
    if not token:
        raise TelegramConfigError(
            f"{ENV_TOKEN} is not set. Create a bot with @BotFather, then export "
            f"{ENV_TOKEN}=<token>. The bot will not poll without it."
        )
    if not _looks_like_token(token):
        raise TelegramConfigError(
            f"{ENV_TOKEN} does not look like a Telegram token "
            f"(expected '<digits>:<secret>'). Refusing to poll."
        )
    return token


def _looks_like_token(token: str) -> bool:
    """A loose shape check: '<numeric id>:<non-empty secret>'."""
    if ":" not in token:
        return False
    head, _, tail = token.partition(":")
    return head.isdigit() and len(head) >= 3 and len(tail) >= 10


def parse_allowed_chat_ids(
    raw: str | None,
    *,
    config_ids: list[int] | None = None,
) -> list[int]:
    """Merge a comma-separated env string with config-supplied ids.

    Bad tokens in the env string are skipped (a typo must not silently widen the
    allowlist to everyone). Returns a de-duplicated, sorted list.
    """
    ids: set[int] = set(config_ids or [])
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("ignoring non-integer chat id in %s: %r", ENV_ALLOWED, part)
    return sorted(ids)


def resolve_root(env: dict[str, str] | None = None) -> Path:
    """Resolve the directory the read-only file agent is rooted at.

    Reads ``RONIN_TELEGRAM_ROOT`` from the environment; falls back to the user's
    home directory. ``~`` is expanded and the path is resolved once at startup so
    the bot always works against one fixed, absolute root.
    """
    source = env if env is not None else os.environ
    raw = (source.get(ENV_ROOT) or "").strip()
    base = Path(raw).expanduser() if raw else Path.home()
    return base.resolve()


# Path PREFIXES (directories) whose contents must never be read into a Telegram
# chat. Anything inside one of these is refused. Stored relative to home and
# resolved against the real home at call time.
_SECRET_DIR_NAMES = (
    ".ssh",
    ".ronin",
    ".aws",
    ".gnupg",
    ".netrc",  # also matched as a file below; harmless to list here too
    os.path.join(".config", "gh"),
)

# A path COMPONENT named like one of these is treated as a secret directory no
# matter where it sits in the tree (e.g. a nested ``.ssh`` under a project).
_SECRET_COMPONENT_NAMES = {".ssh", ".ronin", ".aws", ".gnupg", ".gh"}

# FILENAME globs that name a secret regardless of directory.
_SECRET_FILE_GLOBS = (
    "*.pem",
    "*.key",
    "*_rsa",
    "id_*",
    "*.env",
    ".env",
    ".netrc",
    "credentials*",
    "*secret*token*",
)


def is_secret_path(path: str | Path, *, home: Path | None = None) -> bool:
    """Return True if ``path`` is an obviously sensitive location.

    The guard is conservative on purpose: it refuses reads whose resolved path
    is inside (or equal to) a known secret directory under home, has a path
    component named like a dotfile-secret directory, or whose filename matches a
    secret glob (keys, env files, credentials, tokens). Refusing is cheap; a
    leaked secret in a chat transcript is not.
    """
    home_dir = (home or Path.home()).resolve()
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        # If a path cannot even be resolved, refuse rather than guess.
        return True

    # 1) Inside or equal to a known secret directory under home.
    for rel in _SECRET_DIR_NAMES:
        secret = (home_dir / rel).resolve()
        if resolved == secret or secret in resolved.parents:
            return True

    # 2) Any path component named like a dotfile-secret directory.
    if any(part in _SECRET_COMPONENT_NAMES for part in resolved.parts):
        return True

    # 3) Filename matches a secret glob (case-insensitive).
    name = resolved.name.lower()
    if any(fnmatch.fnmatch(name, glob) for glob in _SECRET_FILE_GLOBS):
        return True

    return False


@dataclass
class TelegramBot:
    """A small, dependency-light Telegram long-poll bridge.

    The agent callable is injected so tests can run fully offline without an LLM
    or a real provider. In the CLI it is wired to the read-only `ronin ask` path.
    """

    token: str
    allowed_chat_ids: list[int] = field(default_factory=list)
    # answer_fn(message_text) -> str. Defaults to None; wired by the CLI.
    answer_fn: Callable[[str], str] | None = None
    poll_timeout: int = DEFAULT_POLL_TIMEOUT
    # injected httpx client (tests pass a mock); None = build one lazily.
    http: Any | None = None
    _offset: int | None = None

    # ---- HTTP plumbing -------------------------------------------------

    def _client(self) -> Any:
        if self.http is not None:
            return self.http
        import httpx  # lazy: keeps the core CLI import light + offline

        # read timeout must outlast the long-poll window so the server, not the
        # client, decides when an empty poll returns.
        self.http = httpx.Client(timeout=self.poll_timeout + 15)
        return self.http

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def _redact(self, message: str) -> str:
        """Mask this bot's token in any string before it is logged or printed."""
        return redact_token(message, self.token)

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client()
        response = client.post(self._url(method), json=payload)
        response.raise_for_status()
        return response.json()

    # ---- Telegram API helpers -----------------------------------------

    def get_me(self) -> dict[str, Any]:
        """Call getMe once; returns the bot's user record (has 'username')."""
        body = self._call("getMe", {})
        if not body.get("ok"):
            raise RuntimeError(f"getMe failed: {body.get('description', 'unknown')}")
        return body.get("result", {})

    def send_message(self, chat_id: int, text: str) -> None:
        """Send text to a chat, chunked so no single call exceeds the cap."""
        for chunk in _chunk_text(text, MAX_MESSAGE_CHARS):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def _get_updates(self) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": self.poll_timeout}
        if self._offset is not None:
            payload["offset"] = self._offset
        body = self._call("getUpdates", payload)
        if not body.get("ok"):
            raise RuntimeError(f"getUpdates failed: {body.get('description', 'unknown')}")
        return body.get("result", []) or []

    # ---- core logic ----------------------------------------------------

    def is_allowed(self, chat_id: int) -> bool:
        """A non-empty allowlist gates by membership. An EMPTY allowlist allows
        NOBODY to run the agent (fail closed)."""
        return bool(self.allowed_chat_ids) and chat_id in self.allowed_chat_ids

    def handle_update(self, update: dict[str, Any]) -> None:
        """Process one update. Advances the offset for every update seen so a
        message is never reprocessed, even if we choose not to answer it."""
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            # offset must be one PAST the highest update we have consumed.
            self._offset = max(self._offset or 0, update_id + 1)

        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = message.get("text")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if text is None or chat_id is None:
            return

        # 1) Empty allowlist: do NOT run the agent for anyone. Reply only with
        #    this chat's numeric id so the owner can add it (safe onboarding).
        if not self.allowed_chat_ids:
            logger.info("onboarding: chat id %s messaged an empty allowlist", chat_id)
            self.send_message(
                chat_id,
                f"your chat id is {chat_id}; add it to {ENV_ALLOWED} to enable me",
            )
            return

        # 2) Non-empty allowlist but this id is not in it: ignore silently.
        if not self.is_allowed(chat_id):
            logger.info("ignoring message from non-allowed chat id %s", chat_id)
            return

        # 3) Allowed: run the SAME read-only ask path and send the answer back.
        logger.info("answering allowed chat id %s", chat_id)
        answer = self._answer(text)
        self.send_message(chat_id, answer)

    def _answer(self, text: str) -> str:
        if self.answer_fn is None:
            # Defensive: should never happen via the CLI, which always wires it.
            return "the agent is not wired up on this host."
        try:
            return self.answer_fn(text)
        except Exception as exc:  # noqa: BLE001 - never crash the poll loop
            logger.exception("agent run failed")
            return f"sorry, the agent hit an error: {exc.__class__.__name__}"

    # ---- poll loop -----------------------------------------------------

    def poll_once(self) -> int:
        """One getUpdates cycle. Returns the number of updates handled."""
        updates = self._get_updates()
        for update in updates:
            self.handle_update(update)
        return len(updates)

    def run_forever(self, *, on_error_sleep: float = 3.0) -> None:
        """Long-poll until interrupted. httpx errors get a short backoff and the
        loop keeps going so a transient network blip does not kill the bridge."""
        import httpx  # lazy

        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except httpx.HTTPError as exc:
                # httpx errors embed the request URL, which carries the token.
                # Redact before logging so a 429/401/5xx never leaks the secret.
                logger.warning(
                    "telegram poll error: %s; backing off %.1fs",
                    self._redact(str(exc)),
                    on_error_sleep,
                )
                _sleep(on_error_sleep)
            except Exception as exc:  # noqa: BLE001 - keep polling on odd errors
                logger.warning(
                    "unexpected poll error: %s; backing off %.1fs",
                    self._redact(str(exc)),
                    on_error_sleep,
                )
                _sleep(on_error_sleep)


def _sleep(seconds: float) -> None:
    """Indirection so tests can monkeypatch the backoff without real waits."""
    import time

    time.sleep(seconds)


def _chunk_text(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most ``limit`` chars, preferring newline
    boundaries so messages stay readable. Always returns at least one chunk."""
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks or [""]
