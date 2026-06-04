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
                  params={"name": city, "count": 1}, timeout=15, follow_redirects=True).json()
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
                  headers={"Accept": "application/vnd.github+json"}, timeout=20, follow_redirects=True).json()
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
    ids = httpx.get(f"{_BASE}/topstories.json", timeout=15, follow_redirects=True).json()[:limit]
    stories = []
    for sid in ids:
        it = httpx.get(f"{_BASE}/item/{sid}.json", timeout=15, follow_redirects=True).json() or {}
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


_CURRENCY = '''# ronin plugin: currency — convert between currencies (frankfurter.app, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def currency(amount: float = 1, base: str = "USD", target: str = "EUR") -> dict:
    base, target = base.upper(), target.upper()
    r = httpx.get("https://api.frankfurter.app/latest",
                  params={"amount": amount, "from": base, "to": target}, timeout=15, follow_redirects=True).json()
    rate = (r.get("rates") or {}).get(target)
    if rate is None:
        return {"error": f"could not convert {base}->{target}"}
    return {"amount": amount, "base": base, "target": target,
            "converted": rate, "date": r.get("date")}


def register_tools():
    return [Tool(
        name="currency",
        description="Convert an amount between currencies (live ECB rates, no key).",
        input_schema={"type": "object", "properties": {
            "amount": {"type": "number"}, "base": {"type": "string", "description": "e.g. USD"},
            "target": {"type": "string", "description": "e.g. EUR"}}},
        handler=currency,
    )]
'''

_CRYPTO = '''# ronin plugin: crypto_price — spot price of a coin (CoinGecko, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def crypto_price(coin: str = "bitcoin", vs: str = "usd") -> dict:
    coin, vs = coin.lower(), vs.lower()
    r = httpx.get("https://api.coingecko.com/api/v3/simple/price",
                  params={"ids": coin, "vs_currencies": vs,
                          "include_24hr_change": "true"}, timeout=15, follow_redirects=True).json()
    data = r.get(coin)
    if not data:
        return {"error": f"unknown coin '{coin}' (use the CoinGecko id, e.g. 'ethereum')"}
    return {"coin": coin, "vs": vs, "price": data.get(vs),
            "change_24h_pct": data.get(f"{vs}_24h_change")}


def register_tools():
    return [Tool(
        name="crypto_price",
        description="Current price (and 24h change) of a cryptocurrency by CoinGecko id. No key.",
        input_schema={"type": "object", "properties": {
            "coin": {"type": "string", "description": "CoinGecko id, e.g. 'bitcoin'."},
            "vs": {"type": "string", "description": "Fiat, e.g. 'usd'."}}},
        handler=crypto_price,
    )]
'''

_DEFINE = '''# ronin plugin: define — dictionary definitions (dictionaryapi.dev, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def define(word: str) -> dict:
    r = httpx.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"word": word, "error": "no definition found"}
    entries = r.json()
    defs = []
    for entry in entries[:1]:
        for meaning in entry.get("meanings", []):
            pos = meaning.get("partOfSpeech", "")
            for d in meaning.get("definitions", [])[:2]:
                defs.append({"part_of_speech": pos, "definition": d.get("definition")})
    return {"word": word, "definitions": defs[:6]}


def register_tools():
    return [Tool(
        name="define",
        description="Dictionary definition(s) of an English word. No key.",
        input_schema={"type": "object", "properties": {
            "word": {"type": "string"}}, "required": ["word"]},
        handler=define,
    )]
'''

_WIKIPEDIA = '''# ronin plugin: wikipedia — a topic summary (Wikipedia REST API, no key)
from __future__ import annotations

import urllib.parse

import httpx
from ronin_agent_patterns import Tool


def wikipedia(query: str) -> dict:
    title = urllib.parse.quote(query.strip().replace(" ", "_"))
    r = httpx.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                  headers={"accept": "application/json", "user-agent": "ronin-plugin/1.0"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"query": query, "error": "no article found"}
    d = r.json()
    return {"title": d.get("title"), "summary": d.get("extract"),
            "url": (d.get("content_urls", {}).get("desktop", {}) or {}).get("page")}


def register_tools():
    return [Tool(
        name="wikipedia",
        description="A plain-language summary of a topic from Wikipedia. No key.",
        input_schema={"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
        handler=wikipedia,
    )]
'''

_IPINFO = '''# ronin plugin: ip_info — geolocate an IP (ip-api.com, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def ip_info(ip: str = "") -> dict:
    r = httpx.get(f"http://ip-api.com/json/{ip.strip()}", timeout=15, follow_redirects=True).json()
    if r.get("status") != "success":
        return {"ip": ip, "error": r.get("message", "lookup failed")}
    return {"ip": r.get("query"), "city": r.get("city"), "region": r.get("regionName"),
            "country": r.get("country"), "isp": r.get("isp"),
            "lat": r.get("lat"), "lon": r.get("lon"), "timezone": r.get("timezone")}


def register_tools():
    return [Tool(
        name="ip_info",
        description="Geolocate an IP address (city/country/ISP). Empty = your own IP. No key.",
        input_schema={"type": "object", "properties": {
            "ip": {"type": "string", "description": "IP to look up; blank for your own."}}},
        handler=ip_info,
    )]
'''


_HOLIDAYS = '''# ronin plugin: public_holidays — official holidays by country (nager.date, no key)
from __future__ import annotations

import datetime

import httpx
from ronin_agent_patterns import Tool


def public_holidays(country: str = "US", year: int = 0) -> dict:
    year = int(year) or datetime.date.today().year
    r = httpx.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country.upper()}",
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"no holiday data for country '{country}'"}
    return {"country": country.upper(), "year": year,
            "holidays": [{"date": h["date"], "name": h["localName"], "english": h["name"]}
                         for h in r.json()]}


def register_tools():
    return [Tool(
        name="public_holidays",
        description="Official public holidays for a country/year (ISO country code). No key.",
        input_schema={"type": "object", "properties": {
            "country": {"type": "string", "description": "ISO-2 code, e.g. 'US', 'IN', 'GB'."},
            "year": {"type": "integer", "description": "Year (default: current)."}}},
        handler=public_holidays,
    )]
'''

_SHORTEN = '''# ronin plugin: shorten_url — shorten a link (is.gd, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def shorten_url(url: str) -> dict:
    resp = httpx.get("https://is.gd/create.php", params={"format": "json", "url": url},
                     timeout=15, follow_redirects=True)
    try:
        r = resp.json()
    except ValueError:
        return {"error": "shortener unavailable (rate-limited?)", "url": url}
    if r.get("errormessage"):
        return {"error": r["errormessage"]}
    return {"url": url, "short": r.get("shorturl")}


def register_tools():
    return [Tool(
        name="shorten_url",
        description="Shorten a long URL into an is.gd link. No key.",
        input_schema={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        handler=shorten_url,
    )]
'''

_QRCODE = '''# ronin plugin: qr_code — build a QR-code image URL (qrserver, no key)
from __future__ import annotations

import urllib.parse

from ronin_agent_patterns import Tool


def qr_code(text: str, size: int = 220) -> dict:
    size = max(80, min(int(size or 220), 1000))
    data = urllib.parse.quote(text)
    return {"text": text,
            "image_url": f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={data}"}


def register_tools():
    return [Tool(
        name="qr_code",
        description="Generate a QR-code image URL encoding any text or link. No key.",
        input_schema={"type": "object", "properties": {
            "text": {"type": "string"}, "size": {"type": "integer", "description": "px (80-1000)."}},
            "required": ["text"]},
        handler=qr_code,
    )]
'''

_TRANSLATE = '''# ronin plugin: translate — machine translation (MyMemory, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def translate(text: str, to: str = "es", source: str = "en") -> dict:
    r = httpx.get("https://api.mymemory.translated.net/get",
                  params={"q": text, "langpair": f"{source}|{to}"},
                  timeout=15, follow_redirects=True).json()
    return {"text": text, "from": source, "to": to,
            "translated": (r.get("responseData") or {}).get("translatedText")}


def register_tools():
    return [Tool(
        name="translate",
        description="Translate text between languages (ISO codes, e.g. en->es). No key.",
        input_schema={"type": "object", "properties": {
            "text": {"type": "string"},
            "to": {"type": "string", "description": "Target lang code, e.g. 'fr'."},
            "source": {"type": "string", "description": "Source lang code (default 'en')."}},
            "required": ["text"]},
        handler=translate,
    )]
'''

_NPM = '''# ronin plugin: npm_package — npm package info (registry.npmjs.org, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def npm_package(name: str) -> dict:
    r = httpx.get(f"https://registry.npmjs.org/{name}/latest", timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"npm package not found: {name}"}
    d = r.json()
    return {"name": d.get("name"), "version": d.get("version"),
            "description": d.get("description"), "homepage": d.get("homepage"),
            "license": d.get("license")}


def register_tools():
    return [Tool(
        name="npm_package",
        description="Look up an npm package: latest version, description, license. No key.",
        input_schema={"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]},
        handler=npm_package,
    )]
'''

_PYPI = '''# ronin plugin: pypi_package — PyPI package info (pypi.org, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def pypi_package(name: str) -> dict:
    r = httpx.get(f"https://pypi.org/pypi/{name}/json", timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"PyPI package not found: {name}"}
    info = r.json().get("info", {})
    return {"name": info.get("name"), "version": info.get("version"),
            "summary": info.get("summary"), "home_page": info.get("home_page"),
            "license": info.get("license")}


def register_tools():
    return [Tool(
        name="pypi_package",
        description="Look up a PyPI package: latest version, summary, license. No key.",
        input_schema={"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]},
        handler=pypi_package,
    )]
'''

_STOCK = '''# ronin plugin: stock_price — latest quote for a ticker (Yahoo, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def stock_price(symbol: str) -> dict:
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                  headers={"user-agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"no data for symbol '{symbol}'"}
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return {"error": f"unknown symbol '{symbol}'"}
    m = res[0].get("meta", {})
    return {"symbol": m.get("symbol"), "price": m.get("regularMarketPrice"),
            "previous_close": m.get("previousClose"), "currency": m.get("currency"),
            "exchange": m.get("exchangeName")}


def register_tools():
    return [Tool(
        name="stock_price",
        description="Latest market price for a stock ticker (e.g. AAPL). No key.",
        input_schema={"type": "object", "properties": {
            "symbol": {"type": "string"}}, "required": ["symbol"]},
        handler=stock_price,
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
    "currency": LibraryPlugin("currency", "Convert between currencies (live rates)", _CURRENCY),
    "crypto_price": LibraryPlugin("crypto_price", "Spot crypto price + 24h change", _CRYPTO),
    "define": LibraryPlugin("define", "Dictionary definitions of a word", _DEFINE),
    "wikipedia": LibraryPlugin("wikipedia", "A topic summary from Wikipedia", _WIKIPEDIA),
    "ip_info": LibraryPlugin("ip_info", "Geolocate an IP address", _IPINFO),
    "public_holidays": LibraryPlugin("public_holidays", "Official holidays by country/year", _HOLIDAYS),
    "shorten_url": LibraryPlugin("shorten_url", "Shorten a link (is.gd)", _SHORTEN),
    "qr_code": LibraryPlugin("qr_code", "Make a QR-code image for any text", _QRCODE),
    "translate": LibraryPlugin("translate", "Translate text between languages", _TRANSLATE),
    "npm_package": LibraryPlugin("npm_package", "npm package info (version, license)", _NPM),
    "pypi_package": LibraryPlugin("pypi_package", "PyPI package info (version, summary)", _PYPI),
    "stock_price": LibraryPlugin("stock_price", "Latest quote for a stock ticker", _STOCK),
}

_ALIASES = {
    "hn": "hackernews", "notes": "scratchpad", "trending": "github_trending",
    "fx": "currency", "forex": "currency", "exchange": "currency",
    "crypto": "crypto_price", "btc": "crypto_price",
    "dictionary": "define", "definition": "define",
    "wiki": "wikipedia", "ip": "ip_info", "geoip": "ip_info",
    "holidays": "public_holidays", "shorten": "shorten_url", "qr": "qr_code",
    "npm": "npm_package", "pypi": "pypi_package", "pip": "pypi_package",
    "stock": "stock_price", "stocks": "stock_price", "ticker": "stock_price",
}


def resolve(name: str) -> LibraryPlugin | None:
    """Look up a library plugin by name or alias (case-insensitive). Pure."""
    key = (name or "").strip().lower()
    return LIBRARY.get(_ALIASES.get(key, key))


def library_rows() -> list[tuple[str, str, str]]:
    """(name, needs, blurb) for every library plugin, for display. Pure."""
    return [(p.name, p.needs, p.blurb) for p in LIBRARY.values()]
