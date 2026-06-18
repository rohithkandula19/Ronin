"""Watch a web page and get a Telegram ping when it changes.

The Telegram bridge already long-polls, knows the owner's chat id, and fires due
reminders on its own tick (see ``reminders.py`` + ``telegram_bot.py``). A page
watch is the same shape: persist a small record, and on the SAME poll tick fetch
each watched page, hash the relevant content, and message the owner when the hash
moves. No new daemon, no new credentials, no second loop.

What lives here:

  * a durable store at ``~/.ronin/watches.json`` (a list of watch dicts) with
    add / list / remove helpers,
  * ``extract_content`` / ``content_hash`` - pure helpers that pull the watched
    slice out of a fetched page (whole readable text, or only lines containing a
    keyword) and hash it,
  * ``parse_watch`` - a stdlib-only parser that turns "watch <url> ...",
    "watch <url> for <keyword>", "list watches", and "cancel watch N" into a small
    intent, and
  * ``due_watches`` - a pure throttle so the poll loop only re-checks a watch
    every ``interval`` seconds, not every tick.

Parsing and hashing are pure. The store is one JSON file. Nothing here sleeps or
dials out; the bot fetches and sends. ``fetch_url`` lives in ``web_tools.py`` and
is imported lazily so this module stays light and offline.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# Default minimum seconds between re-checks of one watch. The bot polls Telegram
# every ~30s; re-fetching every watched page that often is wasteful and rude to
# the sites, so a watch is only re-checked when it has gone this long unchecked.
DEFAULT_INTERVAL = 900  # 15 minutes


def _watches_path() -> Path:
    return Path.home() / ".ronin" / "watches.json"


# --------------------------------------------------------------------------
# Content extraction + hashing. Pure: no network, no clock, no filesystem.
# --------------------------------------------------------------------------


def extract_content(page_text: str, keyword: str | None = None) -> str:
    """The slice of a fetched page we compare across checks.

    With no keyword: the whole readable text, whitespace-normalized so cosmetic
    reflowing does not look like a change. With a keyword: only the lines that
    mention it (case-insensitive), so a price/text watch ignores churn elsewhere
    on the page. Pure.
    """
    text = (page_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if keyword:
        kw = keyword.lower()
        lines = [ln.strip() for ln in text.split("\n") if kw in ln.lower()]
        text = "\n".join(lines)
    # collapse runs of whitespace so trivial reformatting is not a "change"
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def content_hash(page_text: str, keyword: str | None = None) -> str:
    """Stable hash of the watched slice. Empty content hashes to "" (not a real
    snapshot), so a fetch that returned nothing never counts as a change. Pure."""
    content = extract_content(page_text, keyword)
    if not content:
        return ""
    return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------
# Persistence. One JSON file; a list of watch dicts.
# --------------------------------------------------------------------------


@dataclass
class Watch:
    id: int
    url: str
    chat_id: int
    keyword: str | None = None  # if set, only lines containing it are compared
    last_hash: str = ""  # "" until the first successful check
    last_checked: float = 0.0  # epoch of the last fetch attempt (throttle)
    interval: int = DEFAULT_INTERVAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "chat_id": self.chat_id,
            "keyword": self.keyword,
            "last_hash": self.last_hash,
            "last_checked": self.last_checked,
            "interval": self.interval,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Watch":
        kw = d.get("keyword")
        return cls(
            id=int(d.get("id", 0)),
            url=str(d.get("url", "")),
            chat_id=int(d.get("chat_id", 0)),
            keyword=str(kw) if kw else None,
            last_hash=str(d.get("last_hash", "")),
            last_checked=float(d.get("last_checked", 0.0)),
            interval=int(d.get("interval", DEFAULT_INTERVAL)),
        )


def load_watches() -> list[Watch]:
    """Load all watches. Returns [] on a missing or unreadable file."""
    p = _watches_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Watch.from_dict(w) for w in data.get("watches", [])]
    except (OSError, ValueError):
        return []


def _save(watches: list[Watch]) -> None:
    p = _watches_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(
            json.dumps({"watches": [w.to_dict() for w in watches]}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _next_id(watches: list[Watch]) -> int:
    """Stable, monotonically increasing ids so 'cancel watch N' stays sane."""
    return (max((w.id for w in watches), default=0)) + 1


def add_watch(
    url: str,
    chat_id: int,
    *,
    keyword: str | None = None,
    interval: int = DEFAULT_INTERVAL,
) -> Watch:
    """Persist a new watch and return it (with its assigned id)."""
    watches = load_watches()
    watch = Watch(
        id=_next_id(watches),
        url=url.strip(),
        chat_id=int(chat_id),
        keyword=(keyword.strip() or None) if keyword else None,
        interval=int(interval) if interval > 0 else DEFAULT_INTERVAL,
    )
    watches.append(watch)
    _save(watches)
    return watch


def list_watches(chat_id: int | None = None) -> list[Watch]:
    """List watches, oldest id first. Filters to a chat if given."""
    out = load_watches()
    if chat_id is not None:
        out = [w for w in out if w.chat_id == chat_id]
    return sorted(out, key=lambda w: w.id)


def remove_watch(watch_id: int) -> bool:
    """Delete a watch by id. Returns True if one was removed."""
    watches = load_watches()
    kept = [w for w in watches if w.id != watch_id]
    if len(kept) == len(watches):
        return False
    _save(kept)
    return True


def update_watch(watch_id: int, *, last_hash: str, checked_at: float) -> Watch | None:
    """Persist the result of a check: record the new hash and the check time.

    Returns the updated watch, or None if the id is unknown. Used by the poll
    loop after each fetch so an unchanged page is not re-reported and a throttle
    window can be measured from the last check.
    """
    watches = load_watches()
    updated: Watch | None = None
    for w in watches:
        if w.id != watch_id:
            continue
        w.last_hash = last_hash
        w.last_checked = float(checked_at)
        updated = w
        break
    if updated is not None:
        _save(watches)
    return updated


def due_watches(watches: list[Watch], now_epoch: float) -> list[Watch]:
    """The subset due for a re-check at ``now_epoch`` (pure, no side effects).

    A watch is due when it has never been checked (``last_checked`` is 0), or its
    throttle window has elapsed since the last check. Keeping this pure lets the
    poll loop test the throttle without a real clock.
    """
    return [
        w
        for w in watches
        if w.last_checked <= 0 or (now_epoch - w.last_checked) >= w.interval
    ]


# --------------------------------------------------------------------------
# The check step. Fetch is injected so the loop and tests need no network.
# --------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Outcome of checking one watch.

    ``changed`` is True only when there was a previous snapshot and the new hash
    differs. The first successful check (no prior hash) records the baseline but
    is NOT a change, so a freshly added watch does not ping on its first tick.
    ``new_hash`` is "" when the fetch failed or returned nothing; in that case the
    stored hash is left untouched.
    """

    changed: bool
    new_hash: str
    fetched: bool


def check_watch(watch: Watch, fetch: Callable[[str], str]) -> CheckResult:
    """Fetch the watched page and decide whether it changed. Pure given ``fetch``.

    ``fetch(url) -> str`` returns the page's readable text (the real one is
    ``web_tools.fetch_url``). A fetch error string (starts with "ERROR") or empty
    text is treated as "no snapshot": we report no change and keep the old hash so
    a transient failure never looks like a change and never wipes the baseline.
    """
    try:
        page = fetch(watch.url)
    except Exception:  # noqa: BLE001 - a fetch blowup is just "no snapshot"
        return CheckResult(changed=False, new_hash="", fetched=False)

    if not page or page.startswith("ERROR"):
        return CheckResult(changed=False, new_hash="", fetched=False)

    new_hash = content_hash(page, watch.keyword)
    if not new_hash:
        return CheckResult(changed=False, new_hash="", fetched=False)

    # First successful check sets the baseline; that is not a change.
    if not watch.last_hash:
        return CheckResult(changed=False, new_hash=new_hash, fetched=True)

    return CheckResult(changed=new_hash != watch.last_hash, new_hash=new_hash, fetched=True)


# --------------------------------------------------------------------------
# Parsing. Self-contained, stdlib only. No NLP, no extra deps.
# --------------------------------------------------------------------------


@dataclass
class ParsedWatch:
    """The result of parsing a message.

    ``kind`` is one of:
      * "add"    -> a watch to store (url set; keyword optional)
      * "list"   -> the user asked to list watches
      * "cancel" -> the user asked to cancel one (cancel_id set)
      * "error"  -> looked like a watch but no usable url was found (``error`` set)
      * None     -> not a watch message at all (let the agent handle it)
    """

    kind: str | None = None
    url: str = ""
    keyword: str | None = None
    cancel_id: int | None = None
    error: str | None = None


_URL_RE = re.compile(r"(https?://[^\s]+|(?:www\.)[^\s]+|[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?)", re.IGNORECASE)


def parse_watch(message: str) -> ParsedWatch | None:
    """Parse a message into a watch intent, or None if it is not one.

    Recognizes, case-insensitively:
      * "list watches" / "watches"               -> kind="list"
      * "cancel watch 3" / "delete watch 3"      -> kind="cancel", cancel_id=3
      * "watch <url>"                            -> kind="add"
      * "watch <url> for <keyword>"              -> kind="add" with a keyword
      * "watch <url> for changes"                -> kind="add" (no keyword;
                                                    "changes"/"change" is the
                                                    default, not a keyword)

    On a "watch ..." message with no usable url it returns kind="error" so the
    bot can ask for a url instead of silently running the agent. Pure.
    """
    raw = (message or "").strip()
    low = raw.lower()
    if not raw:
        return None

    # --- list ---------------------------------------------------------------
    if low in {"list watches", "watches", "list watch", "show watches"}:
        return ParsedWatch(kind="list")

    # --- cancel watch N -----------------------------------------------------
    m = re.match(r"^(?:cancel|delete|remove|stop)\s+watch\s+#?(\d+)\b", low)
    if m:
        return ParsedWatch(kind="cancel", cancel_id=int(m.group(1)))

    # --- watch <url> [for <keyword>] ----------------------------------------
    if not (low.startswith("watch ") or low == "watch"):
        return None

    body = raw[len("watch"):].strip()

    # Pull an optional "for <keyword>" tail BEFORE we read the url, so a keyword
    # like "for price" is not mistaken for part of the url.
    keyword: str | None = None
    fm = re.search(r"\bfor\b\s+(.*)$", body, flags=re.IGNORECASE | re.DOTALL)
    if fm:
        keyword = fm.group(1).strip() or None
        body = body[: fm.start()].strip()
        # "watch <url> for changes" means "for any change", not a keyword.
        if keyword and keyword.lower() in {"changes", "change", "updates", "a change", "any change"}:
            keyword = None

    um = _URL_RE.search(body)
    if not um:
        return ParsedWatch(
            kind="error",
            error="give me a url to watch, e.g. 'watch https://example.com for price'.",
        )
    url = um.group(1).strip().rstrip(".,)")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return ParsedWatch(kind="add", url=url, keyword=keyword)


# --------------------------------------------------------------------------
# Human-readable formatting for confirmations and the list reply.
# --------------------------------------------------------------------------


def confirm_text(watch: Watch) -> str:
    """The confirmation line sent back when a watch is stored."""
    what = f" for '{watch.keyword}'" if watch.keyword else ""
    return f"ok, watching #{watch.id}{what}: {watch.url}. I will ping you when it changes."


def change_text(watch: Watch) -> str:
    """The message sent when a watched page changed."""
    what = f" ('{watch.keyword}')" if watch.keyword else ""
    return f"watch #{watch.id}{what} changed: {watch.url}"


def list_text(watches: list[Watch]) -> str:
    """Render a list of watches for the chat, or a friendly empty message."""
    if not watches:
        return "no watches set. Try 'watch https://example.com for price'."
    lines = ["your watches:"]
    for w in watches:
        what = f" for '{w.keyword}'" if w.keyword else ""
        lines.append(f"#{w.id}{what} - {w.url}")
    return "\n".join(lines)
