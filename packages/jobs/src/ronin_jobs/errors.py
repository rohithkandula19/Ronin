"""Error taxonomy for the Ronin job system.

Every failure a handler can produce is classified into a stable, honest
taxonomy so the worker can make deterministic retry / dead-letter decisions
and so callers can surface a *safe* message to end users without ever leaking
raw exception internals.

Design rules:

* ``RoninError`` carries a stable ``code``, a ``retryable`` flag, a
  ``severity``, a ``user_message`` (safe to display), and a redacted
  ``detail`` (safe to log).
* ``user_message`` is never derived from the raw exception text; it defaults
  to a fixed, class-level safe message. Any sensitive substrings passed as
  ``detail`` are redacted before storage.
* ``is_retryable`` classifies *arbitrary* exceptions fail-closed: an unknown
  or plain ``Exception`` is treated as a retryable :class:`SystemError`, but
  the worker also caps attempts for such non-``RoninError`` failures so a
  buggy handler cannot loop forever.
"""

from __future__ import annotations

import re
from enum import Enum


class Severity(str, Enum):
    """Operational severity of an error, ordered from least to most urgent."""

    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


# Patterns that, if present in a raw detail string, are masked before storage.
# Matching is intentionally broad and fail-closed: when in doubt, redact.
_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key=value / key: value pairs whose key looks sensitive
    re.compile(
        r"(?i)\b(pass\w*|secret\w*|tok\w*|api[_-]?key|auth\w*|cred\w*|"
        r"bearer|session|cookie|priv\w*)\b\s*[:=]\s*\S+"
    ),
    # bearer-style header values
    re.compile(r"(?i)\bbearer\s+\S+"),
    # long opaque hex / base64-ish blobs
    re.compile(r"\b[A-Za-z0-9+/=_-]{24,}\b"),
)

_REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Return ``text`` with sensitive-looking substrings masked.

    This is a best-effort, fail-closed scrubber used on any ``detail`` string
    before it is stored on an error. It never raises.
    """

    if not text:
        return text
    scrubbed = text
    for pattern in _REDACTION_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


class RoninError(Exception):
    """Base class for all classified Ronin errors.

    Attributes:
        code: Stable machine-readable identifier (never localized).
        retryable: Whether the worker may retry the job on this error.
        severity: Operational :class:`Severity`.
        user_message: Safe-to-display message. Fixed per class; never built
            from raw exception text.
        detail: Redacted, safe-to-log context string.
    """

    #: Stable code, overridden per subclass.
    code: str = "ronin_error"
    #: Whether this class of error is retryable, overridden per subclass.
    retryable: bool = False
    #: Default severity for this class.
    severity: Severity = Severity.error
    #: Fixed, safe default message shown to users when none is supplied.
    default_user_message: str = "Something went wrong. Please try again later."

    def __init__(
        self,
        detail: str | None = None,
        *,
        user_message: str | None = None,
        severity: Severity | None = None,
    ) -> None:
        """Build a classified error.

        Args:
            detail: Optional context for logs. It is redacted before storage
                and is *never* used as the user-facing message.
            user_message: Optional override for the safe user message. If not
                provided, the class ``default_user_message`` is used. The raw
                ``detail`` is never substituted in here.
            severity: Optional override of the class default severity.
        """

        self.detail: str = redact(detail) if detail else ""
        self.user_message: str = user_message or self.default_user_message
        self.severity = severity or type(self).severity
        # The Exception payload is the SAFE message, so str(exc) never leaks.
        super().__init__(self.user_message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.user_message


class UserError(RoninError):
    """Caller did something invalid; retrying will not help."""

    code = "user_error"
    retryable = False
    severity = Severity.warning
    default_user_message = "The request could not be completed as submitted."


class ValidationError(UserError):
    """Input failed validation; not retryable."""

    code = "validation_error"
    retryable = False
    severity = Severity.warning
    default_user_message = "Some of the provided information was invalid."


class PermissionError(RoninError):  # noqa: A001 - deliberately shadows builtin in this namespace
    """Caller is not allowed to perform the action; not retryable."""

    code = "permission_error"
    retryable = False
    severity = Severity.warning
    default_user_message = "You do not have permission to perform this action."


class PaymentsDisabledError(RoninError):
    """A billing/payment path is disabled; not retryable without a config fix."""

    code = "payments_disabled"
    retryable = False
    severity = Severity.error
    default_user_message = "Payments are currently unavailable."


class RateLimitedError(RoninError):
    """A dependency rate-limited us; retry later."""

    code = "rate_limited"
    retryable = True
    severity = Severity.warning
    default_user_message = "The service is busy. This will be retried shortly."


class ProviderError(RoninError):
    """An upstream provider failed transiently; retryable."""

    code = "provider_error"
    retryable = True
    severity = Severity.error
    default_user_message = "An upstream service failed. This will be retried."


class TimeoutError(RoninError):  # noqa: A001 - deliberately shadows builtin in this namespace
    """An operation timed out; retryable."""

    code = "timeout"
    retryable = True
    severity = Severity.warning
    default_user_message = "The operation timed out. This will be retried."


class SystemError(RoninError):  # noqa: A001 - deliberately shadows builtin in this namespace
    """An unexpected internal error; retryable but fail-closed / capped."""

    code = "system_error"
    retryable = True
    severity = Severity.critical
    default_user_message = "An unexpected error occurred. This will be retried."


def classify(exc: BaseException) -> RoninError:
    """Coerce an arbitrary exception into a :class:`RoninError` (fail-closed).

    A :class:`RoninError` is returned unchanged. Anything else is wrapped as a
    retryable :class:`SystemError`; the original text is redacted into
    ``detail`` and never exposed as the user message.
    """

    if isinstance(exc, RoninError):
        return exc
    return SystemError(detail=f"{type(exc).__name__}: {exc}")


def is_retryable(exc: BaseException) -> bool:
    """Return whether ``exc`` should be retried, classifying fail-closed.

    Known :class:`RoninError` instances report their own ``retryable`` flag.
    Any other exception is treated as a retryable :class:`SystemError`. Note
    that the worker independently caps attempts for non-``RoninError``
    failures, so "retryable" here never means "retry forever".
    """

    return classify(exc).retryable


__all__ = [
    "PaymentsDisabledError",
    "PermissionError",
    "ProviderError",
    "RateLimitedError",
    "RoninError",
    "Severity",
    "SystemError",
    "TimeoutError",
    "UserError",
    "ValidationError",
    "classify",
    "is_retryable",
    "redact",
]
