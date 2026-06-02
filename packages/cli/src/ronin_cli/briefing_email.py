"""Email delivery for ``csk briefing``.

Posts a briefing via Resend (https://resend.com). Resend's API takes plain
HTML, so we render the Markdown into a minimal styled HTML body before sending.

Auth: ``RESEND_API_KEY`` env var. Free tier covers 100 emails/day, plenty for
weekly briefings.

The wire-format mirrors briefing_slack.py — same shape, different delivery.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

RESEND_BASE = "https://api.resend.com/emails"
DEFAULT_FROM = "csk briefing <briefing@resend.dev>"


def markdown_to_html(md: str) -> str:
    """Tiny Markdown→HTML for our briefing subset.

    Handles only what ``render_briefing_md`` produces: H1/H2 headings, bullets
    (with indentation), ``**bold**``, inline ``code``, and italics (_..._).
    Anything fancier and we'd reach for a real library, but this keeps the
    package dependency-free for email.
    """
    lines = md.splitlines()
    out: list[str] = []
    in_ul = False
    indent_stack: list[int] = []

    def close_lists_to(indent: int) -> None:
        while indent_stack and indent_stack[-1] > indent:
            out.append("</ul>")
            indent_stack.pop()

    for raw in lines:
        line = raw.rstrip()
        if not line:
            close_lists_to(-1)
            out.append("")
            continue

        # Inline formatting first
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

        if text.startswith("# "):
            close_lists_to(-1)
            out.append(f"<h1>{text[2:]}</h1>")
        elif text.startswith("## "):
            close_lists_to(-1)
            out.append(f"<h2>{text[3:]}</h2>")
        elif text.startswith("### "):
            close_lists_to(-1)
            out.append(f"<h3>{text[4:]}</h3>")
        else:
            # Bullets
            match = re.match(r"^(\s*)-\s+(.*)$", text)
            if match:
                indent = len(match.group(1))
                if not indent_stack or indent > indent_stack[-1]:
                    out.append("<ul>")
                    indent_stack.append(indent)
                elif indent < indent_stack[-1]:
                    close_lists_to(indent)
                out.append(f"<li>{match.group(2)}</li>")
            else:
                close_lists_to(-1)
                out.append(f"<p>{text}</p>")

    close_lists_to(-1)
    body = "\n".join(out)

    # Wrap in a minimal styled HTML doc — readable in every email client.
    return (
        "<!doctype html><html><body>"
        '<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; '
        'max-width: 640px; margin: 24px auto; color: #1a1a1a; line-height: 1.5;">'
        f"{body}"
        "</div></body></html>"
    )


def send_briefing_email(
    to: str,
    markdown_body: str,
    *,
    api_key: str | None = None,
    from_addr: str | None = None,
    subject: str | None = None,
    http: Any | None = None,
) -> dict[str, Any]:
    """POST the briefing to Resend.

    Returns Resend's JSON response (typically ``{"id": "..."}`` on success).

    Raises:
      ValueError if ``to`` is empty or ``RESEND_API_KEY`` is not set
      httpx.HTTPStatusError if Resend rejects the request
    """
    if not to or "@" not in to:
        raise ValueError(f"invalid recipient address: {to!r}")

    key = api_key or os.environ.get("RESEND_API_KEY", "")
    if not key:
        raise ValueError("RESEND_API_KEY not set; pass api_key= or set the env var")

    # First line of the briefing usually contains the date — use it in the subject.
    if subject is None:
        first_h1 = next((ln[2:].strip() for ln in markdown_body.splitlines() if ln.startswith("# ")), "")
        subject = first_h1 or "Your weekly founder briefing"

    body = {
        "from": from_addr or os.environ.get("CSK_EMAIL_FROM", DEFAULT_FROM),
        "to": [to],
        "subject": subject,
        "html": markdown_to_html(markdown_body),
        "text": markdown_body,  # plain-text fallback for clients that hate HTML
    }
    client = http if http is not None else httpx.Client(timeout=30)
    response = client.post(
        RESEND_BASE,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    response.raise_for_status()
    return response.json()
