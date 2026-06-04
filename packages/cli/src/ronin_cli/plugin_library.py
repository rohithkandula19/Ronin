"""A small library of ready-to-use ronin plugins — `ronin plugin add <name>`.

These are complete, working plugins (no auth needed) you can drop in with one
command and use immediately, or read as templates. The library data and lookup
are pure and unit-tested; each source is valid, loadable Python.
"""
from __future__ import annotations

from dataclasses import dataclass

# NB: sources use only '#' comments (no triple-quoted docstrings) so they nest
# cleanly inside these module strings.

_WEATHER = '''# ronin plugin: weather — current conditions for a city (open-meteo, no API key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def weather(city: str) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1}, timeout=15).json()
    if not g.get("results"):
        return {"error": f"city not found: {city}"}
    loc = g["results"][0]
    f = httpx.get("https://api.open-meteo.com/v1/forecast",
                  params={"latitude": loc["latitude"], "longitude": loc["longitude"],
                          "current": "temperature_2m,relative_humidity_2m,wind_speed_10m"},
                  timeout=15).json()
    cur = f.get("current", {})
    return {"city": loc["name"], "country": loc.get("country"),
            "temp_c": cur.get("temperature_2m"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m")}


def register_tools():
    return [Tool(
        name="weather",
        description="Current weather for a city (temperature, humidity, wind). No API key.",
        input_schema={"type": "object", "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Austin'."}},
            "required": ["city"]},
        handler=weather,
    )]
'''

_GITHUB_TRENDING = '''# ronin plugin: github_trending — recently popular repos (GitHub search, no auth)
from __future__ import annotations

import datetime

import httpx
from ronin_agent_patterns import Tool


def github_trending(language: str = "", days: int = 7, limit: int = 10) -> dict:
    since = (datetime.date.today() - datetime.timedelta(days=max(1, int(days or 7)))).isoformat()
    q = f"created:>{since}"
    if language:
        q += f" language:{language}"
    r = httpx.get("https://api.github.com/search/repositories",
                  params={"q": q, "sort": "stars", "order": "desc",
                          "per_page": min(int(limit or 10), 20)},
                  headers={"Accept": "application/vnd.github+json"}, timeout=20).json()
    items = (r.get("items") or [])[: int(limit or 10)]
    return {"repos": [{"full_name": i["full_name"], "stars": i["stargazers_count"],
                       "url": i["html_url"], "desc": i.get("description")} for i in items]}


def register_tools():
    return [Tool(
        name="github_trending",
        description="Recently created, fast-rising GitHub repos (optionally by language). No auth (rate-limited).",
        input_schema={"type": "object", "properties": {
            "language": {"type": "string", "description": "Filter by language, e.g. 'python'."},
            "days": {"type": "integer", "description": "Look back this many days (default 7)."},
            "limit": {"type": "integer", "description": "How many repos (max 20)."}}},
        handler=github_trending,
    )]
'''

_SCRATCHPAD = '''# ronin plugin: scratchpad — a persistent project notes file (no network)
from __future__ import annotations

from pathlib import Path

from ronin_agent_patterns import Tool

_NOTES = Path(".ronin") / "scratchpad.md"


def scratchpad(action: str = "list", text: str = "") -> dict:
    _NOTES.parent.mkdir(parents=True, exist_ok=True)
    if action == "add" and text:
        with _NOTES.open("a", encoding="utf-8") as fh:
            fh.write(f"- {text}\\n")
        return {"ok": True, "added": text}
    if action == "clear":
        _NOTES.write_text("", encoding="utf-8")
        return {"ok": True, "cleared": True}
    body = _NOTES.read_text(encoding="utf-8") if _NOTES.exists() else ""
    return {"notes": [ln[2:] for ln in body.splitlines() if ln.startswith("- ")]}


def register_tools():
    return [Tool(
        name="scratchpad",
        description="A persistent notes scratchpad for this project. action: list | add | clear.",
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "add", "clear"]},
            "text": {"type": "string", "description": "Note text (for action=add)."}}},
        handler=scratchpad,
    )]
'''

_HACKERNEWS = '''# ronin plugin: hacker_news_top — current HN top stories (public API, no auth)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool

_BASE = "https://hacker-news.firebaseio.com/v0"


def hacker_news_top(limit: int = 5) -> dict:
    limit = max(1, min(int(limit or 5), 15))
    ids = httpx.get(f"{_BASE}/topstories.json", timeout=15).json()[:limit]
    stories = []
    for sid in ids:
        it = httpx.get(f"{_BASE}/item/{sid}.json", timeout=15).json() or {}
        stories.append({"title": it.get("title"), "url": it.get("url"),
                        "score": it.get("score"), "by": it.get("by")})
    return {"count": len(stories), "stories": stories}


def register_tools():
    return [Tool(
        name="hacker_news_top",
        description="The current top stories on Hacker News (title, url, score).",
        input_schema={"type": "object", "properties": {
            "limit": {"type": "integer", "description": "How many stories (1-15)."}}},
        handler=hacker_news_top,
    )]
'''


@dataclass(frozen=True)
class LibraryPlugin:
    name: str
    blurb: str
    source: str
    needs: str = "—"


LIBRARY: dict[str, LibraryPlugin] = {
    "weather": LibraryPlugin("weather", "Current weather for any city (open-meteo)", _WEATHER),
    "github_trending": LibraryPlugin("github_trending", "Recently-rising GitHub repos", _GITHUB_TRENDING),
    "scratchpad": LibraryPlugin("scratchpad", "A persistent project notes scratchpad", _SCRATCHPAD),
    "hackernews": LibraryPlugin("hackernews", "Top Hacker News stories", _HACKERNEWS),
}

_ALIASES = {"hn": "hackernews", "notes": "scratchpad", "trending": "github_trending"}


def resolve(name: str) -> LibraryPlugin | None:
    """Look up a library plugin by name or alias (case-insensitive). Pure."""
    key = (name or "").strip().lower()
    return LIBRARY.get(_ALIASES.get(key, key))


def library_rows() -> list[tuple[str, str, str]]:
    """(name, needs, blurb) for every library plugin, for display. Pure."""
    return [(p.name, p.needs, p.blurb) for p in LIBRARY.values()]
