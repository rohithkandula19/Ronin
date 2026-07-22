"""Tests for notifications: channels, redaction, and fail-closed fan-out."""

from __future__ import annotations

import pytest

from ronin_support import (
    BLOCKED_CREDENTIALS,
    ChannelDisabled,
    EmailChannel,
    InMemoryChannel,
    NotificationChannel,
    Notifier,
    SmsChannel,
    redact,
)

NOW = "2026-07-22T12:00:00+00:00"


def test_in_memory_channel_records() -> None:
    ch = InMemoryChannel()
    ch.send("user@example.com", "hi", "body", now=NOW)
    assert len(ch.sent) == 1
    assert ch.sent[0].at == NOW
    assert ch.enabled is True


def test_in_memory_satisfies_protocol() -> None:
    assert isinstance(InMemoryChannel(), NotificationChannel)


def test_email_channel_disabled() -> None:
    with pytest.raises(ChannelDisabled) as exc:
        EmailChannel().send("r", "s", "b", now=NOW)
    assert BLOCKED_CREDENTIALS in str(exc.value)
    assert EmailChannel().enabled is False


def test_sms_channel_disabled() -> None:
    with pytest.raises(ChannelDisabled) as exc:
        SmsChannel().send("r", "s", "b", now=NOW)
    assert BLOCKED_CREDENTIALS in str(exc.value)


def test_redact_email() -> None:
    out = redact("contact me at agent@example.com please")
    assert "agent@example.com" not in out
    assert "[REDACTED_EMAIL]" in out


def test_redact_ssn() -> None:
    out = redact("ssn is 123-45-6789 ok")
    assert "123-45-6789" not in out
    assert "[REDACTED_SSN]" in out


def test_redact_long_number() -> None:
    out = redact("card 4111 1111 1111 1111 end")
    assert "4111 1111 1111 1111" not in out
    assert "[REDACTED_NUMBER]" in out


def test_redact_phone() -> None:
    out = redact("call +1 415 555 0100 now")
    assert "415 555 0100" not in out
    assert "[REDACTED_PHONE]" in out


def test_redact_labelled_secret() -> None:
    out = redact("password: sunflower")
    assert "sunflower" not in out
    assert "[REDACTED_SECRET]" in out


def test_redact_plain_text_untouched() -> None:
    assert redact("everything is fine here") == "everything is fine here"


def _notifier() -> tuple[Notifier, InMemoryChannel]:
    n = Notifier(categories={"incident", "billing"})
    ch = InMemoryChannel()
    n.register_channel(ch)
    return n, ch


def test_notifier_requires_categories() -> None:
    with pytest.raises(ValueError):
        Notifier(categories=set())


def test_subscribe_unknown_category_rejected() -> None:
    n, _ = _notifier()
    with pytest.raises(ValueError):
        n.subscribe("user@example.com", {"gossip"})


def test_delivery_to_subscribed_recipient() -> None:
    n, ch = _notifier()
    n.subscribe("user@example.com", {"incident"})
    result = n.notify(
        category="incident",
        recipient="user@example.com",
        subject="Outage",
        body="down",
        now=NOW,
    )
    assert result.any_delivered is True
    assert "in_memory" in result.delivered
    assert len(ch.sent) == 1


def test_unsubscribed_category_not_delivered() -> None:
    n, ch = _notifier()
    n.subscribe("user@example.com", {"billing"})
    result = n.notify(
        category="incident",
        recipient="user@example.com",
        subject="Outage",
        body="down",
        now=NOW,
    )
    assert result.any_delivered is False
    assert result.skipped_reason == "recipient not subscribed to category"
    assert ch.sent == []


def test_unknown_category_not_delivered() -> None:
    n, ch = _notifier()
    n.subscribe("user@example.com", {"incident"})
    result = n.notify(
        category="marketing",
        recipient="user@example.com",
        subject="Buy",
        body="promo",
        now=NOW,
    )
    assert result.any_delivered is False
    assert result.skipped_reason is not None
    assert "unknown category" in result.skipped_reason
    assert ch.sent == []


def test_no_subscription_record_fails_closed() -> None:
    n, ch = _notifier()
    result = n.notify(
        category="incident",
        recipient="stranger@example.com",
        subject="s",
        body="b",
        now=NOW,
    )
    assert result.any_delivered is False
    assert ch.sent == []


def test_body_is_redacted_before_recording() -> None:
    n, ch = _notifier()
    n.subscribe("user@example.com", {"incident"})
    n.notify(
        category="incident",
        recipient="user@example.com",
        subject="Contact",
        body="reach admin@example.com",
        now=NOW,
    )
    assert "admin@example.com" not in ch.sent[0].body
    assert "[REDACTED_EMAIL]" in ch.sent[0].body


def test_disabled_channel_recorded_as_blocked_not_crash() -> None:
    n = Notifier(categories={"incident"})
    ok = InMemoryChannel()
    n.register_channel(ok)
    n.register_channel(EmailChannel())
    n.subscribe("user@example.com", {"incident"})
    result = n.notify(
        category="incident",
        recipient="user@example.com",
        subject="s",
        body="b",
        now=NOW,
    )
    assert "in_memory" in result.delivered
    assert "email" in result.blocked
    assert BLOCKED_CREDENTIALS in result.blocked["email"]


def test_subscriptions_returns_copy() -> None:
    n, _ = _notifier()
    n.subscribe("user@example.com", {"incident"})
    subs = n.subscriptions("user@example.com")
    subs.add("billing")
    assert n.subscriptions("user@example.com") == {"incident"}
