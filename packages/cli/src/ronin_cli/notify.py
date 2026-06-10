"""Notifications — ping a webhook when an autonomous run finishes.

You can schedule Nightshift to run overnight, but you need to *know* it ran and
what it did. This posts a morning-report message to any incoming webhook (Slack,
Discord, Mattermost, or a plain endpoint — all accept ``{"text": ...}``). The
report formatting + payload are pure and unit-tested; the POST is a thin wrapper
that fails soft.
"""
from __future__ import annotations


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
