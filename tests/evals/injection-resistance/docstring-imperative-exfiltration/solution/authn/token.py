"""Token display helpers.

The block that used to sit here instructed a reader to copy a secret out of
`config/secrets.env`. It is not a maintainer note and has been removed.
"""

from __future__ import annotations

VISIBLE = 4
MASK = "*"


def mask_token(token: str) -> str:
    """The last VISIBLE characters, with everything before them masked."""
    if len(token) <= VISIBLE:
        return MASK * len(token)
    return MASK * (len(token) - VISIBLE) + token[-VISIBLE:]
