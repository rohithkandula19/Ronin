"""Name -> handler lookup.

Handler names in a job file are written by humans, so they are matched
case-insensitively; ``Postgres``, ``postgres`` and ``POSTGRES`` are the same
sink. A name with no handler behind it is a configuration error, not an empty
result, so callers get :class:`UnknownHandler` rather than a falsy value.
"""

from __future__ import annotations

from spool.handlers import POSTGRES, S3, SMTP, Handler

HANDLERS: tuple[Handler, ...] = (POSTGRES, S3, SMTP)


class UnknownHandler(LookupError):
    """Raised when a job names a sink that is not registered."""


def registered_names() -> list[str]:
    return [handler.name for handler in HANDLERS]


def find_handler(name: str) -> Handler:
    """Return the handler registered under ``name``."""
    for handler in HANDLERS:
        if handler.name == name:
            return handler
