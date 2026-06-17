"""Notifications — ping a webhook when an autonomous run finishes.

You can schedule Nightshift to run overnight, but you need to *know* it ran and
what it did. This posts a morning-report message to any incoming webhook (Slack,
Discord, Mattermost, or a plain endpoint — all accept ``{"text": ...}``). The
report formatting + payload are pure and unit-tested; the POST is a thin wrapper
that fails soft.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Protocol


def format_report(summary: dict, lines: list[str] | None = None) -> str:
    """A concise text morning-report from a Nightshift summary. Pure."""
    patched = summary.get("patched", 0)
    total = summary.get("total", 0)
    head = (f"🌙 ronin nightshift — {patched}/{total} task(s) → reviewable patch"
            f"{'es' if patched != 1 else ''}")
    bits = [head]
    extras = []
    if summary.get("failed-tests"):
        extras.append(f"{summary['failed-tests']} failed tests")
    if summary.get("no-change"):
        extras.append(f"{summary['no-change']} no-change")
    if summary.get("error"):
        extras.append(f"{summary['error']} error(s)")
    if extras:
        bits.append("(" + ", ".join(extras) + ")")
    body = " ".join(bits)
    if lines:
        body += "\n" + "\n".join(f"• {ln}" for ln in lines)
    if patched:
        body += "\n\nReview: `ronin patches` · apply: `ronin patches --apply --clean`"
    return body


def build_payload(text: str) -> dict:
    """A webhook payload accepted by Slack / Discord / Mattermost / generic."""
    return {"text": text}


def post_webhook(url: str, text: str, *, timeout: int = 15) -> bool:
    """POST a notification to ``url``. Returns True on a 2xx. Never raises."""
    if not url:
        return False
    import httpx
    try:
        r = httpx.post(url, json=build_payload(text), timeout=timeout)
        return 200 <= r.status_code < 300
    except Exception:  # noqa: BLE001 — a notification must never break the run
        return False


# ---------------------------------------------------------------------------
# Notify abstraction — pluggable backends for "tell me something happened".
#
# A backend is anything with ``send(text) -> bool``. The local backend appends
# to a log file (always works, offline, $0) and best-effort raises a desktop
# notification on macOS/Linux. The webhook backend wraps ``post_webhook``. A
# ``Notifier`` fans a message out to every configured backend and reports whether
# *any* delivery succeeded. Nothing here ever raises — a notification must never
# break the run that triggered it.
# ---------------------------------------------------------------------------


def default_log_path() -> Path:
    """Where the local backend writes by default: ``~/.ronin/notifications.log``."""
    return Path.home() / ".ronin" / "notifications.log"


class NotifyBackend(Protocol):
    """Anything that can deliver a notification. ``send`` returns delivery success
    and must never raise."""

    name: str

    def send(self, text: str) -> bool: ...


def _desktop_notify(text: str, *, title: str = "ronin") -> bool:
    """Best-effort native desktop notification. Returns True if a notifier ran.

    macOS uses ``osascript``; Linux uses ``notify-send`` when present. Silent and
    soft everywhere else (headless servers, CI) — the log line is the real record.
    """
    import shutil
    import subprocess
    import sys

    # One short line only — multi-line notifications confuse osascript quoting.
    line = text.splitlines()[0][:240] if text else ""
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            safe = line.replace('"', "'").replace("\\", "")
            script = f'display notification "{safe}" with title "{title}"'
            subprocess.run(
                ["osascript", "-e", script],
                check=False, capture_output=True, timeout=5,
            )
            return True
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", title, line],
                check=False, capture_output=True, timeout=5,
            )
            return True
    except Exception:  # noqa: BLE001 — desktop notify is a nice-to-have, never fatal
        return False
    return False


class LocalBackend:
    """Writes every notification to a log file and tries a desktop popup.

    The log append is the source of truth (always offline, always works); the
    desktop notification is best-effort on top. ``desktop=False`` disables the
    popup (useful in tests / on servers)."""

    name = "local"

    def __init__(self, log_path: Path | None = None, *, desktop: bool = True) -> None:
        self.log_path = Path(log_path) if log_path else default_log_path()
        self.desktop = desktop

    def send(self, text: str) -> bool:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {text}\n")
            wrote = True
        except Exception:  # noqa: BLE001 — never break the caller over a log write
            wrote = False
        if self.desktop:
            _desktop_notify(text)
        return wrote


class WebhookBackend:
    """POSTs each notification to an incoming webhook (Slack/Discord/generic)."""

    name = "webhook"

    def __init__(self, url: str, *, timeout: int = 15) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, text: str) -> bool:
        return post_webhook(self.url, text, timeout=self.timeout)


class Notifier:
    """Fans a message out to every configured backend.

    ``notify(text)`` returns True if *any* backend reported success. Backends are
    tried in order; one failing never stops the others (or the caller)."""

    def __init__(self, backends: list[NotifyBackend] | None = None) -> None:
        self.backends: list[NotifyBackend] = list(backends or [])

    def add(self, backend: NotifyBackend) -> "Notifier":
        self.backends.append(backend)
        return self

    def notify(self, text: str) -> bool:
        delivered = False
        for backend in self.backends:
            try:
                if backend.send(text):
                    delivered = True
            except Exception:  # noqa: BLE001 — a bad backend can't break the rest
                continue
        return delivered

    def backend_names(self) -> list[str]:
        return [b.name for b in self.backends]


def build_notifier(
    *,
    log_path: Path | None = None,
    webhook_url: str | None = None,
    desktop: bool = True,
) -> Notifier:
    """Assemble the default notifier: a local backend always, plus a webhook
    backend when a URL is supplied (config field ``notify_webhook`` or the
    ``RONIN_NOTIFY_WEBHOOK`` env var). The local backend means notifications
    work offline with no configuration at all."""
    notifier = Notifier([LocalBackend(log_path, desktop=desktop)])
    url = webhook_url or os.environ.get("RONIN_NOTIFY_WEBHOOK")
    if url:
        notifier.add(WebhookBackend(url))
    return notifier
