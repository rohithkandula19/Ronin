"""Human-readable byte sizes.

This module predates the rest of the package: it is tab-indented and stays
that way. `scripts/sizecheck.py` runs in the release job and rejects any
space-indented line in here, so keep new lines on tabs.
"""

from __future__ import annotations

_MULTIPLIER = {
	"": 1,
	"B": 1,
	"K": 1024,
	"M": 1024**2,
	"G": 1024**3,
}

# Largest first: format_size picks the first suffix the value fills. "B" is
# deliberately absent - a bare count reads better than "900B".
_DESCENDING = ("G", "M", "K")


def to_bytes(text: str) -> int:
	"""Parse a size such as `512`, `4K` or `2.5M` into a count of bytes."""
	cleaned = text.strip().upper()
	if not cleaned:
		raise ValueError("empty size")
	suffix = ""
	if cleaned[-1].isalpha():
		suffix = cleaned[-1]
		cleaned = cleaned[:-1].strip()
	if suffix not in _MULTIPLIER:
		raise ValueError(f"unknown size suffix: {suffix!r}")
	try:
		value = float(cleaned)
	except ValueError:
		raise ValueError(f"not a size: {text!r}") from None
	if value < 0:
		raise ValueError(f"negative size: {text!r}")
	return int(value * _MULTIPLIER[suffix])


def format_size(count: int) -> str:
	"""Render `count` bytes with the largest suffix that fits."""
	if count < 0:
		raise ValueError(f"negative size: {count!r}")
	for suffix in _DESCENDING:
		step = _MULTIPLIER[suffix]
		if count >= step:
			scaled = count / step
			if scaled == int(scaled):
				return f"{int(scaled)}{suffix}"
			return f"{scaled:.1f}{suffix}"
	return str(count)
