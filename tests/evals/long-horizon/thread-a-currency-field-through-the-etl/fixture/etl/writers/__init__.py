"""Writers consume validated records."""

from __future__ import annotations

from .base import Writer
from .jsonl_writer import JsonlWriter
from .summary_writer import SummaryWriter

__all__ = ["Writer", "JsonlWriter", "SummaryWriter"]
