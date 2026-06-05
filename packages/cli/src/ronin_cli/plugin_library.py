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


_COUNTRY = '''# ronin plugin: country_info — facts about a country (restcountries, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def country_info(name: str) -> dict:
    r = httpx.get(f"https://restcountries.com/v3.1/name/{name}",
                  params={"fields": "name,capital,population,region,subregion,currencies,languages,flag"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"country not found: {name}"}
    c = r.json()[0]
    return {"name": c.get("name", {}).get("common"), "capital": (c.get("capital") or [None])[0],
            "region": c.get("region"), "subregion": c.get("subregion"),
            "population": c.get("population"), "flag": c.get("flag"),
            "currencies": list((c.get("currencies") or {}).keys()),
            "languages": list((c.get("languages") or {}).values())}


def register_tools():
    return [Tool(
        name="country_info",
        description="Capital, population, region, currencies and languages of a country. No key.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        handler=country_info,
    )]
'''

_DADJOKE = '''# ronin plugin: dad_joke — a random dad joke (icanhazdadjoke, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def dad_joke() -> dict:
    r = httpx.get("https://icanhazdadjoke.com/",
                  headers={"Accept": "application/json", "User-Agent": "ronin (https://github.com/ronin)"},
                  timeout=15, follow_redirects=True)
    return {"joke": r.json().get("joke")}


def register_tools():
    return [Tool(
        name="dad_joke",
        description="Fetch a random dad joke. No key.",
        input_schema={"type": "object", "properties": {}},
        handler=dad_joke,
    )]
'''

_SUNRISE = '''# ronin plugin: sun_times — sunrise/sunset for a city (open-meteo + sunrise-sunset.org)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def sun_times(city: str) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1}, timeout=15, follow_redirects=True).json()
    if not g.get("results"):
        return {"error": f"city not found: {city}"}
    loc = g["results"][0]
    s = httpx.get("https://api.sunrise-sunset.org/json",
                  params={"lat": loc["latitude"], "lng": loc["longitude"], "formatted": 0},
                  timeout=15, follow_redirects=True).json()
    res = s.get("results", {})
    return {"city": loc["name"], "country": loc.get("country"),
            "sunrise_utc": res.get("sunrise"), "sunset_utc": res.get("sunset"),
            "day_length_s": res.get("day_length")}


def register_tools():
    return [Tool(
        name="sun_times",
        description="Sunrise and sunset times (UTC) for a city. No key.",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        handler=sun_times,
    )]
'''

_COLOR = '''# ronin plugin: color_info — name & values of a color (thecolorapi, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def color_info(hex: str) -> dict:
    h = hex.lstrip("#")
    r = httpx.get("https://www.thecolorapi.com/id", params={"hex": h},
                  timeout=15, follow_redirects=True).json()
    return {"hex": f"#{h}", "name": r.get("name", {}).get("value"),
            "rgb": r.get("rgb", {}).get("value"), "hsl": r.get("hsl", {}).get("value")}


def register_tools():
    return [Tool(
        name="color_info",
        description="Given a hex color, return its closest name plus RGB/HSL. No key.",
        input_schema={"type": "object", "properties": {
            "hex": {"type": "string", "description": "e.g. 'ff5733' or '#ff5733'."}}, "required": ["hex"]},
        handler=color_info,
    )]
'''

_UNITCONV = '''# ronin plugin: unit_convert — convert length/mass/temperature (offline, no network)
from __future__ import annotations

from ronin_agent_patterns import Tool

_LENGTH = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mi": 1609.344,
           "ft": 0.3048, "in": 0.0254, "yd": 0.9144, "nmi": 1852}
_MASS = {"kg": 1000, "g": 1, "mg": 0.001, "lb": 453.59237, "oz": 28.349523, "t": 1_000_000}
_TEMP = {"c", "f", "k"}


def unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    f, t = from_unit.lower(), to_unit.lower()
    value = float(value)
    if f in _TEMP and t in _TEMP:
        c = value if f == "c" else (value - 32) * 5 / 9 if f == "f" else value - 273.15
        out = c if t == "c" else c * 9 / 5 + 32 if t == "f" else c + 273.15
        return {"value": value, "from": f, "to": t, "result": round(out, 4)}
    for table in (_LENGTH, _MASS):
        if f in table and t in table:
            return {"value": value, "from": f, "to": t,
                    "result": round(value * table[f] / table[t], 6)}
    return {"error": f"cannot convert '{from_unit}' to '{to_unit}' "
                     "(supported: length m/km/cm/mm/mi/ft/in/yd/nmi, mass kg/g/mg/lb/oz/t, temp c/f/k)"}


def register_tools():
    return [Tool(
        name="unit_convert",
        description="Convert between units of length, mass, or temperature. Works offline.",
        input_schema={"type": "object", "properties": {
            "value": {"type": "number"}, "from_unit": {"type": "string"},
            "to_unit": {"type": "string"}}, "required": ["value", "from_unit", "to_unit"]},
        handler=unit_convert,
    )]
'''

_RANDOMUSER = '''# ronin plugin: random_user — a fake user profile for testing (randomuser.me, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def random_user(nationality: str = "") -> dict:
    params = {"nat": nationality} if nationality else {}
    r = httpx.get("https://randomuser.me/api/", params=params, timeout=15, follow_redirects=True).json()
    u = (r.get("results") or [{}])[0]
    name = u.get("name", {})
    loc = u.get("location", {})
    return {"name": f"{name.get('first', '')} {name.get('last', '')}".strip(),
            "email": u.get("email"), "phone": u.get("phone"),
            "country": loc.get("country"), "city": loc.get("city"),
            "username": u.get("login", {}).get("username")}


def register_tools():
    return [Tool(
        name="random_user",
        description="Generate a realistic fake user profile (for test data/seeding). No key.",
        input_schema={"type": "object", "properties": {
            "nationality": {"type": "string", "description": "Optional 2-letter nat, e.g. 'us', 'gb'."}}},
        handler=random_user,
    )]
'''

_ISS = '''# ronin plugin: iss_location — where the ISS is right now (wheretheiss.at, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def iss_location() -> dict:
    r = httpx.get("https://api.wheretheiss.at/v1/satellites/25544",
                  timeout=15, follow_redirects=True).json()
    return {"latitude": r.get("latitude"), "longitude": r.get("longitude"),
            "altitude_km": r.get("altitude"), "velocity_kmh": r.get("velocity")}


def register_tools():
    return [Tool(
        name="iss_location",
        description="Current latitude/longitude, altitude and speed of the ISS. No key.",
        input_schema={"type": "object", "properties": {}},
        handler=iss_location,
    )]
'''



_AIRQUALITY = '''# ronin plugin: air_quality — AQI for a city (open-meteo air quality, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def air_quality(city: str) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1}, timeout=15, follow_redirects=True).json()
    if not g.get("results"):
        return {"error": f"city not found: {city}"}
    loc = g["results"][0]
    a = httpx.get("https://air-quality-api.open-meteo.com/v1/air-quality",
                  params={"latitude": loc["latitude"], "longitude": loc["longitude"],
                          "current": "us_aqi,pm2_5,pm10,ozone"},
                  timeout=15, follow_redirects=True).json()
    cur = a.get("current", {})
    return {"city": loc["name"], "country": loc.get("country"),
            "us_aqi": cur.get("us_aqi"), "pm2_5": cur.get("pm2_5"),
            "pm10": cur.get("pm10"), "ozone": cur.get("ozone")}


def register_tools():
    return [Tool(
        name="air_quality",
        description="Current air quality (US AQI, PM2.5/PM10, ozone) for a city. No key.",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        handler=air_quality,
    )]
'''

_GHUSER = '''# ronin plugin: github_user — public profile stats (GitHub API, no auth)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def github_user(username: str) -> dict:
    r = httpx.get(f"https://api.github.com/users/{username}",
                  headers={"Accept": "application/vnd.github+json"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"user not found: {username}"}
    d = r.json()
    return {"login": d.get("login"), "name": d.get("name"), "bio": d.get("bio"),
            "company": d.get("company"), "location": d.get("location"),
            "public_repos": d.get("public_repos"), "followers": d.get("followers"),
            "following": d.get("following"), "url": d.get("html_url")}


def register_tools():
    return [Tool(
        name="github_user",
        description="Public GitHub profile: name, bio, repo count, followers. No auth (rate-limited).",
        input_schema={"type": "object", "properties": {"username": {"type": "string"}}, "required": ["username"]},
        handler=github_user,
    )]
'''

_RHYMES = '''# ronin plugin: rhymes — words that rhyme with a word (Datamuse, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def rhymes(word: str, limit: int = 15) -> dict:
    r = httpx.get("https://api.datamuse.com/words",
                  params={"rel_rhy": word, "max": min(int(limit or 15), 50)},
                  timeout=15, follow_redirects=True).json()
    return {"word": word, "rhymes": [w["word"] for w in r]}


def register_tools():
    return [Tool(
        name="rhymes",
        description="Words that rhyme with a given word. No key.",
        input_schema={"type": "object", "properties": {
            "word": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["word"]},
        handler=rhymes,
    )]
'''

_SYNONYMS = '''# ronin plugin: synonyms — synonyms for a word (Datamuse, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def synonyms(word: str, limit: int = 15) -> dict:
    r = httpx.get("https://api.datamuse.com/words",
                  params={"rel_syn": word, "max": min(int(limit or 15), 50)},
                  timeout=15, follow_redirects=True).json()
    return {"word": word, "synonyms": [w["word"] for w in r]}


def register_tools():
    return [Tool(
        name="synonyms",
        description="Synonyms for a given word. No key.",
        input_schema={"type": "object", "properties": {
            "word": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["word"]},
        handler=synonyms,
    )]
'''

_POKEMON = '''# ronin plugin: pokemon — stats for a Pokemon (PokeAPI, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def pokemon(name: str) -> dict:
    r = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{name.lower().strip()}",
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"no Pokemon named '{name}'"}
    d = r.json()
    return {"name": d.get("name"), "id": d.get("id"),
            "types": [t["type"]["name"] for t in d.get("types", [])],
            "height_dm": d.get("height"), "weight_hg": d.get("weight"),
            "abilities": [a["ability"]["name"] for a in d.get("abilities", [])]}


def register_tools():
    return [Tool(
        name="pokemon",
        description="Look up a Pokemon's types, abilities, height and weight. No key.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        handler=pokemon,
    )]
'''

_ADVICE = '''# ronin plugin: advice — a random piece of advice (adviceslip, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def advice() -> dict:
    resp = httpx.get("https://api.adviceslip.com/advice", timeout=15, follow_redirects=True)
    try:
        d = resp.json()
    except ValueError:
        return {"error": "advice service unavailable"}
    return {"advice": (d.get("slip") or {}).get("advice")}


def register_tools():
    return [Tool(
        name="advice",
        description="Fetch a random piece of advice. No key.",
        input_schema={"type": "object", "properties": {}},
        handler=advice,
    )]
'''


_GHREPO = '''# ronin plugin: github_repo — stars/forks/language for a repo (GitHub API, no auth)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def github_repo(repo: str) -> dict:
    r = httpx.get(f"https://api.github.com/repos/{repo.strip().strip('/')}",
                  headers={"Accept": "application/vnd.github+json"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"repo not found: {repo} (use 'owner/name')"}
    d = r.json()
    return {"full_name": d.get("full_name"), "description": d.get("description"),
            "stars": d.get("stargazers_count"), "forks": d.get("forks_count"),
            "open_issues": d.get("open_issues_count"), "language": d.get("language"),
            "license": (d.get("license") or {}).get("name"), "url": d.get("html_url")}


def register_tools():
    return [Tool(
        name="github_repo",
        description="Stats for a GitHub repo (stars, forks, language, issues). Pass 'owner/name'. No auth.",
        input_schema={"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
        handler=github_repo,
    )]
'''

_TRIVIA = '''# ronin plugin: trivia — a random trivia question (Open Trivia DB, no key)
from __future__ import annotations

import html

import httpx
from ronin_agent_patterns import Tool


def trivia(category: str = "") -> dict:
    r = httpx.get("https://opentdb.com/api.php",
                  params={"amount": 1, "type": "multiple"},
                  timeout=15, follow_redirects=True).json()
    res = (r.get("results") or [{}])[0]
    opts = [html.unescape(a) for a in res.get("incorrect_answers", [])] + \\
           [html.unescape(res.get("correct_answer", ""))]
    return {"category": res.get("category"), "difficulty": res.get("difficulty"),
            "question": html.unescape(res.get("question", "")),
            "options": sorted(opts), "answer": html.unescape(res.get("correct_answer", ""))}


def register_tools():
    return [Tool(
        name="trivia",
        description="A random multiple-choice trivia question with its answer. No key.",
        input_schema={"type": "object", "properties": {"category": {"type": "string"}}},
        handler=trivia,
    )]
'''

_PROGJOKE = '''# ronin plugin: programming_joke — a programming joke (JokeAPI, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def programming_joke() -> dict:
    d = httpx.get("https://v2.jokeapi.dev/joke/Programming",
                  params={"safe-mode": ""}, timeout=15, follow_redirects=True).json()
    if d.get("type") == "single":
        return {"joke": d.get("joke")}
    return {"setup": d.get("setup"), "punchline": d.get("delivery")}


def register_tools():
    return [Tool(
        name="programming_joke",
        description="Fetch a programming joke. No key.",
        input_schema={"type": "object", "properties": {}},
        handler=programming_joke,
    )]
'''

_RECIPE = '''# ronin plugin: recipe — find a recipe by name (TheMealDB, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def recipe(query: str) -> dict:
    r = httpx.get("https://www.themealdb.com/api/json/v1/1/search.php",
                  params={"s": query}, timeout=15, follow_redirects=True).json()
    meals = r.get("meals") or []
    if not meals:
        return {"error": f"no recipe found for '{query}'"}
    m = meals[0]
    ingredients = []
    for i in range(1, 21):
        ing, meas = m.get(f"strIngredient{i}"), m.get(f"strMeasure{i}")
        if ing and ing.strip():
            ingredients.append(f"{(meas or '').strip()} {ing.strip()}".strip())
    return {"name": m.get("strMeal"), "category": m.get("strCategory"),
            "area": m.get("strArea"), "ingredients": ingredients,
            "instructions": (m.get("strInstructions") or "")[:600]}


def register_tools():
    return [Tool(
        name="recipe",
        description="Find a recipe by dish name: ingredients + instructions. No key.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=recipe,
    )]
'''

_COCKTAIL = '''# ronin plugin: cocktail — a cocktail recipe by name (TheCocktailDB, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def cocktail(name: str) -> dict:
    r = httpx.get("https://www.thecocktaildb.com/api/json/v1/1/search.php",
                  params={"s": name}, timeout=15, follow_redirects=True).json()
    drinks = r.get("drinks") or []
    if not drinks:
        return {"error": f"no cocktail found for '{name}'"}
    d = drinks[0]
    ingredients = []
    for i in range(1, 16):
        ing, meas = d.get(f"strIngredient{i}"), d.get(f"strMeasure{i}")
        if ing and ing.strip():
            ingredients.append(f"{(meas or '').strip()} {ing.strip()}".strip())
    return {"name": d.get("strDrink"), "glass": d.get("strGlass"),
            "ingredients": ingredients, "instructions": d.get("strInstructions")}


def register_tools():
    return [Tool(
        name="cocktail",
        description="Find a cocktail recipe by name: ingredients + instructions. No key.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        handler=cocktail,
    )]
'''

_ANIME = '''# ronin plugin: anime — look up an anime (Jikan / MyAnimeList, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def anime(query: str) -> dict:
    r = httpx.get("https://api.jikan.moe/v4/anime",
                  params={"q": query, "limit": 1}, timeout=15, follow_redirects=True).json()
    data = r.get("data") or []
    if not data:
        return {"error": f"no anime found for '{query}'"}
    a = data[0]
    return {"title": a.get("title"), "type": a.get("type"), "episodes": a.get("episodes"),
            "score": a.get("score"), "year": a.get("year"), "status": a.get("status"),
            "synopsis": (a.get("synopsis") or "")[:400]}


def register_tools():
    return [Tool(
        name="anime",
        description="Look up an anime: score, episodes, year, synopsis. No key.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=anime,
    )]
'''

_QUOTE = '''# ronin plugin: quote — a random inspirational quote (ZenQuotes, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def quote() -> dict:
    d = httpx.get("https://zenquotes.io/api/random", timeout=15, follow_redirects=True).json()
    item = (d or [{}])[0]
    return {"quote": item.get("q"), "author": item.get("a")}


def register_tools():
    return [Tool(
        name="quote",
        description="A random inspirational quote with its author. No key.",
        input_schema={"type": "object", "properties": {}},
        handler=quote,
    )]
'''


_UUIDGEN = '''# ronin plugin: uuid_gen — generate UUIDs (offline)
from __future__ import annotations

import uuid

from ronin_agent_patterns import Tool


def uuid_gen(count: int = 1) -> dict:
    n = max(1, min(int(count or 1), 50))
    return {"uuids": [str(uuid.uuid4()) for _ in range(n)]}


def register_tools():
    return [Tool(name="uuid_gen", description="Generate one or more random UUID4s. Offline.",
                 input_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
                 handler=uuid_gen)]
'''

_PASSWORD = '''# ronin plugin: password_gen — a strong random password (offline)
from __future__ import annotations

import secrets
import string

from ronin_agent_patterns import Tool


def password_gen(length: int = 16, symbols: bool = True) -> dict:
    length = max(6, min(int(length or 16), 128))
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*-_=+" if symbols else "")
    pw = "".join(secrets.choice(alphabet) for _ in range(length))
    return {"password": pw, "length": length}


def register_tools():
    return [Tool(name="password_gen", description="Generate a strong random password. Offline.",
                 input_schema={"type": "object", "properties": {
                     "length": {"type": "integer"}, "symbols": {"type": "boolean"}}},
                 handler=password_gen)]
'''

_BASE64 = '''# ronin plugin: base64_tool — encode/decode base64 (offline)
from __future__ import annotations

import base64

from ronin_agent_patterns import Tool


def base64_tool(text: str, mode: str = "encode") -> dict:
    try:
        if mode == "decode":
            return {"mode": "decode", "result": base64.b64decode(text).decode("utf-8", "replace")}
        return {"mode": "encode", "result": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def register_tools():
    return [Tool(name="base64_tool", description="Base64 encode or decode text. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"]}},
                     "required": ["text"]},
                 handler=base64_tool)]
'''

_HASH = '''# ronin plugin: hash_text — hash text (md5/sha1/sha256, offline)
from __future__ import annotations

import hashlib

from ronin_agent_patterns import Tool


def hash_text(text: str, algo: str = "sha256") -> dict:
    algo = algo.lower()
    if algo not in ("md5", "sha1", "sha256", "sha512"):
        return {"error": f"unsupported algo '{algo}'"}
    h = hashlib.new(algo, text.encode("utf-8")).hexdigest()
    return {"algo": algo, "hash": h}


def register_tools():
    return [Tool(name="hash_text", description="Hash text with md5/sha1/sha256/sha512. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "algo": {"type": "string"}}, "required": ["text"]},
                 handler=hash_text)]
'''

_LOREM = '''# ronin plugin: lorem_ipsum — placeholder text (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_P = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
      "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
      "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.")


def lorem_ipsum(paragraphs: int = 1) -> dict:
    n = max(1, min(int(paragraphs or 1), 10))
    return {"text": "\\n\\n".join([_P] * n), "paragraphs": n}


def register_tools():
    return [Tool(name="lorem_ipsum", description="Generate placeholder lorem-ipsum text. Offline.",
                 input_schema={"type": "object", "properties": {"paragraphs": {"type": "integer"}}},
                 handler=lorem_ipsum)]
'''

_JSONFMT = '''# ronin plugin: json_format — validate & pretty-print JSON (offline)
from __future__ import annotations

import json

from ronin_agent_patterns import Tool


def json_format(text: str, indent: int = 2) -> dict:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {"valid": False, "error": f"{e.msg} at line {e.lineno} col {e.colno}"}
    return {"valid": True, "formatted": json.dumps(obj, indent=int(indent or 2), ensure_ascii=False)}


def register_tools():
    return [Tool(name="json_format", description="Validate and pretty-print a JSON string. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "indent": {"type": "integer"}}, "required": ["text"]},
                 handler=json_format)]
'''

_TIMESTAMP = '''# ronin plugin: timestamp — convert unix <-> ISO time (offline)
from __future__ import annotations

import datetime

from ronin_agent_patterns import Tool


def timestamp(value: str = "") -> dict:
    v = str(value).strip()
    if not v:
        now = datetime.datetime.now(datetime.timezone.utc)
        return {"unix": int(now.timestamp()), "iso_utc": now.isoformat()}
    try:
        if v.lstrip("-").isdigit():
            dt = datetime.datetime.fromtimestamp(int(v), datetime.timezone.utc)
            return {"unix": int(v), "iso_utc": dt.isoformat()}
        dt = datetime.datetime.fromisoformat(v)
        return {"iso": v, "unix": int(dt.timestamp())}
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not parse '{value}': {e}"}


def register_tools():
    return [Tool(name="timestamp", description="Convert between unix epoch and ISO time (blank = now). Offline.",
                 input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
                 handler=timestamp)]
'''

_URLCHECK = '''# ronin plugin: url_check — HTTP status & timing of a URL
from __future__ import annotations

import time

import httpx
from ronin_agent_patterns import Tool


def url_check(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    t0 = time.time()
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)}
    return {"url": str(r.url), "status": r.status_code, "ok": r.is_success,
            "content_type": r.headers.get("content-type", ""),
            "response_ms": round((time.time() - t0) * 1000), "size_bytes": len(r.content)}


def register_tools():
    return [Tool(name="url_check", description="Check a URL's HTTP status, content-type and response time.",
                 input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                 handler=url_check)]
'''

_DNS = '''# ronin plugin: dns_lookup — resolve DNS records (dns.google, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def dns_lookup(name: str, type: str = "A") -> dict:
    r = httpx.get("https://dns.google/resolve",
                  params={"name": name, "type": type.upper()},
                  timeout=15, follow_redirects=True).json()
    answers = [{"data": a.get("data"), "ttl": a.get("TTL")} for a in r.get("Answer", [])]
    return {"name": name, "type": type.upper(), "answers": answers}


def register_tools():
    return [Tool(name="dns_lookup", description="Resolve DNS records (A/AAAA/MX/TXT/CNAME...) for a domain. No key.",
                 input_schema={"type": "object", "properties": {
                     "name": {"type": "string"}, "type": {"type": "string"}}, "required": ["name"]},
                 handler=dns_lookup)]
'''

_CHUCK = '''# ronin plugin: chuck_norris — a Chuck Norris joke (api.chucknorris.io, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def chuck_norris() -> dict:
    d = httpx.get("https://api.chucknorris.io/jokes/random", timeout=15, follow_redirects=True).json()
    return {"joke": d.get("value")}


def register_tools():
    return [Tool(name="chuck_norris", description="A random Chuck Norris joke. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=chuck_norris)]
'''

_AGEGUESS = '''# ronin plugin: age_guess — predict age from a first name (agify.io, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def age_guess(name: str) -> dict:
    d = httpx.get("https://api.agify.io", params={"name": name}, timeout=15, follow_redirects=True).json()
    return {"name": d.get("name"), "predicted_age": d.get("age"), "sample_size": d.get("count")}


def register_tools():
    return [Tool(name="age_guess", description="Predict the likely age for a first name. Fun. No key.",
                 input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                 handler=age_guess)]
'''

_DOGIMG = '''# ronin plugin: dog_image — a random dog photo URL (dog.ceo, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def dog_image() -> dict:
    d = httpx.get("https://dog.ceo/api/breeds/image/random", timeout=15, follow_redirects=True).json()
    return {"image_url": d.get("message")}


def register_tools():
    return [Tool(name="dog_image", description="Get a random dog photo URL. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=dog_image)]
'''

_BOOK = '''# ronin plugin: book_search — find a book (Open Library, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def book_search(query: str) -> dict:
    r = httpx.get("https://openlibrary.org/search.json",
                  params={"q": query, "limit": 3,
                          "fields": "title,author_name,first_publish_year"},
                  timeout=15, follow_redirects=True).json()
    docs = r.get("docs") or []
    return {"results": [{"title": d.get("title"),
                         "author": (d.get("author_name") or [None])[0],
                         "year": d.get("first_publish_year")} for d in docs]}


def register_tools():
    return [Tool(name="book_search", description="Search for a book: title, author, year (Open Library). No key.",
                 input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                 handler=book_search)]
'''

_SPACEX = '''# ronin plugin: spacex_latest — the most recent SpaceX launch (no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def spacex_latest() -> dict:
    d = httpx.get("https://api.spacexdata.com/v4/launches/latest", timeout=15, follow_redirects=True).json()
    return {"name": d.get("name"), "date_utc": d.get("date_utc"),
            "success": d.get("success"), "details": d.get("details"),
            "flight_number": d.get("flight_number")}


def register_tools():
    return [Tool(name="spacex_latest", description="Details of the most recent SpaceX launch. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=spacex_latest)]
'''


_TEXTSTATS = '''# ronin plugin: text_stats — word/char/reading-time stats (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def text_stats(text: str) -> dict:
    words = re.findall(r"\\S+", text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    return {"characters": len(text), "words": len(words),
            "sentences": len(sentences), "lines": text.count("\\n") + 1 if text else 0,
            "reading_time_min": round(len(words) / 200, 1)}


def register_tools():
    return [Tool(name="text_stats", description="Word/character/sentence counts + reading time for text. Offline.",
                 input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                 handler=text_stats)]
'''

_SLUGIFY = '''# ronin plugin: slugify — turn text into a URL slug (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def slugify(text: str) -> dict:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return {"slug": s}


def register_tools():
    return [Tool(name="slugify", description="Convert text into a clean URL slug. Offline.",
                 input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                 handler=slugify)]
'''

_CASECONV = '''# ronin plugin: case_convert — snake/camel/kebab/pascal/title case (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def case_convert(text: str, to: str = "snake") -> dict:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\\1 \\2", text)
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", spaced)]
    if not words:
        return {"result": "", "case": to}
    if to == "snake":
        r = "_".join(words)
    elif to == "kebab":
        r = "-".join(words)
    elif to == "camel":
        r = words[0] + "".join(w.capitalize() for w in words[1:])
    elif to == "pascal":
        r = "".join(w.capitalize() for w in words)
    elif to == "title":
        r = " ".join(w.capitalize() for w in words)
    else:
        return {"error": f"unknown case '{to}' (snake|kebab|camel|pascal|title)"}
    return {"result": r, "case": to}


def register_tools():
    return [Tool(name="case_convert", description="Convert an identifier between snake/camel/kebab/pascal/title case. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "to": {"type": "string",
                     "enum": ["snake", "kebab", "camel", "pascal", "title"]}}, "required": ["text"]},
                 handler=case_convert)]
'''

_DIFFTEXT = '''# ronin plugin: diff_text — unified diff between two texts (offline)
from __future__ import annotations

import difflib

from ronin_agent_patterns import Tool


def diff_text(a: str, b: str) -> dict:
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                      fromfile="a", tofile="b", lineterm="", n=2))
    return {"diff": "\\n".join(lines) or "(identical)",
            "changed": a != b}


def register_tools():
    return [Tool(name="diff_text", description="Show a unified diff between two blocks of text. Offline.",
                 input_schema={"type": "object", "properties": {
                     "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
                 handler=diff_text)]
'''

_GHRELEASE = '''# ronin plugin: github_releases — latest release of a repo (GitHub API, no auth)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def github_releases(repo: str) -> dict:
    r = httpx.get(f"https://api.github.com/repos/{repo.strip().strip('/')}/releases/latest",
                  headers={"Accept": "application/vnd.github+json"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"no published release for {repo}"}
    d = r.json()
    return {"repo": repo, "tag": d.get("tag_name"), "name": d.get("name"),
            "published": d.get("published_at"), "url": d.get("html_url")}


def register_tools():
    return [Tool(name="github_releases", description="Latest published release (tag, date) of a GitHub repo. Pass 'owner/name'. No auth.",
                 input_schema={"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
                 handler=github_releases)]
'''

_RSS = '''# ronin plugin: rss_feed — latest items from an RSS/Atom feed (stdlib parser)
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
from ronin_agent_patterns import Tool

_ATOM = "{http://www.w3.org/2005/Atom}"


def rss_feed(url: str, limit: int = 5) -> dict:
    n = min(int(limit or 5), 20)
    r = httpx.get(url, headers={"user-agent": "ronin-rss/1.0"}, timeout=15, follow_redirects=True)
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return {"error": "could not parse feed"}
    items = []
    for item in root.iter("item"):                       # RSS 2.0
        items.append({"title": item.findtext("title"), "link": item.findtext("link")})
        if len(items) >= n:
            break
    if not items:                                        # Atom fallback
        for entry in root.iter(f"{_ATOM}entry"):
            link = entry.find(f"{_ATOM}link")
            items.append({"title": entry.findtext(f"{_ATOM}title"),
                          "link": link.get("href") if link is not None else None})
            if len(items) >= n:
                break
    return {"feed": url, "items": items}


def register_tools():
    return [Tool(name="rss_feed", description="Latest items (title + link) from an RSS or Atom feed URL.",
                 input_schema={"type": "object", "properties": {
                     "url": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["url"]},
                 handler=rss_feed)]
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
    "country_info": LibraryPlugin("country_info", "Capital, population, currencies of a country", _COUNTRY),
    "dad_joke": LibraryPlugin("dad_joke", "A random dad joke", _DADJOKE),
    "sun_times": LibraryPlugin("sun_times", "Sunrise/sunset for a city", _SUNRISE),
    "color_info": LibraryPlugin("color_info", "Name + RGB/HSL of a hex color", _COLOR),
    "unit_convert": LibraryPlugin("unit_convert", "Convert length/mass/temperature (offline)", _UNITCONV),
    "random_user": LibraryPlugin("random_user", "Generate a fake user profile", _RANDOMUSER),
    "iss_location": LibraryPlugin("iss_location", "Live position of the ISS", _ISS),
    "air_quality": LibraryPlugin("air_quality", "Air quality (AQI) for a city", _AIRQUALITY),
    "github_user": LibraryPlugin("github_user", "Public GitHub profile stats", _GHUSER),
    "rhymes": LibraryPlugin("rhymes", "Words that rhyme with a word", _RHYMES),
    "synonyms": LibraryPlugin("synonyms", "Synonyms for a word", _SYNONYMS),
    "pokemon": LibraryPlugin("pokemon", "Stats for a Pokemon", _POKEMON),
    "advice": LibraryPlugin("advice", "A random piece of advice", _ADVICE),
    "github_repo": LibraryPlugin("github_repo", "Stars/forks/language for a repo", _GHREPO),
    "trivia": LibraryPlugin("trivia", "A random trivia question + answer", _TRIVIA),
    "programming_joke": LibraryPlugin("programming_joke", "A programming joke", _PROGJOKE),
    "recipe": LibraryPlugin("recipe", "Find a recipe by dish name", _RECIPE),
    "cocktail": LibraryPlugin("cocktail", "A cocktail recipe by name", _COCKTAIL),
    "anime": LibraryPlugin("anime", "Look up an anime (MyAnimeList)", _ANIME),
    "quote": LibraryPlugin("quote", "A random inspirational quote", _QUOTE),
    # --- offline dev utilities (never fail) ---
    "uuid_gen": LibraryPlugin("uuid_gen", "Generate UUIDs (offline)", _UUIDGEN),
    "password_gen": LibraryPlugin("password_gen", "Generate a strong password (offline)", _PASSWORD),
    "base64_tool": LibraryPlugin("base64_tool", "Base64 encode/decode (offline)", _BASE64),
    "hash_text": LibraryPlugin("hash_text", "md5/sha256 a string (offline)", _HASH),
    "lorem_ipsum": LibraryPlugin("lorem_ipsum", "Placeholder text (offline)", _LOREM),
    "json_format": LibraryPlugin("json_format", "Validate + pretty-print JSON (offline)", _JSONFMT),
    "timestamp": LibraryPlugin("timestamp", "Unix <-> ISO time (offline)", _TIMESTAMP),
    "url_check": LibraryPlugin("url_check", "HTTP status & timing of a URL", _URLCHECK),
    # --- more online (no key) ---
    "dns_lookup": LibraryPlugin("dns_lookup", "Resolve DNS records", _DNS),
    "chuck_norris": LibraryPlugin("chuck_norris", "A Chuck Norris joke", _CHUCK),
    "age_guess": LibraryPlugin("age_guess", "Predict age from a name", _AGEGUESS),
    "dog_image": LibraryPlugin("dog_image", "A random dog photo", _DOGIMG),
    "book_search": LibraryPlugin("book_search", "Find a book (Open Library)", _BOOK),
    "spacex_latest": LibraryPlugin("spacex_latest", "Most recent SpaceX launch", _SPACEX),
    # --- text / dev utilities ---
    "text_stats": LibraryPlugin("text_stats", "Word/char counts + reading time (offline)", _TEXTSTATS),
    "slugify": LibraryPlugin("slugify", "Text → URL slug (offline)", _SLUGIFY),
    "case_convert": LibraryPlugin("case_convert", "snake/camel/kebab/pascal/title (offline)", _CASECONV),
    "diff_text": LibraryPlugin("diff_text", "Unified diff of two texts (offline)", _DIFFTEXT),
    "github_releases": LibraryPlugin("github_releases", "Latest release of a repo", _GHRELEASE),
    "rss_feed": LibraryPlugin("rss_feed", "Latest items from an RSS/Atom feed", _RSS),
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
    "country": "country_info", "joke": "dad_joke", "sunrise": "sun_times",
    "sunset": "sun_times", "color": "color_info", "convert": "unit_convert",
    "units": "unit_convert", "user": "random_user", "iss": "iss_location",
    "aqi": "air_quality", "air": "air_quality", "ghuser": "github_user",
    "rhyme": "rhymes", "synonym": "synonyms", "thesaurus": "synonyms",
    "poke": "pokemon", "pokémon": "pokemon",
    "repo": "github_repo", "ghrepo": "github_repo",
    "progjoke": "programming_joke", "food": "recipe", "meal": "recipe",
    "drink": "cocktail", "mal": "anime", "quotes": "quote",
    "uuid": "uuid_gen", "password": "password_gen", "passwd": "password_gen",
    "base64": "base64_tool", "b64": "base64_tool", "hash": "hash_text",
    "sha256": "hash_text", "lorem": "lorem_ipsum", "json": "json_format",
    "epoch": "timestamp", "unix": "timestamp", "url": "url_check",
    "dns": "dns_lookup", "chuck": "chuck_norris", "age": "age_guess",
    "dog": "dog_image", "book": "book_search", "books": "book_search",
    "spacex": "spacex_latest",
    "wordcount": "text_stats", "stats": "text_stats", "slug": "slugify",
    "case": "case_convert", "diff": "diff_text", "releases": "github_releases",
    "rss": "rss_feed", "feed": "rss_feed",
}


def resolve(name: str) -> LibraryPlugin | None:
    """Look up a library plugin by name or alias (case-insensitive). Pure."""
    key = (name or "").strip().lower()
    return LIBRARY.get(_ALIASES.get(key, key))


def library_rows() -> list[tuple[str, str, str]]:
    """(name, needs, blurb) for every library plugin, for display. Pure."""
    return [(p.name, p.needs, p.blurb) for p in LIBRARY.values()]


def search(query: str) -> list[LibraryPlugin]:
    """Library plugins whose name, blurb, or an alias matches ``query`` (substring,
    case-insensitive). Empty query returns everything. Pure."""
    q = (query or "").strip().lower()
    if not q:
        return list(LIBRARY.values())
    alias_hits = {tgt for alias, tgt in _ALIASES.items() if q in alias}
    out: list[LibraryPlugin] = []
    for key, p in LIBRARY.items():
        if q in key or q in p.blurb.lower() or key in alias_hits:
            out.append(p)
    return out
