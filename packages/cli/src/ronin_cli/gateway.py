"""A channel-agnostic GATEWAY: drive Ronin's read-only agent from chat, safely.

This generalizes the Telegram bridge (`ronin telegram`) into a multi-channel
gateway. The same outbound-only, allowlisted, read-only posture is applied to
*any* channel (Telegram today, Discord/Slack via the adapter scaffold), so a
single safety core gates every inbound message the same way regardless of where
it came from.

Why this shape (and how it is SAFER than Hermes / OpenClaw-style bridges):

- OUTBOUND ONLY. Every adapter POLLS its provider (long-poll / getUpdates style)
  and dials OUT. The gateway opens NO inbound port and needs no public hostname,
  so it works behind NAT and presents no attack surface to the internet. There
  is no webhook, no listener, no `serve` here. (For remote reach without an
  inbound port, that is what `ronin relay` is for.)
- ALLOWLIST + PAIRING-CODE. Every sender is gated through a single PURE function
  (`gate`). An EMPTY allowlist runs the agent for NOBODY (fail closed) — it only
  hands an unknown sender a deterministic PAIRING CODE they can read to the owner
  to be added. An allowlisted sender runs; everyone else is denied. The owner is
  never auto-trusted: pairing is share-a-code-out-of-band, not self-service.
- READ-ONLY AGENT PATH ONLY. A chat message routes to `run_ask` — the SAME
  read-only / ask agent `ronin ask` uses. That agent has read-only service tools
  and refuses to mutate state. The gateway NEVER takes a write / edit / shell /
  `--full-access` path from any channel. There is no `RONIN_*_ALLOW_EDITS`
  escape hatch here: a chat message cannot trigger a destructive action, full
  stop. There is no shell / eval / exec path in this module.
- SECRET-GUARDED, FAIL-CLOSED CONFIG. Each adapter's bot token comes from the
  ENVIRONMENT and is REQUIRED: a missing/malformed token makes the channel refuse
  to start rather than silently doing nothing (mirrors `telegram_bot`'s
  `get_bot_token`). The pairing secret likewise comes from env and is required.

Network clients (httpx / the discord client) are imported LAZILY inside method
bodies so importing this module — and the core CLI — stays light and offline.
Nothing in the PURE safety core (`is_allowed`, `pairing_code`, `gate`) touches
the network, the filesystem, or any token, so it is trivially unit-testable.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger("ronin.gateway")

# ---------------------------------------------------------------------------
# Environment contract (all secrets come from env; channels fail closed without
# the token they need).
# ---------------------------------------------------------------------------

# Shared secret used to derive a sender's pairing code. REQUIRED to enable
# pairing for unknown senders; without it the gateway cannot mint codes and an
# unknown sender is simply denied (still fail-closed, just no onboarding path).
ENV_PAIRING_SECRET = "RONIN_GATEWAY_SECRET"

# Per-channel bot tokens. Each is REQUIRED for its channel to start.
ENV_TELEGRAM_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_DISCORD_TOKEN = "DISCORD_BOT_TOKEN"

# Per-channel allowlists (comma-separated sender ids), merged on top of any
# config-supplied ids. An EMPTY merged allowlist = nobody runs (fail closed).
ENV_TELEGRAM_ALLOWED = "TELEGRAM_ALLOWED_CHAT_IDS"
ENV_DISCORD_ALLOWED = "DISCORD_ALLOWED_USER_IDS"

# How many hex chars of the HMAC to surface as the pairing code. 8 hex chars
# (~32 bits) is short enough to read aloud yet far too large to brute-force
# against an allowlist add that a human performs by hand.
PAIRING_CODE_LEN = 8

# Telegram caps a single message at 4096 chars; stay safely under it.
MAX_MESSAGE_CHARS = 4000

# Gate outcomes. Exposed as constants so callers (and tests) don't hardcode the
# literals.
RUN = "run"
NEEDS_PAIRING = "needs_pairing"
DENIED = "denied"


# ===========================================================================
# PURE SAFETY CORE  (no network, no filesystem, no tokens — unit-testable)
# ===========================================================================


def is_allowed(sender_id: str, allowlist: set[str]) -> bool:
    """Return True iff ``sender_id`` is explicitly allowlisted.

    Fail-closed by construction, exactly like ``TelegramBot.is_allowed``: a
    NON-EMPTY allowlist gates by membership, and an EMPTY allowlist allows
    NOBODY (``bool(empty)`` is False, so this returns False for every sender).
    Sender ids are compared as strings so the same core works for Telegram's
    numeric chat ids, Discord/Slack snowflake ids, etc. — the adapter is
    responsible for stringifying its native id.
    """
    if not allowlist:
        return False
    return str(sender_id) in allowlist


def pairing_code(sender_id: str, secret: str) -> str:
    """Deterministic short pairing code for ``sender_id`` under ``secret``.

    An unknown sender reads this code to the owner out-of-band; the owner
    confirms it (re-derives the same code for that id) before adding the id to
    the allowlist. Properties:

    - DETERMINISTIC: same (sender_id, secret) always yields the same code, so the
      sender and owner independently compute the identical string. (This is what
      the test asserts.)
    - KEYED: it is an HMAC-SHA256 over the sender id keyed by the server secret,
      so codes cannot be precomputed or forged without the secret, and the secret
      cannot be recovered from a code.
    - SHORT & SPEAKABLE: the first ``PAIRING_CODE_LEN`` hex chars, lowercased.

    A missing/empty secret is a programming error here (the caller fails closed
    before getting this far); we raise rather than emit a guessable code.
    """
    if not secret:
        raise ValueError(
            f"{ENV_PAIRING_SECRET} is not set; cannot mint a pairing code. "
            "Refusing to emit a guessable code."
        )
    digest = hmac.new(
        secret.encode("utf-8"),
        str(sender_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:PAIRING_CODE_LEN]


def gate(sender_id: str, allowlist: set[str], *, secret: str | None = None) -> str:
    """Decide what to do with a message from ``sender_id``. PURE.

    Returns one of:
    - ``"run"``           — sender is allowlisted; route the message to the
                            read-only agent.
    - ``"needs_pairing"`` — sender is unknown and there IS at least one
                            allowlisted sender (the gateway is "open for
                            business"); hand them a pairing code to share with
                            the owner.
    - ``"denied"``        — the allowlist is EMPTY, so nobody runs (fail closed);
                            there is no one to approve a pairing yet.

    Note the asymmetry, which is the deliberate fail-closed posture: an empty
    allowlist denies (there is no owner context to pair against), while a
    populated allowlist lets an unknown sender START the pairing handshake. The
    ``secret`` is accepted only so a caller can pass it through uniformly; it is
    not required to make the decision (the code itself is minted by
    ``pairing_code``). ``is_allowed`` already treats an empty allowlist as
    "nobody", so the empty-allowlist branch here is what distinguishes
    ``denied`` from ``needs_pairing``.
    """
    if is_allowed(sender_id, allowlist):
        return RUN
    if not allowlist:
        # Fail closed: with nobody allowlisted there is no owner to approve a
        # pairing, so we do not even offer one.
        return DENIED
    return NEEDS_PAIRING


def get_required_token(env_var: str, env: dict[str, str] | None = None) -> str:
    """Return a required bot token from the environment, or fail closed.

    Mirrors ``telegram_bot.get_bot_token``'s posture: a missing/blank token
    raises so a channel refuses to start rather than silently polling nothing.
    Token SHAPE validation is left to the channel's adapter (Telegram's
    ``<digits>:<secret>`` differs from Discord's), but presence is enforced here
    uniformly.
    """
    source = env if env is not None else os.environ
    token = (source.get(env_var) or "").strip()
    if not token:
        raise GatewayConfigError(
            f"{env_var} is not set. The channel will not start without it. "
            f"Export {env_var}=<token>."
        )
    return token


def get_pairing_secret(env: dict[str, str] | None = None) -> str:
    """Return the pairing secret from the environment, or fail closed.

    REQUIRED to enable the pairing handshake for unknown senders. Without it the
    gateway still runs for already-allowlisted senders (and still DENIES everyone
    else), but cannot mint onboarding codes — so we require it up front and tell
    the owner how to set it.
    """
    source = env if env is not None else os.environ
    secret = (source.get(ENV_PAIRING_SECRET) or "").strip()
    if not secret:
        raise GatewayConfigError(
            f"{ENV_PAIRING_SECRET} is not set. The gateway needs a stable secret "
            f"to mint pairing codes for new senders. Export {ENV_PAIRING_SECRET}="
            f"<a long random string>."
        )
    return secret


class GatewayConfigError(ValueError):
    """Raised when required gateway config (a token / the secret) is missing."""


# ===========================================================================
# CHANNEL ADAPTERS
# ===========================================================================


@dataclass
class InboundMessage:
    """One normalized inbound message, channel-agnostic.

    Every adapter maps its native payload onto this so the ``Gateway`` core never
    sees channel-specific shapes. ``sender_id`` is stringified by the adapter so
    the pure safety core can compare ids uniformly. ``channel`` is a short label
    ("telegram" / "discord") used only for logging.
    """

    sender_id: str
    text: str
    channel: str = ""
    # Optional opaque handle the adapter may stash (e.g. a Telegram chat id as an
    # int, a Discord channel object) so its own ``send`` can reply on the exact
    # same conversation. The core never inspects this.
    reply_to: Any = None


@runtime_checkable
class ChannelAdapter(Protocol):
    """The contract every channel must satisfy.

    Two responsibilities only, both OUTBOUND:
    - ``poll()``  — dial out, fetch any new inbound messages, return them
                    normalized as ``InboundMessage``. Returns ``[]`` when idle.
                    Must advance its own read cursor so a message is never
                    re-delivered.
    - ``send(...)`` — deliver a reply back on the same channel/conversation.

    ``name`` is a short channel label for logging. There is deliberately no
    "listen"/"serve" method: adapters never accept inbound connections.
    """

    name: str

    def poll(self) -> list[InboundMessage]:
        ...

    def send(self, message: InboundMessage, text: str) -> None:
        ...


def _chunk_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``text`` into <=``limit`` char chunks on newline boundaries when
    possible. Always returns at least one (possibly empty) chunk."""
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


@dataclass
class TelegramAdapter:
    """Telegram channel, wrapping the existing, battle-tested ``TelegramBot``.

    We reuse ``telegram_bot`` rather than reimplement long-poll: it already does
    token validation, redaction, chunking, and offset management. This adapter is
    a thin bridge that exposes Telegram's getUpdates as ``poll()`` and
    sendMessage as ``send()`` against the gateway's normalized message shape. The
    ``TelegramBot``'s own allowlist/answer machinery is intentionally NOT used —
    the gateway core owns gating and routing — so we construct it with an empty
    allowlist and no ``answer_fn`` and call only its transport helpers.
    """

    name: str = "telegram"
    token: str = ""
    poll_timeout: int = 30
    # Injected for tests (a fake httpx client); None builds one lazily.
    http: Any | None = None
    _bot: Any = None

    def _ensure_bot(self) -> Any:
        if self._bot is None:
            # Lazy import keeps this module light/offline until a poll happens.
            from .telegram_bot import TelegramBot

            self._bot = TelegramBot(
                token=self.token,
                allowed_chat_ids=[],  # gateway core gates; the bot must not.
                answer_fn=None,       # gateway core routes; the bot must not.
                poll_timeout=self.poll_timeout,
                http=self.http,
            )
        return self._bot

    def poll(self) -> list[InboundMessage]:
        bot = self._ensure_bot()
        out: list[InboundMessage] = []
        for update in bot._get_updates():
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                bot._offset = max(bot._offset or 0, update_id + 1)
            message = update.get("message") or update.get("edited_message")
            if not message:
                continue
            text = message.get("text")
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if text is None or chat_id is None:
                continue
            out.append(
                InboundMessage(
                    sender_id=str(chat_id),
                    text=text,
                    channel=self.name,
                    reply_to=chat_id,  # native int chat id for send()
                )
            )
        return out

    def send(self, message: InboundMessage, text: str) -> None:
        bot = self._ensure_bot()
        chat_id = message.reply_to
        if chat_id is None:
            # Fall back to the stringified sender id if no native handle was kept.
            try:
                chat_id = int(message.sender_id)
            except (TypeError, ValueError):
                logger.warning("telegram: no chat id to reply to; dropping reply")
                return
        bot.send_message(chat_id, text)


@dataclass
class DiscordAdapter:
    """Discord channel — SCAFFOLD (does not connect during tests).

    Discord's gateway is a persistent OUTBOUND websocket the bot dials out to
    (https://discord.com/developers/docs/topics/gateway); like Telegram's
    long-poll it opens NO inbound port, which keeps the gateway's outbound-only
    posture intact. To make this live you would:

      1. Create an application + bot at https://discord.com/developers, copy the
         bot token, and ``export DISCORD_BOT_TOKEN=<token>`` (REQUIRED; this
         adapter fails closed without it, see ``from_env`` below).
      2. Enable the MESSAGE CONTENT intent for the bot (Discord gates message
         text behind a privileged intent).
      3. Wire a real client where ``_client()`` is marked below — e.g.
         ``discord.py`` (``import discord``) or a raw websocket to the Gateway
         API. Map each inbound ``message_create`` event to an ``InboundMessage``
         (``sender_id = str(message.author.id)``, ``text = message.content``,
         ``reply_to = message.channel``), and have ``send`` post back to that
         channel. The discord client MUST be imported LAZILY inside ``_client``
         so importing this module stays offline.

    Until that client is wired, ``poll`` returns ``[]`` and ``send`` is a no-op,
    so the adapter is inert and safe to construct in tests without a token or any
    network. The gateway's safety core treats Discord senders identically to
    Telegram's — same allowlist + pairing-code gate, same read-only agent path.
    """

    name: str = "discord"
    token: str = ""
    allowed_user_ids: set[str] = field(default_factory=set)
    _client_obj: Any = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "DiscordAdapter":
        """Build a Discord adapter from the environment, failing closed.

        The bot token is REQUIRED (``get_required_token`` raises if missing), so
        a misconfigured Discord channel refuses to start rather than silently
        doing nothing — exactly the Telegram posture. The allowlist is read from
        ``DISCORD_ALLOWED_USER_IDS`` (comma-separated); an empty one means the
        gateway runs for nobody on Discord until ids are added.
        """
        source = env if env is not None else os.environ
        token = get_required_token(ENV_DISCORD_TOKEN, source)
        allowed = {
            part.strip()
            for part in (source.get(ENV_DISCORD_ALLOWED) or "").split(",")
            if part.strip()
        }
        return cls(token=token, allowed_user_ids=allowed)

    def _client(self) -> Any:
        """Return the live Discord client. SCAFFOLD — not wired.

        >>> WIRE THE REAL DISCORD CLIENT HERE (lazy import). <<<
        e.g.:
            import discord  # lazy: keep this module offline until used
            intents = discord.Intents.default()
            intents.message_content = True
            self._client_obj = discord.Client(intents=intents)
            ...  # log in with self.token, subscribe to on_message
            return self._client_obj

        Returning ``None`` (the scaffold default) makes ``poll``/``send`` inert,
        which is what keeps the adapter constructible and test-safe with no
        network and no real token connection.
        """
        return self._client_obj

    def poll(self) -> list[InboundMessage]:
        client = self._client()
        if client is None:
            # Scaffold: nothing to poll until a real client is wired.
            return []
        # >>> Drain queued inbound discord events into InboundMessage list. <<<
        return []

    def send(self, message: InboundMessage, text: str) -> None:
        client = self._client()
        if client is None:
            logger.debug("discord adapter not wired; dropping reply")
            return
        # >>> Post `text` back to message.reply_to (the discord channel). <<<
        for _chunk in _chunk_text(text):
            pass


# ===========================================================================
# THE GATEWAY  (routes every inbound message through the gate to run_ask)
# ===========================================================================


@dataclass
class Gateway:
    """Route inbound chat messages from any adapter to the READ-ONLY agent.

    For each polled message the gateway:
      1. GATES the sender via the pure ``gate`` (allowlist + empty=nobody).
      2. On ``run``: routes the text to ``answer_fn`` — wired by the CLI to
         ``run_ask`` (the read-only / ask agent). NEVER a write/edit/shell path.
      3. On ``needs_pairing``: replies with the sender's pairing code so they can
         get added (no agent call).
      4. On ``denied``: stays silent by default (no agent call, no oracle for an
         empty allowlist) — see ``announce_denied``.

    The agent callable is INJECTED so tests run fully offline with no LLM. The
    gateway never holds a token itself; adapters do, and they fail closed without
    one.
    """

    adapters: list[ChannelAdapter]
    # answer_fn(message_text) -> str. Wired by the CLI to the READ-ONLY run_ask.
    # Injected so tests stay offline. None → a safe placeholder reply.
    answer_fn: Callable[[str], str] | None = None
    # Per-channel allowlists keyed by adapter name. An absent/empty set means
    # nobody runs on that channel (fail closed).
    allowlists: dict[str, set[str]] = field(default_factory=dict)
    # Secret used to mint pairing codes. Required to offer pairing; if None we
    # still RUN allowlisted senders and DENY others, just without codes.
    secret: str | None = None
    # If True, tell a denied sender they're not allowed (still no agent call).
    # Default False mirrors telegram_bot ignoring non-allowed chats silently.
    announce_denied: bool = False

    def _allowlist_for(self, channel: str) -> set[str]:
        return self.allowlists.get(channel, set())

    def _answer(self, text: str) -> str:
        """Run the READ-ONLY agent for an allowed message. Never raises into the
        poll loop: any agent/provider error becomes a short reply."""
        if self.answer_fn is None:
            return "the agent is not wired up on this host."
        try:
            return self.answer_fn(text)
        except Exception as exc:  # noqa: BLE001 - never crash the poll loop
            logger.exception("agent run failed")
            return f"sorry, the agent hit an error: {exc.__class__.__name__}"

    def handle(self, adapter: ChannelAdapter, message: InboundMessage) -> str:
        """Gate + route ONE message; return the gate outcome that was taken.

        Returning the outcome (one of RUN/NEEDS_PAIRING/DENIED) makes the routing
        decision observable to tests without inspecting side effects. The actual
        reply (if any) is sent via ``adapter.send`` as a side effect.
        """
        allowlist = self._allowlist_for(adapter.name)
        outcome = gate(message.sender_id, allowlist, secret=self.secret)

        if outcome == RUN:
            logger.info("gateway[%s]: running for sender %s", adapter.name, message.sender_id)
            reply = self._answer(message.text)
            adapter.send(message, reply)
            return RUN

        if outcome == NEEDS_PAIRING:
            logger.info(
                "gateway[%s]: unknown sender %s — offering pairing",
                adapter.name, message.sender_id,
            )
            if self.secret:
                code = pairing_code(message.sender_id, self.secret)
                adapter.send(
                    message,
                    f"you're not paired yet. Give the owner this code to be added: "
                    f"{code}  (your id: {message.sender_id})",
                )
            else:
                # No secret configured: cannot mint a code, but still must not run.
                adapter.send(
                    message,
                    "this gateway isn't accepting new senders right now.",
                )
            return NEEDS_PAIRING

        # DENIED (empty allowlist → nobody, fail closed). Silent unless asked.
        logger.info("gateway[%s]: denied sender %s", adapter.name, message.sender_id)
        if self.announce_denied:
            adapter.send(message, "this gateway is not accepting messages.")
        return DENIED

    def poll_once(self) -> int:
        """Poll every adapter once and handle all messages. Returns the count
        handled. One failing adapter never stops the others."""
        handled = 0
        for adapter in self.adapters:
            try:
                messages = adapter.poll()
            except Exception:  # noqa: BLE001 - one bad channel must not stop the rest
                logger.warning("gateway: poll failed for %s", adapter.name, exc_info=True)
                continue
            for message in messages:
                try:
                    self.handle(adapter, message)
                    handled += 1
                except Exception:  # noqa: BLE001 - one bad message must not stop the rest
                    logger.warning(
                        "gateway[%s]: handling a message failed", adapter.name, exc_info=True
                    )
        return handled

    def run_forever(self, *, on_error_sleep: float = 3.0) -> None:
        """Poll until interrupted. Transient errors get a short backoff so a
        network blip never kills the gateway."""
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001 - keep polling on odd errors
                logger.warning("gateway poll error; backing off %.1fs", on_error_sleep, exc_info=True)
                _sleep(on_error_sleep)


def _sleep(seconds: float) -> None:
    """Indirection so tests can monkeypatch the backoff without real waits."""
    import time

    time.sleep(seconds)


# ===========================================================================
# WIRING  (build the read-only answer_fn + adapters from config/env)
# ===========================================================================


def build_answer_fn() -> Callable[[str], str]:
    """Build the READ-ONLY agent callable the gateway routes to.

    This wires the gateway to ``run_ask`` — the exact read-only / ask agent
    ``ronin ask`` uses, which has read-only service tools and refuses to mutate
    state. There is no write/edit/shell/--full-access path here; a chat message
    can only ever read. ``run_ask`` is imported LAZILY so importing this module
    stays light and offline.
    """
    from .config import load_config
    from .runner import run_ask

    config = load_config()

    def answer_fn(text: str) -> str:
        result = run_ask(config, text)  # console=None → no spinner, plain string
        return result.output or result.error or "(no answer)"

    return answer_fn


def build_gateway(
    channels: list[str],
    *,
    env: dict[str, str] | None = None,
    answer_fn: Callable[[str], str] | None = None,
    poll_timeout: int = 30,
) -> Gateway:
    """Construct a ``Gateway`` for the requested ``channels`` from env + config.

    Fails CLOSED: each channel's token is required (raises ``GatewayConfigError``
    if missing), and the pairing secret is required to enable onboarding. Unknown
    channel names raise. The returned gateway routes to the read-only ``run_ask``
    via ``answer_fn`` (built here if not injected).
    """
    source = env if env is not None else os.environ

    # Pairing secret: required so the gateway can mint onboarding codes. Fails
    # closed if unset.
    secret = get_pairing_secret(source)

    # Read-only agent path. Injected in tests; built from config otherwise.
    if answer_fn is None:
        answer_fn = build_answer_fn()

    # Pull config-supplied Telegram allowlist (reuse the existing field) so the
    # gateway honours the same allowlist as `ronin telegram`.
    from .config import load_config

    config = load_config()

    adapters: list[ChannelAdapter] = []
    allowlists: dict[str, set[str]] = {}

    for raw in channels:
        channel = raw.strip().lower()
        if channel == "telegram":
            token = get_required_token(ENV_TELEGRAM_TOKEN, source)
            adapters.append(TelegramAdapter(token=token, poll_timeout=poll_timeout))
            ids: set[str] = {str(i) for i in (config.telegram_allowed_chat_ids or [])}
            ids |= {
                p.strip()
                for p in (source.get(ENV_TELEGRAM_ALLOWED) or "").split(",")
                if p.strip()
            }
            allowlists["telegram"] = ids
        elif channel == "discord":
            adapter = DiscordAdapter.from_env(source)
            adapters.append(adapter)
            allowlists["discord"] = set(adapter.allowed_user_ids)
        else:
            raise GatewayConfigError(
                f"unknown channel {channel!r}. Supported: telegram, discord."
            )

    return Gateway(
        adapters=adapters,
        answer_fn=answer_fn,
        allowlists=allowlists,
        secret=secret,
    )


# ===========================================================================
# CLI  (`ronin gateway` — outbound-only, allowlisted, read-only)
# ===========================================================================

# A standalone Typer group so this can be wired with ONE line in main.py
# (mirroring how `relay_app` is added):
#
#     from .gateway import gateway_app
#     app.add_typer(gateway_app, name="gateway")
#
# It is NOT imported by main.py here (the task forbids editing main.py); the
# group works on its own via `python -m ronin_cli.gateway`.
try:  # typer is a CLI-only dep; keep the pure core importable without it.
    import typer
    from rich.console import Console

    gateway_app = typer.Typer(
        help="Multi-channel gateway: drive Ronin's READ-ONLY agent from chat "
             "(Telegram/Discord). Outbound-only, allowlist + pairing-code, "
             "fail-closed. No inbound port.",
    )
    _console = Console()

    @gateway_app.callback(invoke_without_command=True)
    def gateway(
        channel: list[str] = typer.Option(
            ["telegram"], "--channel", "-c",
            help="Channel(s) to run (repeatable). Supported: telegram, discord. "
                 "Default: telegram.",
        ),
        once: bool = typer.Option(
            False, "--once",
            help="Poll every channel a single time and exit (for testing).",
        ),
        poll_timeout: int = typer.Option(
            30, "--poll-timeout",
            help="Long-poll timeout in seconds for channels that support it.",
        ),
    ) -> None:
        """Run the gateway. Outbound-only, allowlisted, READ-ONLY.

        Each channel POLLS its provider (dials OUT; no inbound port) and routes
        allowed messages to the same read-only agent `ronin ask` uses. Set the
        per-channel bot token (TELEGRAM_BOT_TOKEN / DISCORD_BOT_TOKEN) and
        RONIN_GATEWAY_SECRET; unset/malformed config makes the gateway refuse to
        start (fail closed). An empty allowlist runs the agent for NOBODY — an
        unknown sender only gets a pairing code to share with you.
        """
        try:
            gw = build_gateway(list(channel), poll_timeout=poll_timeout)
        except GatewayConfigError as exc:
            _console.print(f"[red]x[/red] {exc}")
            raise typer.Exit(2)

        names = ", ".join(a.name for a in gw.adapters)
        if once:
            n = gw.poll_once()
            _console.print(f"[dim]gateway polled {names}; handled {n} message(s)[/dim]")
            return
        _console.print(f"[green]gateway up[/green] on {names} (read-only, allowlisted). Ctrl+C to stop.")
        try:
            gw.run_forever()
        except KeyboardInterrupt:
            _console.print("\n[dim]gateway stopped[/dim]")

    def register(app: Any) -> None:
        """Attach the gateway group to a parent Typer ``app`` (one-liner for
        main.py): ``from .gateway import register; register(app)``."""
        app.add_typer(gateway_app, name="gateway")

    def main() -> None:
        """Console entry: ``python -m ronin_cli.gateway``."""
        gateway_app()

except ImportError:  # pragma: no cover - typer always present in the CLI env
    gateway_app = None  # type: ignore[assignment]


if __name__ == "__main__":  # pragma: no cover
    main()
