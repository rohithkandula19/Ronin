"""World clock & timezone conversion — `ronin time`.

`ronin time` shows the current time across common zones; `ronin time Asia/Tokyo`
shows one. The conversion/formatting is pure (takes a UTC datetime in), so it's
unit-tested deterministically; the command supplies the real "now".
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

COMMON_ZONES = [
    "UTC", "America/Los_Angeles", "America/New_York", "America/Chicago",
    "America/Sao_Paulo", "Europe/London", "Europe/Berlin", "Europe/Moscow",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo",
    "Australia/Sydney",
]


def format_in(dt_utc: datetime.datetime, tz_name: str) -> dict | None:
    """Format a UTC datetime in the named timezone. None if the zone is unknown.
    Pure given ``dt_utc``."""
    try:
        zi = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
        return None
    local = dt_utc.astimezone(zi)
    off = local.strftime("%z")
    return {
        "zone": tz_name,
        "time": local.strftime("%H:%M"),
        "date": local.strftime("%Y-%m-%d"),
        "day": local.strftime("%A"),
        "offset": f"{off[:3]}:{off[3:]}" if off else "",
        "abbrev": local.tzname() or "",
    }


def world_clock(dt_utc: datetime.datetime, zones: list[str] | None = None) -> list[dict]:
    """Format ``dt_utc`` across several zones (skips unknown ones). Pure."""
    out = []
    for z in (zones or COMMON_ZONES):
        info = format_in(dt_utc, z)
        if info:
            out.append(info)
    return out
