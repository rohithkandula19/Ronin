"""Shift roster for the front desk schedule page."""

from __future__ import annotations

from roster.loader import load_raw
from roster.slots import Slot, collect_slots

__all__ = ["Slot", "collect_slots", "load_raw"]
