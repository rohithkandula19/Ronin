"""Notification channels, redaction, and a fail-closed fan-out notifier.

Only local/in-app delivery is enabled: :class:`InMemoryChannel` records what
it "sends". Real external delivery (email, SMS) is modelled as DISABLED stubs
whose ``send`` raises :class:`ChannelDisabled` with reason
``BLOCKED_CREDENTIALS`` — honest about the fact that real delivery requires
provider credentials plus authorization that this package deliberately does
not carry.

Before anything is recorded or sent, sensitive-looking content is redacted
from the body so raw secrets/PII are never logged. Delivery is fail-closed:
a recipient only receives a category they are explicitly subscribed to, and an
unknown category is never delivered.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

BLOCKED_CREDENTIALS = "BLOCKED_CREDENTIALS"


class ChannelDisabled(RuntimeError):
    """Raised by a stub channel whose real delivery path is not available."""


# --- Redaction ---------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Long digit runs (card / account numbers), tolerating spaces or dashes.
_LONG_DIGITS_RE = re.compile(r"\b(?:\d[ \-]?){12,19}\b")
# US-style SSN.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Phone numbers with optional country code.
_PHONE_RE = re.compile(r"\+?\d{1,3}[ \-]?\(?\d{3}\)?[ \-]?\d{3}[ \-]?\d{4}\b")
# Labelled sensitive values, e.g. "password: swordfish" or "api-key = value".
_LABELLED_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_\-]?key|bearer)\b\s*[:=]?\s*\S+"
)


def redact(text: str) -> str:
    """Return ``text`` with sensitive-looking substrings masked.

    The ordering matters: labelled secrets and SSNs are masked before the
    broader phone/digit sweeps so the most specific label wins.
    """
    text = _LABELLED_RE.sub("[REDACTED_SECRET]", text)
    text = _SSN_RE.sub("[REDACTED_SSN]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _LONG_DIGITS_RE.sub("[REDACTED_NUMBER]", text)
    return text


# --- Channels ----------------------------------------------------------------


@runtime_checkable
class NotificationChannel(Protocol):
    """A delivery channel. Implementations must be side-effect honest."""

    name: str
    enabled: bool

    def send(self, recipient: str, subject: str, body: str, *, now: str) -> None:
        """Deliver a message, or raise if the channel is disabled."""
        ...


class SentMessage(BaseModel):
    """A record of a message accepted by :class:`InMemoryChannel`."""

    model_config = ConfigDict(extra="forbid")

    recipient: str
    subject: str
    body: str
    at: str


class InMemoryChannel:
    """Enabled local channel that records delivered messages in memory."""

    name = "in_memory"
    enabled = True

    def __init__(self, name: str = "in_memory") -> None:
        self.name = name
        self.sent: list[SentMessage] = []

    def send(self, recipient: str, subject: str, body: str, *, now: str) -> None:
        """Record the (already-redacted) message."""
        self.sent.append(
            SentMessage(recipient=recipient, subject=subject, body=body, at=now)
        )


class _DisabledChannel:
    """Base for stub channels that cannot perform real delivery."""

    name = "disabled"
    enabled = False

    def send(self, recipient: str, subject: str, body: str, *, now: str) -> None:
        raise ChannelDisabled(
            f"{self.name} delivery requires provider credentials + "
            f"authorization = {BLOCKED_CREDENTIALS}"
        )


class EmailChannel(_DisabledChannel):
    """DISABLED email stub. Real delivery is BLOCKED_CREDENTIALS."""

    name = "email"


class SmsChannel(_DisabledChannel):
    """DISABLED SMS stub. Real delivery is BLOCKED_CREDENTIALS."""

    name = "sms"


# --- Notifier ----------------------------------------------------------------


class DeliveryResult(BaseModel):
    """Outcome of a single :meth:`Notifier.notify` call."""

    model_config = ConfigDict(extra="forbid")

    delivered: list[str] = Field(default_factory=list)
    blocked: dict[str, str] = Field(default_factory=dict)
    skipped_reason: str | None = None
    redacted_body: str = ""

    @property
    def any_delivered(self) -> bool:
        """True if at least one channel accepted the message."""
        return bool(self.delivered)


class Notifier:
    """Fans a notification out to registered channels, fail-closed.

    A message is only delivered when its category is one of the configured
    ``categories`` AND the recipient is subscribed to that category. Bodies are
    redacted before any channel sees them. Disabled channels that raise
    :class:`ChannelDisabled` are recorded as blocked rather than crashing the
    whole fan-out.
    """

    def __init__(self, *, categories: set[str]) -> None:
        if not categories:
            raise ValueError("at least one category must be configured")
        self._categories = set(categories)
        self._channels: dict[str, NotificationChannel] = {}
        self._subscriptions: dict[str, set[str]] = {}

    def register_channel(self, channel: NotificationChannel) -> None:
        """Register a channel by its ``name``."""
        self._channels[channel.name] = channel

    def subscribe(self, recipient: str, categories: set[str]) -> None:
        """Set the categories ``recipient`` is subscribed to.

        Unknown categories are rejected fail-closed rather than silently kept.
        """
        unknown = categories - self._categories
        if unknown:
            raise ValueError(f"unknown categories: {sorted(unknown)}")
        self._subscriptions[recipient] = set(categories)

    def subscriptions(self, recipient: str) -> set[str]:
        """Return the categories ``recipient`` is subscribed to (may be empty)."""
        return set(self._subscriptions.get(recipient, set()))

    def notify(
        self,
        *,
        category: str,
        recipient: str,
        subject: str,
        body: str,
        now: str,
    ) -> DeliveryResult:
        """Attempt delivery of one notification. Never raises for delivery.

        Returns a :class:`DeliveryResult` describing what happened. Skips
        (fail-closed) when the category is unknown or the recipient is not
        subscribed to it.
        """
        redacted = redact(body)
        result = DeliveryResult(redacted_body=redacted)

        if category not in self._categories:
            result.skipped_reason = f"unknown category: {category}"
            return result
        if category not in self._subscriptions.get(recipient, set()):
            result.skipped_reason = "recipient not subscribed to category"
            return result

        for name, channel in self._channels.items():
            try:
                channel.send(recipient, subject, redacted, now=now)
            except ChannelDisabled as exc:
                result.blocked[name] = str(exc)
            else:
                result.delivered.append(name)
        return result
