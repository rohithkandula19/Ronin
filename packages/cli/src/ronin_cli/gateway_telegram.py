"""OPTIONAL Telegram adapter for the messaging gateway.

This is a thin, honest adapter, NOT a hollow stub. It is OFF by default and the
generic webhook (`gateway.py`) never needs it: the gateway runs with zero
external accounts. Turn this on only if you want a real Telegram bot front-end.

Enable it by setting both:
    RONIN_GATEWAY_TELEGRAM=1
    TELEGRAM_BOT_TOKEN=<token from @BotFather>

When enabled, `ronin gateway` mounts a webhook at ``/telegram/<token>`` that
accepts Telegram's update JSON, extracts the chat id + text, runs the agent (via
the same session store as the generic endpoint), and replies by calling
Telegram's ``sendMessage`` API. Telegram delivers updates to this URL after you
register it once:

    curl "https://api.telegram.org/bot<token>/setWebhook?url=https://<host>/telegram/<token>"

Everything here fails soft: a missing token disables the adapter, and a network
error talking to Telegram is swallowed so it never takes the gateway down. The
update-parsing is pure and unit-tested; only the outbound POST touches the
network.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request

# Telegram's API base. The bot token is appended per-call: <BASE>/bot<token>/...
_TELEGRAM_API = "https://api.telegram.org"


def telegram_enabled(env: dict[str, str] | None = None) -> bool:
    """True only when the adapter is explicitly switched on AND a token exists.

    Pure: reads the given mapping (defaults to ``os.environ``). The generic
    webhook works regardless of this flag — this gates ONLY the Telegram mount.
    """
    env = os.environ if env is None else env
    flag = (env.get("RONIN_GATEWAY_TELEGRAM", "") or "").strip().lower()
    on = flag in ("1", "true", "yes", "on")
    return on and bool((env.get("TELEGRAM_BOT_TOKEN", "") or "").strip())


def parse_update(update: dict[str, Any]) -> tuple[int | None, str]:
    """Extract ``(chat_id, text)`` from a Telegram update payload. Pure.

    Handles both fresh messages and edited messages. Returns ``(None, "")`` when
    the update carries no usable text (e.g. a sticker or a service message).
    """
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not isinstance(chat_id, int):
        chat_id = None
    return chat_id, text


def send_message(token: str, chat_id: int, text: str, *, timeout: int = 15) -> bool:
    """POST a reply back to a Telegram chat. Returns True on a 2xx. Never raises."""
    if not token or chat_id is None:
        return False
    import httpx

    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    try:
        r = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=timeout)
        return 200 <= r.status_code < 300
    except Exception:  # noqa: BLE001 — a failed reply must never crash the gateway
        return False


def mount_telegram(app: FastAPI, *, token: str) -> None:
    """Mount the Telegram webhook on an existing gateway ``app``.

    Reuses the app's config + session store, so a Telegram chat shares the same
    per-session memory machinery as the generic endpoint. The webhook path
    embeds the token so only Telegram (which knows it) can post here.
    """
    from .runner import run_ask

    @app.post(f"/telegram/{token}")
    async def telegram_webhook(request: Request) -> dict[str, Any]:
        try:
            update = await request.json()
        except Exception:  # noqa: BLE001 — malformed body → ack and ignore
            return {"ok": True}
        chat_id, text = parse_update(update)
        if chat_id is None or not text:
            return {"ok": True}

        config = app.state.config
        store = app.state.session_store
        session_id = f"telegram:{chat_id}"
        prompt = store.build_prompt(session_id, text)
        result = run_ask(config, prompt, console=None)
        store.record_reply(session_id, result.output)
        send_message(token, chat_id, result.output)
        return {"ok": True}
