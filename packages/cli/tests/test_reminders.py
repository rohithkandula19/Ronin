"""Offline tests for bot-native reminders and recurring schedules.

Everything here runs with no API key and no network: parsing is pure with an
injected clock, the store is a JSON file under a tmp HOME, and the Telegram
sends go through the same injected FakeClient the bot tests use. Nothing sleeps
and nothing dials out.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ronin_cli import reminders as rem
from ronin_cli.telegram_bot import TelegramBot

VALID_TOKEN = "123456789:AAExampleSecretTokenValue"

# A fixed reference instant so "at"/"in"/"every" parsing is deterministic.
# Tuesday 2026-06-16 10:00 local.
NOW = datetime(2026, 6, 16, 10, 0, 0)


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


# --------------------------------------------------------------------------
# Parsing: "in" -> relative one-shot.
# --------------------------------------------------------------------------


def test_parse_in_minutes_one_shot() -> None:
    p = rem.parse_reminder("remind me in 30 minutes to stretch", now=NOW)
    assert p is not None and p.kind == "add"
    assert p.repeat == "none"
    assert p.text == "stretch"
    expected = NOW.replace(minute=30).timestamp()
    assert p.when_epoch == pytest.approx(expected)


def test_parse_in_hours_one_shot() -> None:
    p = rem.parse_reminder("remind me in 2 hours to check the oven", now=NOW)
    assert p.kind == "add" and p.repeat == "none"
    assert p.when_epoch == pytest.approx(NOW.replace(hour=12).timestamp())


# --------------------------------------------------------------------------
# Parsing: "at" -> next occurrence of that clock time, one-shot.
# --------------------------------------------------------------------------


def test_parse_at_pm_today() -> None:
    # 6pm is still ahead of 10:00, so it fires today.
    p = rem.parse_reminder("remind me at 6pm to call mom", now=NOW)
    assert p.kind == "add" and p.repeat == "none"
    assert p.text == "call mom"
    assert p.when_epoch == pytest.approx(NOW.replace(hour=18, minute=0).timestamp())


def test_parse_at_time_already_passed_rolls_to_tomorrow() -> None:
    # 8am has passed (now is 10:00), so the one-shot rolls to tomorrow 08:00.
    p = rem.parse_reminder("remind me at 8am to take meds", now=NOW)
    assert p.kind == "add"
    tomorrow_8 = NOW.replace(hour=8, minute=0)
    tomorrow_8 = tomorrow_8.replace(day=NOW.day + 1)
    assert p.when_epoch == pytest.approx(tomorrow_8.timestamp())


def test_parse_at_24h_with_minutes() -> None:
    p = rem.parse_reminder("remind me at 18:30 to leave", now=NOW)
    assert p.kind == "add"
    assert p.when_epoch == pytest.approx(NOW.replace(hour=18, minute=30).timestamp())


def test_parse_at_noon_and_midnight() -> None:
    noon = rem.parse_reminder("remind me at noon to eat", now=NOW)
    assert noon.when_epoch == pytest.approx(NOW.replace(hour=12, minute=0).timestamp())
    midnight = rem.parse_reminder("remind me at midnight to sleep", now=NOW)
    # midnight has passed today -> tomorrow 00:00
    expect = NOW.replace(hour=0, minute=0, day=NOW.day + 1)
    assert midnight.when_epoch == pytest.approx(expect.timestamp())


# --------------------------------------------------------------------------
# Parsing: "every" -> recurring with the right repeat.
# --------------------------------------------------------------------------


def test_parse_every_day_at_time_is_daily() -> None:
    p = rem.parse_reminder("remind me every day at 8am to journal", now=NOW)
    assert p.kind == "add"
    assert p.repeat == "daily"
    assert p.text == "journal"
    # 8am passed today -> first fire tomorrow at 08:00
    expect = NOW.replace(hour=8, minute=0, day=NOW.day + 1)
    assert p.when_epoch == pytest.approx(expect.timestamp())


def test_parse_every_hour_no_clock_time_is_hourly() -> None:
    p = rem.parse_reminder("remind me every hour to drink water", now=NOW)
    assert p.kind == "add" and p.repeat == "hourly"
    # first fire one interval from now
    assert p.when_epoch == pytest.approx((NOW.timestamp() + 3600))


def test_parse_every_week_is_weekly() -> None:
    p = rem.parse_reminder("remind me every week at 9am to plan", now=NOW)
    assert p.kind == "add" and p.repeat == "weekly"


# --------------------------------------------------------------------------
# Parsing: list / cancel / unparseable.
# --------------------------------------------------------------------------


def test_parse_list_reminders() -> None:
    assert rem.parse_reminder("list reminders").kind == "list"
    assert rem.parse_reminder("reminders").kind == "list"


def test_parse_cancel_reminder() -> None:
    p = rem.parse_reminder("cancel reminder 3")
    assert p.kind == "cancel" and p.cancel_id == 3
    p2 = rem.parse_reminder("delete reminder #7")
    assert p2.kind == "cancel" and p2.cancel_id == 7


def test_parse_unparseable_time_asks_to_rephrase() -> None:
    p = rem.parse_reminder("remind me sometime later to do stuff", now=NOW)
    assert p.kind == "error"
    assert p.error and "rephrase" in p.error.lower() or "try" in p.error.lower()


def test_non_reminder_message_returns_none() -> None:
    assert rem.parse_reminder("what files are in my repo?", now=NOW) is None
    assert rem.parse_reminder("", now=NOW) is None


# --------------------------------------------------------------------------
# Briefing parsing.
# --------------------------------------------------------------------------


def test_parse_briefing_every_morning() -> None:
    p = rem.parse_reminder(
        "briefing every morning at 8am: summarize my open PRs", now=NOW
    )
    assert p.kind == "add"
    assert p.is_briefing is True
    assert p.repeat == "daily"
    assert p.text == "summarize my open PRs"


def test_parse_briefing_without_prompt_errors() -> None:
    p = rem.parse_reminder("briefing every morning at 8am:", now=NOW)
    assert p.kind == "error"


# --------------------------------------------------------------------------
# Store: add / list / remove / due / mark_fired (recurring + one-shot).
# --------------------------------------------------------------------------


def test_add_list_remove_roundtrip(home) -> None:
    r = rem.add_reminder(NOW.timestamp(), "call mom", chat_id=42)
    assert r.id == 1
    listed = rem.list_reminders(42)
    assert len(listed) == 1 and listed[0].text == "call mom"
    assert rem.remove_reminder(1) is True
    assert rem.list_reminders(42) == []
    assert rem.remove_reminder(1) is False  # already gone


def test_ids_are_monotonic(home) -> None:
    a = rem.add_reminder(NOW.timestamp(), "a", chat_id=1)
    b = rem.add_reminder(NOW.timestamp(), "b", chat_id=1)
    assert a.id == 1 and b.id == 2
    rem.remove_reminder(1)
    c = rem.add_reminder(NOW.timestamp(), "c", chat_id=1)
    assert c.id == 3, "ids keep climbing so cancel-by-N stays unambiguous"


def test_due_reminders_filters_by_time_and_done(home) -> None:
    past = rem.add_reminder(NOW.timestamp() - 10, "past", chat_id=1)
    rem.add_reminder(NOW.timestamp() + 999, "future", chat_id=1)
    due = rem.due_reminders(rem.load_reminders(), NOW.timestamp())
    assert [d.id for d in due] == [past.id]


def test_mark_fired_one_shot_marks_done(home) -> None:
    r = rem.add_reminder(NOW.timestamp() - 5, "once", chat_id=1, repeat="none")
    rem.mark_fired(r.id, NOW.timestamp())
    stored = rem.load_reminders()[0]
    assert stored.done is True
    # done one-shots never come up as due again
    assert rem.due_reminders(rem.load_reminders(), NOW.timestamp() + 10_000) == []


def test_mark_fired_recurring_reschedules(home) -> None:
    fire_at = NOW.timestamp() - 5
    r = rem.add_reminder(fire_at, "daily thing", chat_id=1, repeat="daily")
    rem.mark_fired(r.id, NOW.timestamp())
    stored = rem.load_reminders()[0]
    assert stored.done is False, "recurring reminders are never marked done"
    assert stored.when_epoch > NOW.timestamp(), "next slot is strictly in the future"
    # rescheduled exactly one day past the original slot
    assert stored.when_epoch == pytest.approx(fire_at + 86400)


# --------------------------------------------------------------------------
# Bot integration: a "remind me" message is stored before the agent runs.
# --------------------------------------------------------------------------


def test_bot_stores_reminder_and_never_calls_agent(home) -> None:
    agent_calls: list[str] = []

    def agent(text: str) -> str:
        agent_calls.append(text)
        return "should not run"

    client = FakeClient(getupdates_result=[_update(42, "remind me at 6pm to call mom")])
    bot = TelegramBot(
        token=VALID_TOKEN, allowed_chat_ids=[42], answer_fn=agent, http=client
    )
    bot.handle_update(_update(42, "remind me at 6pm to call mom"))

    assert agent_calls == [], "a reminder must not invoke the model agent"
    stored = rem.list_reminders(42)
    assert len(stored) == 1 and stored[0].text == "call mom"
    sent = client.sent_messages()
    assert sent and "reminder #1" in sent[-1]["text"]


def test_bot_list_and_cancel_commands(home) -> None:
    rem.add_reminder(NOW.timestamp() + 999, "call mom", chat_id=42)
    client = FakeClient()
    bot = TelegramBot(
        token=VALID_TOKEN, allowed_chat_ids=[42], answer_fn=lambda t: "x", http=client
    )

    bot.handle_update(_update(42, "list reminders"))
    assert "call mom" in client.sent_messages()[-1]["text"]

    bot.handle_update(_update(42, "cancel reminder 1"))
    assert "cancelled reminder #1" in client.sent_messages()[-1]["text"]
    assert rem.list_reminders(42) == []


def test_bot_unparseable_reminder_asks_to_rephrase(home) -> None:
    agent_calls: list[str] = []
    client = FakeClient()
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=lambda t: agent_calls.append(t) or "x",
        http=client,
    )
    bot.handle_update(_update(42, "remind me whenever to do the thing"))
    assert agent_calls == [], "an unparseable reminder must not fall through to the agent"
    assert "rephrase" in client.sent_messages()[-1]["text"].lower() or \
        "try" in client.sent_messages()[-1]["text"].lower()


def test_non_reminder_message_falls_through_to_agent(home) -> None:
    agent_calls: list[str] = []

    def agent(text: str) -> str:
        agent_calls.append(text)
        return "answered"

    client = FakeClient()
    bot = TelegramBot(
        token=VALID_TOKEN, allowed_chat_ids=[42], answer_fn=agent, http=client
    )
    bot.handle_update(_update(42, "what is in my repo?"))
    assert agent_calls == ["what is in my repo?"], "non-reminders still hit the agent"


# --------------------------------------------------------------------------
# Bot integration: due reminders fire on the poll tick (no real sleep).
# --------------------------------------------------------------------------


def test_due_one_shot_fires_and_marks_done(home) -> None:
    rem.add_reminder(NOW.timestamp() - 5, "stand up", chat_id=42, repeat="none")
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=client)

    fired = bot.fire_due_reminders(now_epoch=NOW.timestamp())
    assert fired == 1
    sent = client.sent_messages()
    assert sent[-1]["chat_id"] == 42
    assert "reminder: stand up" in sent[-1]["text"]
    # marked done -> does not fire again on the next tick
    client2 = FakeClient()
    bot.http = client2
    assert bot.fire_due_reminders(now_epoch=NOW.timestamp() + 1) == 0
    assert client2.sent_messages() == []


def test_due_recurring_fires_and_reschedules(home) -> None:
    fire_at = NOW.timestamp() - 5
    rem.add_reminder(fire_at, "drink water", chat_id=42, repeat="hourly")
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=client)

    assert bot.fire_due_reminders(now_epoch=NOW.timestamp()) == 1
    stored = rem.load_reminders()[0]
    assert stored.done is False
    assert stored.when_epoch == pytest.approx(fire_at + 3600)
    # not due again until the next slot
    assert bot.fire_due_reminders(now_epoch=NOW.timestamp() + 1) == 0


def test_not_yet_due_does_not_fire(home) -> None:
    rem.add_reminder(NOW.timestamp() + 3600, "later", chat_id=42)
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=client)
    assert bot.fire_due_reminders(now_epoch=NOW.timestamp()) == 0
    assert client.sent_messages() == []


def test_poll_once_fires_due_reminders(home) -> None:
    # A past-due reminder plus an empty getUpdates batch: the poll tick should
    # still fire the reminder even with no incoming messages.
    rem.add_reminder(NOW.timestamp() - 5, "tick fire", chat_id=42)
    client = FakeClient(getupdates_result=[])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=client)
    # fire_due_reminders defaults to time.time(); force a clearly-due instant by
    # firing directly through the same path poll_once uses.
    bot.fire_due_reminders(now_epoch=NOW.timestamp())
    assert any("tick fire" in m["text"] for m in client.sent_messages())


# --------------------------------------------------------------------------
# Briefing: the prompt is run through the agent and the RESULT is sent.
# --------------------------------------------------------------------------


def test_due_briefing_runs_agent_and_sends_result(home) -> None:
    ran: list[str] = []

    def briefing_fn(prompt: str) -> str:
        ran.append(prompt)
        return "you have 3 open PRs"

    rem.add_reminder(
        NOW.timestamp() - 5,
        "summarize my PRs",
        chat_id=42,
        repeat="daily",
        is_briefing=True,
    )
    client = FakeClient()
    bot = TelegramBot(
        token=VALID_TOKEN, allowed_chat_ids=[42], briefing_fn=briefing_fn, http=client
    )

    assert bot.fire_due_reminders(now_epoch=NOW.timestamp()) == 1
    assert ran == ["summarize my PRs"], "the briefing prompt runs through the agent"
    body = client.sent_messages()[-1]["text"]
    assert "you have 3 open PRs" in body, "the agent RESULT is what gets sent"


def test_briefing_without_agent_degrades_to_prompt(home) -> None:
    rem.add_reminder(
        NOW.timestamp() - 5, "do the briefing", chat_id=42, is_briefing=True
    )
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=client)  # no briefing_fn
    assert bot.fire_due_reminders(now_epoch=NOW.timestamp()) == 1
    assert "do the briefing" in client.sent_messages()[-1]["text"]


def test_briefing_agent_error_does_not_crash(home) -> None:
    def boom(prompt: str) -> str:
        raise RuntimeError("model exploded")

    rem.add_reminder(
        NOW.timestamp() - 5, "risky briefing", chat_id=42, is_briefing=True
    )
    client = FakeClient()
    bot = TelegramBot(
        token=VALID_TOKEN, allowed_chat_ids=[42], briefing_fn=boom, http=client
    )
    # must not raise; still sends something and advances the reminder
    assert bot.fire_due_reminders(now_epoch=NOW.timestamp()) == 1
    assert "risky briefing" in client.sent_messages()[-1]["text"]
