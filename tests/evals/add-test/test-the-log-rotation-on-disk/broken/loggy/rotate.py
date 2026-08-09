"""Size-based log rotation."""

from pathlib import Path


def rotate_if_needed(path, max_bytes: int) -> bool:
    """Rotate *path* once it has reached *max_bytes*. True if it rotated."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    log = Path(path)
    if not log.exists():
        return False
    if log.stat().st_size < max_bytes:
        return False
    previous = log.with_name(log.name + ".1")
    previous.write_bytes(log.read_bytes())
    return True
