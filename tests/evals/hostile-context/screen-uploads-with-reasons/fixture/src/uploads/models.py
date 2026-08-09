"""Records shared by the upload pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Upload:
    """One file offered to the intake endpoint.

    ``filename`` is whatever the client sent, so it may contain directory
    components, trailing whitespace or a temp-file suffix.
    """

    filename: str
    size_bytes: int
