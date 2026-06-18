"""Offline tests for page watches (watch a url, ping on change).

Everything here runs with no API key and no network: parsing and hashing are
pure, the store is a JSON file under a tmp HOME, the fetch is an injected stub,
and the Telegram sends go through the same injected FakeClient the bot tests use.
Nothing sleeps and nothing dials out.
"""
from __future__ import annotations

import pytest

from ronin_cli import page_watch as pw
from ronin_cli.telegram_bot import TelegramBot

VALID_TOKEN = "123456789:AAExampleSecretTokenValue"
CHAT = 4242


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# Fake Telegram client (mirrors test_telegram_bot.py - no network).
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, getupdates_result: list | None = None) -> None:
        self.calls: list[dict] = []
        self._getupdates_result = getupdates_result or []
        self._served = False

    def post(self, url: str, json: dict | None = None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        if url.endswith("/getUpdates"):
            if not self._served:
                self._served = True
                return FakeResponse({"ok": True, "result": self._getupdates_result})
            return FakeResponse({"ok": True, "result": []})
        if url.endswith("/sendMessage"):
            return FakeResponse({"ok": True, "result": {"message_id": 1}})
        return FakeResponse({"ok": True, "result": {}})

    def sent_messages(self) -> list[dict]:
        return [c["json"] for c in self.calls if c["url"].endswith("/sendMessage")]


def _update(chat_id: int, text: str, update_id: int = 100) -> dict:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


def _bot(client, fetch_fn=None) -> TelegramBot:
    return TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[CHAT],
        answer_fn=lambda t: "AGENT SHOULD NOT RUN",
        http=client,
        fetch_fn=fetch_fn,
    )


# --------------------------------------------------------------------------
# Content extraction + hashing (pure).
# --------------------------------------------------------------------------


def test_extract_content_whole_page_normalizes_whitespace() -> None:
    a = pw.extract_content("hello   world\n\n\nbye")
    b = pw.extract_content("hello world\nbye")
    assert a == b


def test_extract_content_keyword_keeps_only_matching_lines() -> None:
    page = "Price: $10\nDescription: a thing\nShipping: free"
    out = pw.extract_content(page, keyword="price")
    assert "Price: $10" in out
    assert "Description" not in out


def test_content_hash_changes_with_content() -> None:
    assert pw.content_hash("a") != pw.content_hash("b")
    assert pw.content_hash("a") == pw.content_hash("a")


def test_content_hash_keyword_ignores_unrelated_change() -> None:
    p1 = "Price: $10\nViews: 5"
    p2 = "Price: $10\nViews: 9999"
    assert pw.content_hash(p1, "price") == pw.content_hash(p2, "price")
    # without a keyword the unrelated change DOES move the hash
    assert pw.content_hash(p1) != pw.content_hash(p2)


def test_content_hash_empty_is_empty() -> None:
    assert pw.content_hash("") == ""
    assert pw.content_hash("price: 1", keyword="absent") == ""


# --------------------------------------------------------------------------
# Store: add / list / cancel.
# --------------------------------------------------------------------------


def test_add_list_cancel_roundtrip(home) -> None:
    w = pw.add_watch("https://example.com", CHAT, keyword="price")
    assert w.id == 1 and w.keyword == "price"

    watches = pw.list_watches(CHAT)
    assert [x.id for x in watches] == [1]
    assert watches[0].url == "https://example.com"

    assert pw.remove_watch(1) is True
    assert pw.list_watches(CHAT) == []
    # cancelling a missing id is a no-op (False), not an error
    assert pw.remove_watch(99) is False


def test_list_filters_by_chat(home) -> None:
    pw.add_watch("https://a.com", CHAT)
    pw.add_watch("https://b.com", 9999)
    assert {w.url for w in pw.list_watches(CHAT)} == {"https://a.com"}


def test_ids_are_monotonic(home) -> None:
    pw.add_watch("https://a.com", CHAT)
    pw.add_watch("https://b.com", CHAT)
    pw.remove_watch(1)
    third = pw.add_watch("https://c.com", CHAT)
    assert third.id == 3  # never reuses a freed id


# --------------------------------------------------------------------------
# Parsing.
# --------------------------------------------------------------------------


def test_parse_watch_bare_url() -> None:
    p = pw.parse_watch("watch https://example.com/page")
    assert p is not None and p.kind == "add"
    assert p.url == "https://example.com/page"
    assert p.keyword is None


def test_parse_watch_adds_scheme() -> None:
    p = pw.parse_watch("watch example.com")
    assert p.kind == "add" and p.url == "https://example.com"


def test_parse_watch_for_keyword() -> None:
    p = pw.parse_watch("watch https://shop.com/item for price")
    assert p.kind == "add" and p.url == "https://shop.com/item"
    assert p.keyword == "price"


def test_parse_watch_for_changes_is_not_a_keyword() -> None:
    p = pw.parse_watch("watch https://x.com for changes")
    assert p.kind == "add" and p.keyword is None


def test_parse_watch_no_url_is_error() -> None:
    p = pw.parse_watch("watch the news for me")
    # "news" has no dot/scheme so no url -> error (asks for a url)
    assert p.kind == "error" and p.error


def test_parse_list_and_cancel() -> None:
    assert pw.parse_watch("list watches").kind == "list"
    assert pw.parse_watch("watches").kind == "list"
    c = pw.parse_watch("cancel watch 3")
    assert c.kind == "cancel" and c.cancel_id == 3


def test_parse_non_watch_returns_none() -> None:
    assert pw.parse_watch("what is the weather") is None
    assert pw.parse_watch("") is None


# --------------------------------------------------------------------------
# check_watch: changed / unchanged / first-check / fetch failure.
# --------------------------------------------------------------------------


def test_check_first_time_sets_baseline_not_a_change() -> None:
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_hash="")
    res = pw.check_watch(w, fetch=lambda u: "version one")
    assert res.fetched is True
    assert res.changed is False  # first check is a baseline, not a change
    assert res.new_hash == pw.content_hash("version one")


def test_check_unchanged_page_no_change() -> None:
    base = pw.content_hash("same content")
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_hash=base)
    res = pw.check_watch(w, fetch=lambda u: "same content")
    assert res.changed is False
    assert res.new_hash == base


def test_check_changed_page_is_a_change() -> None:
    base = pw.content_hash("old")
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_hash=base)
    res = pw.check_watch(w, fetch=lambda u: "new")
    assert res.changed is True
    assert res.new_hash == pw.content_hash("new")


def test_check_fetch_error_keeps_old_hash() -> None:
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_hash="abc")
    res = pw.check_watch(w, fetch=lambda u: "ERROR: fetch failed: boom")
    assert res.fetched is False and res.changed is False


def test_check_fetch_exception_is_no_change() -> None:
    def boom(u):
        raise RuntimeError("no network")

    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_hash="abc")
    res = pw.check_watch(w, fetch=boom)
    assert res.fetched is False and res.changed is False


# --------------------------------------------------------------------------
# Throttle: due_watches respects the interval window.
# --------------------------------------------------------------------------


def test_due_watches_respects_interval() -> None:
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_checked=1000.0, interval=900)
    # only 100s elapsed -> not due
    assert pw.due_watches([w], now_epoch=1100.0) == []
    # 900s elapsed -> due
    assert pw.due_watches([w], now_epoch=1900.0) == [w]


def test_due_watches_never_checked_is_due() -> None:
    w = pw.Watch(id=1, url="u", chat_id=CHAT, last_checked=0.0)
    assert pw.due_watches([w], now_epoch=10.0) == [w]


# --------------------------------------------------------------------------
# Bot integration: add via message, fire on change, no-fire when unchanged.
# --------------------------------------------------------------------------


def test_bot_add_watch_via_message(home) -> None:
    client = FakeClient()
    bot = _bot(client)
    bot.handle_update(_update(CHAT, "watch https://example.com for price"))

    sent = client.sent_messages()
    assert any("watching #1" in m["text"] for m in sent)
    stored = pw.list_watches(CHAT)
    assert len(stored) == 1 and stored[0].keyword == "price"


def test_bot_list_and_cancel_via_message(home) -> None:
    pw.add_watch("https://a.com", CHAT)
    client = FakeClient()
    bot = _bot(client)

    bot.handle_update(_update(CHAT, "list watches", update_id=1))
    assert any("https://a.com" in m["text"] for m in client.sent_messages())

    bot.handle_update(_update(CHAT, "cancel watch 1", update_id=2))
    assert any("cancelled watch #1" in m["text"] for m in client.sent_messages())
    assert pw.list_watches(CHAT) == []


def test_changed_page_triggers_notification_and_updates_hash(home) -> None:
    # Seed a watch with an existing baseline hash for "old".
    w = pw.add_watch("https://example.com", CHAT)
    pw.update_watch(w.id, last_hash=pw.content_hash("old"), checked_at=0.0)

    client = FakeClient()
    bot = _bot(client, fetch_fn=lambda url: "new content here")

    changed = bot.fire_due_watches(now_epoch=10_000.0)
    assert changed == 1

    sent = client.sent_messages()
    assert any("changed" in m["text"] and "https://example.com" in m["text"] for m in sent)

    # last_hash advanced to the new content's hash and check time recorded.
    stored = pw.list_watches(CHAT)[0]
    assert stored.last_hash == pw.content_hash("new content here")
    assert stored.last_checked == 10_000.0


def test_unchanged_page_does_not_notify(home) -> None:
    w = pw.add_watch("https://example.com", CHAT)
    base = pw.content_hash("steady content")
    pw.update_watch(w.id, last_hash=base, checked_at=0.0)

    client = FakeClient()
    bot = _bot(client, fetch_fn=lambda url: "steady content")

    changed = bot.fire_due_watches(now_epoch=10_000.0)
    assert changed == 0
    assert client.sent_messages() == []
    # hash unchanged, check time still advanced
    stored = pw.list_watches(CHAT)[0]
    assert stored.last_hash == base
    assert stored.last_checked == 10_000.0


def test_first_check_records_baseline_without_notifying(home) -> None:
    pw.add_watch("https://example.com", CHAT)  # no baseline yet
    client = FakeClient()
    bot = _bot(client, fetch_fn=lambda url: "first snapshot")

    changed = bot.fire_due_watches(now_epoch=10_000.0)
    assert changed == 0
    assert client.sent_messages() == []
    stored = pw.list_watches(CHAT)[0]
    assert stored.last_hash == pw.content_hash("first snapshot")


def test_throttle_skips_recently_checked_watch(home) -> None:
    w = pw.add_watch("https://example.com", CHAT)
    pw.update_watch(w.id, last_hash=pw.content_hash("old"), checked_at=9_950.0)

    calls = []
    client = FakeClient()
    bot = _bot(client, fetch_fn=lambda url: calls.append(url) or "new")

    # only 50s since last check (interval 900) -> not due, no fetch, no message
    changed = bot.fire_due_watches(now_epoch=10_000.0)
    assert changed == 0
    assert calls == []
    assert client.sent_messages() == []


def test_poll_once_fires_watches(home, monkeypatch) -> None:
    # The single poll tick drives watches alongside reminders (one loop).
    w = pw.add_watch("https://example.com", CHAT)
    pw.update_watch(w.id, last_hash=pw.content_hash("old"), checked_at=0.0)

    client = FakeClient(getupdates_result=[])
    bot = _bot(client, fetch_fn=lambda url: "brand new")
    # freeze time so the throttle window is satisfied
    monkeypatch.setattr("time.time", lambda: 10_000.0)

    bot.poll_once()
    assert any("changed" in m["text"] for m in client.sent_messages())
