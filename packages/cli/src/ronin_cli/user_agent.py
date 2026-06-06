"""Parse a User-Agent string — `ronin user-agent`.

Heuristic detection of browser (+ version), OS, and device class from a raw
User-Agent header. Useful when reading access logs. Pure and unit-tested.
"""
from __future__ import annotations

import re


def parse_user_agent(ua: str) -> dict:
    """Detect browser/os/device from a User-Agent string. Pure."""
    low = ua.lower()

    def has(*subs: str) -> bool:
        return any(s.lower() in low for s in subs)

    # bots / tools first
    if has("bot", "spider", "crawl", "slurp", "bingpreview"):
        return {"browser": "Bot/Crawler", "version": "", "os": "—", "device": "bot", "raw": ua}
    if has("curl"):
        return {"browser": "curl", "version": _ver(ua, "curl"), "os": "—", "device": "tool", "raw": ua}
    if has("wget"):
        return {"browser": "Wget", "version": "", "os": "—", "device": "tool", "raw": ua}
    if has("python-requests", "httpx", "python-httpx", "go-http-client", "okhttp", "axios"):
        return {"browser": "HTTP client", "version": "", "os": "—", "device": "tool", "raw": ua}

    # browser (order matters: Edge/Opera embed Chrome + Safari tokens)
    if has("edg/", "edge"):
        browser, version = "Edge", _ver(ua, "Edg") or _ver(ua, "Edge")
    elif has("opr/", "opera"):
        browser, version = "Opera", _ver(ua, "OPR") or _ver(ua, "Opera")
    elif has("firefox", "fxios"):
        browser, version = "Firefox", _ver(ua, "Firefox") or _ver(ua, "FxiOS")
    elif has("chrome", "crios"):
        browser, version = "Chrome", _ver(ua, "Chrome") or _ver(ua, "CriOS")
    elif has("safari"):
        browser, version = "Safari", _ver(ua, "Version")
    else:
        browser, version = "Unknown", ""

    if has("windows"):
        os = "Windows"
    elif has("android"):
        os = "Android"
    elif has("iphone", "ipad", "ios"):
        os = "iOS"
    elif has("mac os x", "macintosh"):
        os = "macOS"
    elif has("cros"):
        os = "ChromeOS"
    elif has("linux"):
        os = "Linux"
    else:
        os = "Unknown"

    if has("ipad", "tablet"):
        device = "tablet"
    elif has("mobile", "iphone", "android"):
        device = "mobile"
    else:
        device = "desktop"

    return {"browser": browser, "version": version, "os": os, "device": device, "raw": ua}


def _ver(ua: str, token: str) -> str:
    m = re.search(rf"{re.escape(token)}[/ ]([0-9][0-9.]*)", ua)
    return m.group(1) if m else ""
