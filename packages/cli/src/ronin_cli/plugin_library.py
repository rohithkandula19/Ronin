"""A small library of ready-to-use ronin plugins — `ronin plugin add <name>`.

These are complete, working plugins (no auth needed) you can drop in with one
command and use immediately, or read as templates. The library data and lookup
are pure and unit-tested; each source is valid, loadable Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


_REGEX = '''# ronin plugin: regex_test — test a regex against text (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def regex_test(pattern: str, text: str, ignorecase: bool = False) -> dict:
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignorecase else 0)
    except re.error as e:
        return {"error": f"invalid regex: {e}"}
    matches = [{"match": m.group(0), "groups": list(m.groups()), "start": m.start()}
               for m in rx.finditer(text)]
    return {"pattern": pattern, "match_count": len(matches), "matches": matches[:25]}


def register_tools():
    return [Tool(name="regex_test", description="Test a regular expression against text; returns matches + groups. Offline.",
                 input_schema={"type": "object", "properties": {
                     "pattern": {"type": "string"}, "text": {"type": "string"},
                     "ignorecase": {"type": "boolean"}}, "required": ["pattern", "text"]},
                 handler=regex_test)]
'''

_JWT = '''# ronin plugin: jwt_decode — decode a JWT without verifying (offline)
from __future__ import annotations

import base64
import json

from ronin_agent_patterns import Tool


def _seg(s: str):
    s += "=" * (-len(s) % 4)
    return json.loads(base64.urlsafe_b64decode(s.encode()))


def jwt_decode(token: str) -> dict:
    parts = token.strip().split(".")
    if len(parts) < 2:
        return {"error": "not a JWT (expected header.payload.signature)"}
    try:
        return {"header": _seg(parts[0]), "payload": _seg(parts[1]),
                "note": "signature NOT verified — decode only"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not decode: {e}"}


def register_tools():
    return [Tool(name="jwt_decode", description="Decode a JWT's header & payload (does NOT verify the signature). Offline.",
                 input_schema={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
                 handler=jwt_decode)]
'''

_URLENCODE = '''# ronin plugin: url_encode — percent-encode/decode text (offline)
from __future__ import annotations

import urllib.parse

from ronin_agent_patterns import Tool


def url_encode(text: str, mode: str = "encode") -> dict:
    if mode == "decode":
        return {"mode": "decode", "result": urllib.parse.unquote(text)}
    return {"mode": "encode", "result": urllib.parse.quote(text, safe="")}


def register_tools():
    return [Tool(name="url_encode", description="URL percent-encode or decode a string. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"]}},
                     "required": ["text"]},
                 handler=url_encode)]
'''

_ROMAN = '''# ronin plugin: roman_numeral — convert int <-> Roman numeral (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_TABLE = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_numeral(value: str) -> dict:
    s = str(value).strip().upper()
    if s.isdigit():
        n = int(s)
        if not (1 <= n <= 3999):
            return {"error": "integer out of range (1-3999)"}
        out = ""
        for v, sym in _TABLE:
            while n >= v:
                out += sym
                n -= v
        return {"input": int(s), "roman": out}
    if not s or any(c not in _VALUES for c in s):
        return {"error": f"not an integer or Roman numeral: {value}"}
    total, prev = 0, 0
    for c in reversed(s):
        v = _VALUES[c]
        total += -v if v < prev else v
        prev = v
    return {"roman": s, "value": total}


def register_tools():
    return [Tool(name="roman_numeral", description="Convert an integer to a Roman numeral or vice-versa. Offline.",
                 input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
                 handler=roman_numeral)]
'''


_GEODIST = '''# ronin plugin: geo_distance — distance between two coordinates (offline)
from __future__ import annotations

import math

from ronin_agent_patterns import Tool


def geo_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    km = 2 * R * math.asin(math.sqrt(a))
    return {"km": round(km, 2), "miles": round(km * 0.621371, 2)}


def register_tools():
    return [Tool(name="geo_distance", description="Great-circle distance between two lat/lon points (km + miles). Offline.",
                 input_schema={"type": "object", "properties": {
                     "lat1": {"type": "number"}, "lon1": {"type": "number"},
                     "lat2": {"type": "number"}, "lon2": {"type": "number"}},
                     "required": ["lat1", "lon1", "lat2", "lon2"]},
                 handler=geo_distance)]
'''

_LOAN = '''# ronin plugin: loan_payment — monthly loan/mortgage payment (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def loan_payment(principal: float, annual_rate: float, years: float) -> dict:
    r = annual_rate / 100 / 12
    n = int(years * 12)
    if n <= 0:
        return {"error": "years must be > 0"}
    m = principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)
    return {"monthly_payment": round(m, 2), "total_paid": round(m * n, 2),
            "total_interest": round(m * n - principal, 2), "months": n}


def register_tools():
    return [Tool(name="loan_payment", description="Monthly payment + total interest for a loan/mortgage. Offline.",
                 input_schema={"type": "object", "properties": {
                     "principal": {"type": "number"}, "annual_rate": {"type": "number", "description": "Annual % rate."},
                     "years": {"type": "number"}}, "required": ["principal", "annual_rate", "years"]},
                 handler=loan_payment)]
'''

_TIP = '''# ronin plugin: tip_split — tip + split a bill (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def tip_split(bill: float, tip_percent: float = 18, people: int = 1) -> dict:
    people = max(1, int(people or 1))
    tip = bill * tip_percent / 100
    total = bill + tip
    return {"tip": round(tip, 2), "total": round(total, 2),
            "per_person": round(total / people, 2), "people": people}


def register_tools():
    return [Tool(name="tip_split", description="Calculate tip and split a bill among people. Offline.",
                 input_schema={"type": "object", "properties": {
                     "bill": {"type": "number"}, "tip_percent": {"type": "number"},
                     "people": {"type": "integer"}}, "required": ["bill"]},
                 handler=tip_split)]
'''

_BMI = '''# ronin plugin: bmi — body mass index (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def bmi(weight_kg: float, height_cm: float) -> dict:
    h = height_cm / 100
    if h <= 0:
        return {"error": "height must be > 0"}
    b = weight_kg / (h * h)
    cat = ("underweight" if b < 18.5 else "normal" if b < 25
           else "overweight" if b < 30 else "obese")
    return {"bmi": round(b, 1), "category": cat}


def register_tools():
    return [Tool(name="bmi", description="Body Mass Index from weight (kg) and height (cm). Offline.",
                 input_schema={"type": "object", "properties": {
                     "weight_kg": {"type": "number"}, "height_cm": {"type": "number"}},
                     "required": ["weight_kg", "height_cm"]},
                 handler=bmi)]
'''

_DICE = '''# ronin plugin: dice — roll dice in NdM(+K) notation (offline)
from __future__ import annotations

import random
import re

from ronin_agent_patterns import Tool


def dice(notation: str = "1d6") -> dict:
    m = re.fullmatch(r"(\\d*)d(\\d+)([+-]\\d+)?", notation.strip().lower())
    if not m:
        return {"error": f"bad notation '{notation}' (try 2d6, d20, 3d8+2)"}
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if count > 100 or sides > 1000 or sides < 1 or count < 1:
        return {"error": "out of range (1-100 dice, 1-1000 sides)"}
    rolls = [random.randint(1, sides) for _ in range(count)]
    return {"notation": notation, "rolls": rolls, "modifier": mod, "total": sum(rolls) + mod}


def register_tools():
    return [Tool(name="dice", description="Roll dice in NdM(+K) notation, e.g. '2d6' or '1d20+3'. Offline.",
                 input_schema={"type": "object", "properties": {"notation": {"type": "string"}}},
                 handler=dice)]
'''

_MORSE = '''# ronin plugin: morse_code — encode/decode Morse code (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_M = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
      "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
      "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
      "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
      "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
      "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
      "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..",
      "!": "-.-.--", "/": "-..-.", "@": ".--.-.", "-": "-....-"}


def morse_code(text: str, mode: str = "encode") -> dict:
    if mode == "decode":
        rev = {v: k for k, v in _M.items()}
        words = text.strip().split("   ")
        out = " ".join("".join(rev.get(c, "") for c in w.split()) for w in words)
        return {"mode": "decode", "result": out}
    out = " ".join(_M.get(c.upper(), "") for c in text if c.strip())
    return {"mode": "encode", "result": out.strip()}


def register_tools():
    return [Tool(name="morse_code", description="Encode text to Morse or decode Morse back to text. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"]}},
                     "required": ["text"]},
                 handler=morse_code)]
'''

_RANDCOLOR = '''# ronin plugin: random_color — random hex colors (offline)
from __future__ import annotations

import random

from ronin_agent_patterns import Tool


def random_color(count: int = 1) -> dict:
    n = max(1, min(int(count or 1), 20))
    cols = ["#{:06x}".format(random.randint(0, 0xFFFFFF)) for _ in range(n)]
    return {"colors": cols}


def register_tools():
    return [Tool(name="random_color", description="Generate one or more random hex colors. Offline.",
                 input_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
                 handler=random_color)]
'''

_FORECAST = '''# ronin plugin: weather_forecast — multi-day forecast for a city (open-meteo)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def weather_forecast(city: str, days: int = 3) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1}, timeout=15, follow_redirects=True).json()
    if not g.get("results"):
        return {"error": f"city not found: {city}"}
    loc = g["results"][0]
    n = max(1, min(int(days or 3), 7))
    f = httpx.get("https://api.open-meteo.com/v1/forecast",
                  params={"latitude": loc["latitude"], "longitude": loc["longitude"],
                          "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                          "forecast_days": n, "timezone": "auto"},
                  timeout=15, follow_redirects=True).json()
    d = f.get("daily", {})
    out = [{"date": dt, "high_c": hi, "low_c": lo, "rain_pct": pr}
           for dt, hi, lo, pr in zip(d.get("time", []), d.get("temperature_2m_max", []),
                                     d.get("temperature_2m_min", []),
                                     d.get("precipitation_probability_max", []))]
    return {"city": loc["name"], "country": loc.get("country"), "forecast": out}


def register_tools():
    return [Tool(name="weather_forecast", description="Multi-day weather forecast (high/low/rain) for a city. No key.",
                 input_schema={"type": "object", "properties": {
                     "city": {"type": "string"}, "days": {"type": "integer"}}, "required": ["city"]},
                 handler=weather_forecast)]
'''

_GEOCODE = '''# ronin plugin: geocode — place name -> coordinates (open-meteo, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def geocode(place: str) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": place, "count": 5}, timeout=15, follow_redirects=True).json()
    return {"results": [{"name": r["name"], "country": r.get("country"),
                         "admin": r.get("admin1"), "lat": r["latitude"], "lon": r["longitude"]}
                        for r in g.get("results", [])]}


def register_tools():
    return [Tool(name="geocode", description="Look up the latitude/longitude of a place name. No key.",
                 input_schema={"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]},
                 handler=geocode)]
'''

_RANDFACT = '''# ronin plugin: random_fact — a random interesting fact (uselessfacts, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def random_fact() -> dict:
    d = httpx.get("https://uselessfacts.jsph.pl/api/v2/facts/random",
                  params={"language": "en"}, timeout=15, follow_redirects=True).json()
    return {"fact": d.get("text")}


def register_tools():
    return [Tool(name="random_fact", description="A random interesting (useless) fact. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=random_fact)]
'''


_BASECONV = '''# ronin plugin: base_convert — convert a number between bases (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def base_convert(value: str, from_base: int = 10, to_base: int = 16) -> dict:
    fb, tb = int(from_base), int(to_base)
    if not (2 <= fb <= 36 and 2 <= tb <= 36):
        return {"error": "bases must be 2-36"}
    try:
        n = int(str(value).strip(), fb)
    except ValueError:
        return {"error": f"'{value}' is not valid in base {fb}"}
    neg, n = n < 0, abs(n)
    if n == 0:
        r = "0"
    else:
        r = ""
        while n:
            r = _DIGITS[n % tb] + r
            n //= tb
    return {"value": str(value), "from_base": fb, "to_base": tb, "result": ("-" + r) if neg else r}


def register_tools():
    return [Tool(name="base_convert", description="Convert a number between bases (2-36): dec/hex/bin/oct etc. Offline.",
                 input_schema={"type": "object", "properties": {
                     "value": {"type": "string"}, "from_base": {"type": "integer"},
                     "to_base": {"type": "integer"}}, "required": ["value"]},
                 handler=base_convert)]
'''

_DAYSBETWEEN = '''# ronin plugin: days_between — days between two dates (offline)
from __future__ import annotations

import datetime

from ronin_agent_patterns import Tool


def days_between(date1: str, date2: str = "") -> dict:
    try:
        d1 = datetime.date.fromisoformat(date1.strip())
        d2 = datetime.date.fromisoformat(date2.strip()) if date2.strip() else datetime.date.today()
    except ValueError as e:
        return {"error": f"use YYYY-MM-DD ({e})"}
    return {"date1": str(d1), "date2": str(d2), "days": abs((d2 - d1).days)}


def register_tools():
    return [Tool(name="days_between", description="Number of days between two YYYY-MM-DD dates (date2 blank = today). Offline.",
                 input_schema={"type": "object", "properties": {
                     "date1": {"type": "string"}, "date2": {"type": "string"}}, "required": ["date1"]},
                 handler=days_between)]
'''

_AGECALC = '''# ronin plugin: age_calculator — age from a birthdate (offline)
from __future__ import annotations

import datetime

from ronin_agent_patterns import Tool


def age_calculator(birthdate: str) -> dict:
    try:
        b = datetime.date.fromisoformat(birthdate.strip())
    except ValueError:
        return {"error": "use YYYY-MM-DD"}
    t = datetime.date.today()
    years = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    return {"birthdate": str(b), "age_years": years, "age_days": (t - b).days}


def register_tools():
    return [Tool(name="age_calculator", description="Exact age (years + days) from a YYYY-MM-DD birthdate. Offline.",
                 input_schema={"type": "object", "properties": {"birthdate": {"type": "string"}}, "required": ["birthdate"]},
                 handler=age_calculator)]
'''

_PCTCHANGE = '''# ronin plugin: percent_change — percentage change between two numbers (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def percent_change(old: float, new: float) -> dict:
    if old == 0:
        return {"error": "old value cannot be zero"}
    pct = (new - old) / abs(old) * 100
    return {"old": old, "new": new, "percent_change": round(pct, 2),
            "direction": "up" if pct > 0 else "down" if pct < 0 else "flat"}


def register_tools():
    return [Tool(name="percent_change", description="Percentage change from an old value to a new value. Offline.",
                 input_schema={"type": "object", "properties": {
                     "old": {"type": "number"}, "new": {"type": "number"}}, "required": ["old", "new"]},
                 handler=percent_change)]
'''

_WORDFREQ = '''# ronin plugin: word_frequency — most common words in text (offline)
from __future__ import annotations

import re
from collections import Counter

from ronin_agent_patterns import Tool


def word_frequency(text: str, top: int = 10) -> dict:
    words = re.findall(r"[a-z0-9']+", text.lower())
    c = Counter(words)
    return {"total_words": len(words), "unique_words": len(c),
            "top": [{"word": w, "count": n} for w, n in c.most_common(int(top or 10))]}


def register_tools():
    return [Tool(name="word_frequency", description="Count the most frequent words in a block of text. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "top": {"type": "integer"}}, "required": ["text"]},
                 handler=word_frequency)]
'''

_COMPOUND = '''# ronin plugin: compound_interest — investment growth (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def compound_interest(principal: float, annual_rate: float, years: float,
                      compounds_per_year: int = 12) -> dict:
    n = max(1, int(compounds_per_year or 12))
    r = annual_rate / 100
    amount = principal * (1 + r / n) ** (n * years)
    return {"principal": principal, "final_amount": round(amount, 2),
            "interest_earned": round(amount - principal, 2)}


def register_tools():
    return [Tool(name="compound_interest", description="Final value + interest of an investment with compound interest. Offline.",
                 input_schema={"type": "object", "properties": {
                     "principal": {"type": "number"}, "annual_rate": {"type": "number"},
                     "years": {"type": "number"}, "compounds_per_year": {"type": "integer"}},
                     "required": ["principal", "annual_rate", "years"]},
                 handler=compound_interest)]
'''

_REDDIT = '''# ronin plugin: reddit_top — top posts in a subreddit (no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def reddit_top(subreddit: str, limit: int = 5) -> dict:
    r = httpx.get(f"https://www.reddit.com/r/{subreddit.strip().strip('/')}/top.json",
                  params={"limit": min(int(limit or 5), 15), "t": "day"},
                  headers={"user-agent": "ronin/1.0"}, timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"could not fetch r/{subreddit}"}
    try:
        children = r.json()["data"]["children"]
    except Exception:  # noqa: BLE001
        return {"error": "could not parse reddit response"}
    return {"subreddit": subreddit, "posts": [
        {"title": p["data"]["title"], "score": p["data"]["score"],
         "url": "https://reddit.com" + p["data"]["permalink"]} for p in children]}


def register_tools():
    return [Tool(name="reddit_top", description="Top posts of the day in a subreddit. No key.",
                 input_schema={"type": "object", "properties": {
                     "subreddit": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["subreddit"]},
                 handler=reddit_top)]
'''

_CRYPTOMKT = '''# ronin plugin: crypto_market — market cap & rank for a coin (CoinGecko, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def crypto_market(coin: str = "bitcoin") -> dict:
    r = httpx.get(f"https://api.coingecko.com/api/v3/coins/{coin.lower().strip()}",
                  params={"localization": "false", "tickers": "false",
                          "community_data": "false", "developer_data": "false"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": f"unknown coin '{coin}' (use the CoinGecko id)"}
    j = r.json()
    m = j.get("market_data", {})
    return {"name": j.get("name"), "symbol": j.get("symbol"),
            "price_usd": m.get("current_price", {}).get("usd"),
            "market_cap_usd": m.get("market_cap", {}).get("usd"),
            "rank": j.get("market_cap_rank"),
            "ath_usd": m.get("ath", {}).get("usd")}


def register_tools():
    return [Tool(name="crypto_market", description="Market cap, rank and all-time-high for a cryptocurrency. No key.",
                 input_schema={"type": "object", "properties": {"coin": {"type": "string"}}},
                 handler=crypto_market)]
'''


_PRIME = '''# ronin plugin: prime_check — primality + prime factors (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def prime_check(n: int) -> dict:
    n = int(n)
    if n < 2:
        return {"n": n, "is_prime": False, "prime_factors": []}
    factors, m, d = [], n, 2
    while d * d <= m:
        while m % d == 0:
            factors.append(d)
            m //= d
        d += 1
    if m > 1:
        factors.append(m)
    return {"n": n, "is_prime": len(factors) == 1, "prime_factors": sorted(set(factors))}


def register_tools():
    return [Tool(name="prime_check", description="Check if a number is prime and list its prime factors. Offline.",
                 input_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                 handler=prime_check)]
'''

_FIB = '''# ronin plugin: fibonacci — the Fibonacci sequence (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def fibonacci(n: int = 10) -> dict:
    n = max(0, min(int(n or 10), 1000))
    a, b, seq = 0, 1, []
    for _ in range(n):
        seq.append(a)
        a, b = b, a + b
    return {"n": n, "sequence": seq[:60], "nth": seq[-1] if seq else 0}


def register_tools():
    return [Tool(name="fibonacci", description="First N Fibonacci numbers (and the Nth). Offline.",
                 input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
                 handler=fibonacci)]
'''

_CAESAR = '''# ronin plugin: caesar_cipher — shift-cipher encode/decode (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def caesar_cipher(text: str, shift: int = 3, mode: str = "encode") -> dict:
    s = int(shift) * (-1 if mode == "decode" else 1)
    out = []
    for c in text:
        if c.isupper():
            out.append(chr((ord(c) - 65 + s) % 26 + 65))
        elif c.islower():
            out.append(chr((ord(c) - 97 + s) % 26 + 97))
        else:
            out.append(c)
    return {"mode": mode, "shift": int(shift), "result": "".join(out)}


def register_tools():
    return [Tool(name="caesar_cipher", description="Caesar shift-cipher encode/decode (ROT-N). Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "shift": {"type": "integer"},
                     "mode": {"type": "string", "enum": ["encode", "decode"]}}, "required": ["text"]},
                 handler=caesar_cipher)]
'''

_ANAGRAM = '''# ronin plugin: anagram_check — are two words anagrams? (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def anagram_check(word1: str, word2: str) -> dict:
    norm = lambda w: sorted(c for c in w.lower() if c.isalnum())
    return {"word1": word1, "word2": word2, "is_anagram": norm(word1) == norm(word2)}


def register_tools():
    return [Tool(name="anagram_check", description="Check whether two words/phrases are anagrams. Offline.",
                 input_schema={"type": "object", "properties": {
                     "word1": {"type": "string"}, "word2": {"type": "string"}}, "required": ["word1", "word2"]},
                 handler=anagram_check)]
'''

_PUBLICIP = '''# ronin plugin: public_ip — your public IP address (ipify, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def public_ip() -> dict:
    d = httpx.get("https://api.ipify.org", params={"format": "json"},
                  timeout=15, follow_redirects=True).json()
    return {"ip": d.get("ip")}


def register_tools():
    return [Tool(name="public_ip", description="Your machine's current public IP address. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=public_ip)]
'''

_WIKIRAND = '''# ronin plugin: wikipedia_random — a random Wikipedia article (no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def wikipedia_random() -> dict:
    r = httpx.get("https://en.wikipedia.org/api/rest_v1/page/random/summary",
                  headers={"accept": "application/json", "user-agent": "ronin-plugin/1.0"},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": "wikipedia unavailable"}
    d = r.json()
    return {"title": d.get("title"), "summary": (d.get("extract") or "")[:320],
            "url": (d.get("content_urls", {}).get("desktop", {}) or {}).get("page")}


def register_tools():
    return [Tool(name="wikipedia_random", description="A random Wikipedia article summary. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=wikipedia_random)]
'''


_LUHN = '''# ronin plugin: luhn_check — validate a card/IMEI number via Luhn (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def luhn_check(number: str) -> dict:
    digits = [int(c) for c in str(number) if c.isdigit()]
    if not digits:
        return {"error": "no digits found"}
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return {"number": str(number), "valid": total % 10 == 0}


def register_tools():
    return [Tool(name="luhn_check", description="Validate a credit-card / IMEI number with the Luhn checksum. Offline.",
                 input_schema={"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]},
                 handler=luhn_check)]
'''

_DOW = '''# ronin plugin: day_of_week — what weekday a date falls on (offline)
from __future__ import annotations

import datetime

from ronin_agent_patterns import Tool


def day_of_week(date: str) -> dict:
    try:
        d = datetime.date.fromisoformat(date.strip())
    except ValueError:
        return {"error": "use YYYY-MM-DD"}
    return {"date": str(d), "day": d.strftime("%A"), "weekday": d.isoweekday()}


def register_tools():
    return [Tool(name="day_of_week", description="The weekday name for any YYYY-MM-DD date. Offline.",
                 input_schema={"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]},
                 handler=day_of_week)]
'''

_LEAP = '''# ronin plugin: leap_year — is a year a leap year? (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def leap_year(year: int) -> dict:
    y = int(year)
    return {"year": y, "is_leap": (y % 4 == 0 and y % 100 != 0) or y % 400 == 0}


def register_tools():
    return [Tool(name="leap_year", description="Whether a given year is a leap year. Offline.",
                 input_schema={"type": "object", "properties": {"year": {"type": "integer"}}, "required": ["year"]},
                 handler=leap_year)]
'''

_SCRABBLE = '''# ronin plugin: scrabble_score — Scrabble points for a word (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_VALS = {}
for letters, v in [("eaionrtlsu", 1), ("dg", 2), ("bcmp", 3), ("fhvwy", 4),
                   ("k", 5), ("jx", 8), ("qz", 10)]:
    for ch in letters:
        _VALS[ch] = v


def scrabble_score(word: str) -> dict:
    score = sum(_VALS.get(c, 0) for c in word.lower())
    return {"word": word, "score": score}


def register_tools():
    return [Tool(name="scrabble_score", description="Scrabble point value of a word. Offline.",
                 input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
                 handler=scrabble_score)]
'''

_HEXRGB = '''# ronin plugin: hex_rgb — convert between hex and RGB color (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def hex_rgb(value: str) -> dict:
    v = value.strip()
    if "," in v or v.lower().startswith("rgb"):
        nums = [int(x) for x in re.findall(r"\\d+", v)][:3]
        if len(nums) != 3 or any(not 0 <= x <= 255 for x in nums):
            return {"error": "need three 0-255 values"}
        return {"rgb": nums, "hex": "#{:02x}{:02x}{:02x}".format(*nums)}
    h = v.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return {"error": "invalid hex color"}
    try:
        rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return {"error": "invalid hex color"}
    return {"hex": "#" + h.lower(), "rgb": rgb}


def register_tools():
    return [Tool(name="hex_rgb", description="Convert a color between hex (#ff5733) and RGB (255,87,51). Offline.",
                 input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
                 handler=hex_rgb)]
'''

_GRAVATAR = '''# ronin plugin: gravatar — build a Gravatar avatar URL for an email (offline)
from __future__ import annotations

import hashlib

from ronin_agent_patterns import Tool


def gravatar(email: str, size: int = 200) -> dict:
    h = hashlib.md5(email.strip().lower().encode()).hexdigest()
    return {"email": email,
            "url": f"https://www.gravatar.com/avatar/{h}?s={int(size or 200)}&d=identicon"}


def register_tools():
    return [Tool(name="gravatar", description="Gravatar avatar image URL for an email address. Offline.",
                 input_schema={"type": "object", "properties": {
                     "email": {"type": "string"}, "size": {"type": "integer"}}, "required": ["email"]},
                 handler=gravatar)]
'''

_CATFACT = '''# ronin plugin: cat_fact — a random cat fact (catfact.ninja, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def cat_fact() -> dict:
    d = httpx.get("https://catfact.ninja/fact", timeout=15, follow_redirects=True).json()
    return {"fact": d.get("fact")}


def register_tools():
    return [Tool(name="cat_fact", description="A random fact about cats. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=cat_fact)]
'''

_NASA = '''# ronin plugin: nasa_apod — NASA Astronomy Picture of the Day (DEMO_KEY)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def nasa_apod() -> dict:
    r = httpx.get("https://api.nasa.gov/planetary/apod",
                  params={"api_key": "DEMO_KEY"}, timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return {"error": "NASA APOD unavailable (DEMO_KEY rate-limited — get a free key at api.nasa.gov)"}
    j = r.json()
    return {"title": j.get("title"), "date": j.get("date"),
            "explanation": (j.get("explanation") or "")[:320], "image_url": j.get("url")}


def register_tools():
    return [Tool(name="nasa_apod", description="NASA's Astronomy Picture of the Day (title, image, blurb). DEMO_KEY.",
                 input_schema={"type": "object", "properties": {}}, handler=nasa_apod)]
'''


_PWSTRENGTH = '''# ronin plugin: password_strength — rate a password (offline)
from __future__ import annotations

import re

from ronin_agent_patterns import Tool


def password_strength(password: str) -> dict:
    p, score, tips = password, 0, []
    if len(p) >= 8:
        score += 1
    else:
        tips.append("use at least 8 characters")
    if len(p) >= 12:
        score += 1
    if re.search(r"[a-z]", p) and re.search(r"[A-Z]", p):
        score += 1
    else:
        tips.append("mix upper and lower case")
    if re.search(r"\\d", p):
        score += 1
    else:
        tips.append("add a digit")
    if re.search(r"[^A-Za-z0-9]", p):
        score += 1
    else:
        tips.append("add a symbol")
    labels = ["very weak", "weak", "fair", "good", "strong", "very strong"]
    return {"score": score, "rating": labels[score], "suggestions": tips}


def register_tools():
    return [Tool(name="password_strength", description="Rate a password's strength and suggest improvements. Offline.",
                 input_schema={"type": "object", "properties": {"password": {"type": "string"}}, "required": ["password"]},
                 handler=password_strength)]
'''

_BINTEXT = '''# ronin plugin: binary_text — text <-> binary (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def binary_text(text: str, mode: str = "encode") -> dict:
    if mode == "decode":
        try:
            return {"mode": "decode", "result": "".join(chr(int(b, 2)) for b in text.split())}
        except ValueError:
            return {"error": "invalid binary (space-separate 8-bit groups)"}
    return {"mode": "encode", "result": " ".join(format(ord(c), "08b") for c in text)}


def register_tools():
    return [Tool(name="binary_text", description="Convert text to binary or binary back to text. Offline.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"]}},
                     "required": ["text"]},
                 handler=binary_text)]
'''

_CONTRAST = '''# ronin plugin: color_contrast — WCAG contrast ratio of two colors (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def _lum(h: str):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return None
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])


def color_contrast(hex1: str, hex2: str) -> dict:
    l1, l2 = _lum(hex1), _lum(hex2)
    if l1 is None or l2 is None:
        return {"error": "invalid hex color"}
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    return {"ratio": round(ratio, 2), "AA_normal": ratio >= 4.5,
            "AAA_normal": ratio >= 7, "AA_large": ratio >= 3}


def register_tools():
    return [Tool(name="color_contrast", description="WCAG contrast ratio between two hex colors (accessibility). Offline.",
                 input_schema={"type": "object", "properties": {
                     "hex1": {"type": "string"}, "hex2": {"type": "string"}}, "required": ["hex1", "hex2"]},
                 handler=color_contrast)]
'''

_NATO = '''# ronin plugin: nato_alphabet — spell text in the NATO phonetic alphabet (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool

_NATO = {"A": "Alfa", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo",
         "F": "Foxtrot", "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett",
         "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November", "O": "Oscar",
         "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
         "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray", "Y": "Yankee",
         "Z": "Zulu", "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
         "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}


def nato_alphabet(text: str) -> dict:
    out = [_NATO.get(c.upper(), c) for c in text if not c.isspace()]
    return {"text": text, "nato": " ".join(out)}


def register_tools():
    return [Tool(name="nato_alphabet", description="Spell text using the NATO phonetic alphabet (Alfa Bravo Charlie...). Offline.",
                 input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                 handler=nato_alphabet)]
'''

_FACTORIAL = '''# ronin plugin: factorial — n! (offline)
from __future__ import annotations

import math

from ronin_agent_patterns import Tool


def factorial(n: int) -> dict:
    n = int(n)
    if n < 0:
        return {"error": "n must be >= 0"}
    if n > 1000:
        return {"error": "n too large (max 1000)"}
    return {"n": n, "factorial": str(math.factorial(n))}


def register_tools():
    return [Tool(name="factorial", description="Compute n! (factorial). Offline.",
                 input_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                 handler=factorial)]
'''

_ASCIICODE = '''# ronin plugin: ascii_code — characters <-> code points (offline)
from __future__ import annotations

from ronin_agent_patterns import Tool


def ascii_code(text: str) -> dict:
    return {"text": text, "codes": [{"char": c, "code": ord(c), "hex": hex(ord(c))}
                                    for c in text[:60]]}


def register_tools():
    return [Tool(name="ascii_code", description="Show the code point (decimal + hex) of each character. Offline.",
                 input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                 handler=ascii_code)]
'''

_URBAN = '''# ronin plugin: urban_dictionary — slang definitions (no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def urban_dictionary(term: str) -> dict:
    d = httpx.get("https://api.urbandictionary.com/v0/define",
                  params={"term": term}, timeout=15, follow_redirects=True).json()
    defs = d.get("list") or []
    if not defs:
        return {"term": term, "error": "no definition found"}
    top = defs[0]
    clean = lambda s: (s or "").replace("[", "").replace("]", "")
    return {"term": term, "definition": clean(top.get("definition"))[:400],
            "example": clean(top.get("example"))[:200], "thumbs_up": top.get("thumbs_up")}


def register_tools():
    return [Tool(name="urban_dictionary", description="Slang/Urban Dictionary definition of a term. No key.",
                 input_schema={"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]},
                 handler=urban_dictionary)]
'''

_RANDMEAL = '''# ronin plugin: random_meal — a random recipe (TheMealDB, no key)
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


def random_meal() -> dict:
    d = httpx.get("https://www.themealdb.com/api/json/v1/1/random.php",
                  timeout=15, follow_redirects=True).json()
    meals = d.get("meals") or []
    if not meals:
        return {"error": "no meal returned"}
    m = meals[0]
    return {"name": m.get("strMeal"), "category": m.get("strCategory"),
            "area": m.get("strArea"), "instructions": (m.get("strInstructions") or "")[:300]}


def register_tools():
    return [Tool(name="random_meal", description="A random recipe idea with instructions. No key.",
                 input_schema={"type": "object", "properties": {}}, handler=random_meal)]
'''


_W1 = {}

_W1["reverse_text"] = ('Reverse a string (offline)', '''from ronin_agent_patterns import Tool
def reverse_text(text: str) -> dict:
    return {"result": text[::-1]}
def register_tools():
    return [Tool(name="reverse_text", description="Reverse a string. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=reverse_text)]
''')

_W1["reverse_words"] = ('Reverse word order (offline)', '''from ronin_agent_patterns import Tool
def reverse_words(text: str) -> dict:
    return {"result": " ".join(text.split()[::-1])}
def register_tools():
    return [Tool(name="reverse_words", description="Reverse the order of words in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=reverse_words)]
''')

_W1["count_vowels"] = ('Count vowels & consonants (offline)', '''from ronin_agent_patterns import Tool
def count_vowels(text: str) -> dict:
    v = sum(c in "aeiouAEIOU" for c in text)
    cons = sum(c.isalpha() and c not in "aeiouAEIOU" for c in text)
    return {"vowels": v, "consonants": cons, "letters": v + cons}
def register_tools():
    return [Tool(name="count_vowels", description="Count vowels and consonants in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=count_vowels)]
''')

_W1["acronym"] = ('Make an acronym from a phrase (offline)', '''from ronin_agent_patterns import Tool
def acronym(text: str) -> dict:
    return {"acronym": "".join(w[0].upper() for w in text.split() if w)}
def register_tools():
    return [Tool(name="acronym", description="Build an acronym from the first letter of each word. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=acronym)]
''')

_W1["pig_latin"] = ('Translate to Pig Latin (offline)', '''import re
from ronin_agent_patterns import Tool
def pig_latin(text: str) -> dict:
    def conv(w):
        if not w.isalpha(): return w
        if w[0].lower() in "aeiou": return w + "way"
        i = next((i for i, c in enumerate(w) if c.lower() in "aeiou"), len(w))
        return w[i:] + w[:i] + "ay"
    return {"result": " ".join(conv(w) for w in text.split())}
def register_tools():
    return [Tool(name="pig_latin", description="Translate text into Pig Latin. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=pig_latin)]
''')

_W1["leetspeak"] = ('Convert to leetspeak (offline)', '''from ronin_agent_patterns import Tool
_L = {"a":"4","e":"3","i":"1","o":"0","s":"5","t":"7","l":"1","b":"8"}
def leetspeak(text: str) -> dict:
    return {"result": "".join(_L.get(c.lower(), c) for c in text)}
def register_tools():
    return [Tool(name="leetspeak", description="Convert text to l33tspeak. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=leetspeak)]
''')

_W1["atbash_cipher"] = ('Atbash cipher (offline)', '''from ronin_agent_patterns import Tool
def atbash_cipher(text: str) -> dict:
    def f(c):
        if c.isupper(): return chr(155 - ord(c))
        if c.islower(): return chr(219 - ord(c))
        return c
    return {"result": "".join(f(c) for c in text)}
def register_tools():
    return [Tool(name="atbash_cipher", description="Encode/decode with the Atbash cipher (self-inverse). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=atbash_cipher)]
''')

_W1["rot47"] = ('ROT47 cipher (offline)', '''from ronin_agent_patterns import Tool
def rot47(text: str) -> dict:
    out = [chr(33 + (ord(c) - 33 + 47) % 94) if 33 <= ord(c) <= 126 else c for c in text]
    return {"result": "".join(out)}
def register_tools():
    return [Tool(name="rot47", description="ROT47 cipher (self-inverse, covers punctuation+digits). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=rot47)]
''')

_W1["vigenere_cipher"] = ('Vigenere cipher (offline)', '''from ronin_agent_patterns import Tool
def vigenere_cipher(text: str, key: str, mode: str = "encode") -> dict:
    key = [k for k in key.lower() if k.isalpha()]
    if not key: return {"error": "key needs letters"}
    out, j = [], 0
    for c in text:
        if c.isalpha():
            s = (ord(key[j % len(key)]) - 97) * (-1 if mode == "decode" else 1)
            base = 65 if c.isupper() else 97
            out.append(chr((ord(c) - base + s) % 26 + base)); j += 1
        else: out.append(c)
    return {"mode": mode, "result": "".join(out)}
def register_tools():
    return [Tool(name="vigenere_cipher", description="Vigenere cipher encode/decode with a keyword. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"key":{"type":"string"},"mode":{"type":"string","enum":["encode","decode"]}},"required":["text","key"]}, handler=vigenere_cipher)]
''')

_W1["sort_lines"] = ('Sort lines of text (offline)', '''from ronin_agent_patterns import Tool
def sort_lines(text: str, reverse: bool = False) -> dict:
    return {"result": "\\n".join(sorted(text.splitlines(), reverse=bool(reverse)))}
def register_tools():
    return [Tool(name="sort_lines", description="Sort the lines of a block of text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"reverse":{"type":"boolean"}},"required":["text"]}, handler=sort_lines)]
''')

_W1["dedupe_lines"] = ('Remove duplicate lines (offline)', '''from ronin_agent_patterns import Tool
def dedupe_lines(text: str) -> dict:
    seen, out = set(), []
    for ln in text.splitlines():
        if ln not in seen: seen.add(ln); out.append(ln)
    return {"result": "\\n".join(out), "removed": len(text.splitlines()) - len(out)}
def register_tools():
    return [Tool(name="dedupe_lines", description="Remove duplicate lines, keeping order. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=dedupe_lines)]
''')

_W1["find_replace"] = ('Find and replace text (offline)', '''from ronin_agent_patterns import Tool
def find_replace(text: str, find: str, replace: str = "") -> dict:
    return {"result": text.replace(find, replace), "count": text.count(find)}
def register_tools():
    return [Tool(name="find_replace", description="Replace all occurrences of a substring. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"find":{"type":"string"},"replace":{"type":"string"}},"required":["text","find"]}, handler=find_replace)]
''')

_W1["word_wrap"] = ('Wrap text to a width (offline)', '''import textwrap
from ronin_agent_patterns import Tool
def word_wrap(text: str, width: int = 80) -> dict:
    return {"result": textwrap.fill(text, width=max(1, int(width or 80)))}
def register_tools():
    return [Tool(name="word_wrap", description="Wrap text to a given column width. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"width":{"type":"integer"}},"required":["text"]}, handler=word_wrap)]
''')

_W1["html_escape"] = ('HTML escape/unescape (offline)', '''import html as _h
from ronin_agent_patterns import Tool
def html_escape(text: str, mode: str = "encode") -> dict:
    return {"result": _h.unescape(text) if mode == "decode" else _h.escape(text)}
def register_tools():
    return [Tool(name="html_escape", description="HTML-escape text or unescape entities. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"mode":{"type":"string","enum":["encode","decode"]}},"required":["text"]}, handler=html_escape)]
''')

_W1["hex_encode"] = ('Text <-> hex (offline)', '''from ronin_agent_patterns import Tool
def hex_encode(text: str, mode: str = "encode") -> dict:
    try:
        if mode == "decode": return {"result": bytes.fromhex(text.replace(" ", "")).decode("utf-8", "replace")}
        return {"result": text.encode("utf-8").hex()}
    except ValueError as e:
        return {"error": str(e)}
def register_tools():
    return [Tool(name="hex_encode", description="Encode text to hex or decode hex back to text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"mode":{"type":"string","enum":["encode","decode"]}},"required":["text"]}, handler=hex_encode)]
''')

_W1["char_frequency"] = ('Character frequency (offline)', '''from collections import Counter
from ronin_agent_patterns import Tool
def char_frequency(text: str, top: int = 10) -> dict:
    c = Counter(ch for ch in text if not ch.isspace())
    return {"top": [{"char": ch, "count": n} for ch, n in c.most_common(int(top or 10))]}
def register_tools():
    return [Tool(name="char_frequency", description="Most frequent characters in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"top":{"type":"integer"}},"required":["text"]}, handler=char_frequency)]
''')

_W1["extract_emails"] = ('Extract emails from text (offline)', '''import re
from ronin_agent_patterns import Tool
def extract_emails(text: str) -> dict:
    return {"emails": sorted(set(re.findall(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+", text)))}
def register_tools():
    return [Tool(name="extract_emails", description="Find all email addresses in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=extract_emails)]
''')

_W1["extract_urls"] = ('Extract URLs from text (offline)', '''import re
from ronin_agent_patterns import Tool
def extract_urls(text: str) -> dict:
    return {"urls": sorted(set(re.findall(r"https?://[^\\s)>\\]]+", text)))}
def register_tools():
    return [Tool(name="extract_urls", description="Find all URLs in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=extract_urls)]
''')

_W1["title_case"] = ('Smart title-case (offline)', '''from ronin_agent_patterns import Tool
_SMALL = {"a","an","the","and","but","or","for","nor","on","at","to","by","of","in","with"}
def title_case(text: str) -> dict:
    words = text.split()
    out = [w.capitalize() if (i == 0 or i == len(words) - 1 or w.lower() not in _SMALL) else w.lower() for i, w in enumerate(words)]
    return {"result": " ".join(out)}
def register_tools():
    return [Tool(name="title_case", description="Title-case a heading (keeps small words lowercase). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=title_case)]
''')


_W2 = {}

_W2["gcd_lcm"] = ('GCD & LCM of two numbers (offline)', '''import math
from ronin_agent_patterns import Tool
def gcd_lcm(a: int, b: int) -> dict:
    a, b = int(a), int(b)
    g = math.gcd(a, b)
    return {"gcd": g, "lcm": abs(a * b) // g if g else 0}
def register_tools():
    return [Tool(name="gcd_lcm", description="Greatest common divisor and least common multiple. Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"integer"},"b":{"type":"integer"}},"required":["a","b"]}, handler=gcd_lcm)]
''')

_W2["sum_digits"] = ('Sum the digits of a number (offline)', '''from ronin_agent_patterns import Tool
def sum_digits(n: int) -> dict:
    s = sum(int(c) for c in str(abs(int(n))))
    return {"n": int(n), "digit_sum": s}
def register_tools():
    return [Tool(name="sum_digits", description="Sum the digits of an integer. Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"integer"}},"required":["n"]}, handler=sum_digits)]
''')

_W2["ordinal"] = ('Ordinal of a number, 1->1st (offline)', '''from ronin_agent_patterns import Tool
def ordinal(n: int) -> dict:
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1:"st",2:"nd",3:"rd"}.get(n % 10, "th")
    return {"n": n, "ordinal": f"{n}{suf}"}
def register_tools():
    return [Tool(name="ordinal", description="Ordinal form of a number (1 -> 1st). Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"integer"}},"required":["n"]}, handler=ordinal)]
''')

_W2["number_format"] = ('Add thousands separators (offline)', '''from ronin_agent_patterns import Tool
def number_format(n: float) -> dict:
    return {"result": f"{float(n):,}"}
def register_tools():
    return [Tool(name="number_format", description="Format a number with thousands separators. Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"number"}},"required":["n"]}, handler=number_format)]
''')

_W2["stats_summary"] = ('Mean/median/min/max of numbers (offline)', '''import statistics, re
from ronin_agent_patterns import Tool
def stats_summary(numbers: str) -> dict:
    nums = [float(x) for x in re.findall(r"-?\\d+\\.?\\d*", numbers)]
    if not nums: return {"error": "no numbers found"}
    return {"count": len(nums), "sum": sum(nums), "mean": round(statistics.mean(nums), 4),
            "median": statistics.median(nums), "min": min(nums), "max": max(nums)}
def register_tools():
    return [Tool(name="stats_summary", description="Mean/median/min/max/sum of a list of numbers. Offline.",
                 input_schema={"type":"object","properties":{"numbers":{"type":"string","description":"comma or space separated"}},"required":["numbers"]}, handler=stats_summary)]
''')

_W2["round_to"] = ('Round to N decimals (offline)', '''from ronin_agent_patterns import Tool
def round_to(value: float, decimals: int = 2) -> dict:
    return {"result": round(float(value), int(decimals))}
def register_tools():
    return [Tool(name="round_to", description="Round a number to N decimal places. Offline.",
                 input_schema={"type":"object","properties":{"value":{"type":"number"},"decimals":{"type":"integer"}},"required":["value"]}, handler=round_to)]
''')

_W2["clamp"] = ('Clamp a value to a range (offline)', '''from ronin_agent_patterns import Tool
def clamp(value: float, low: float, high: float) -> dict:
    return {"result": max(float(low), min(float(value), float(high)))}
def register_tools():
    return [Tool(name="clamp", description="Constrain a value between a low and high bound. Offline.",
                 input_schema={"type":"object","properties":{"value":{"type":"number"},"low":{"type":"number"},"high":{"type":"number"}},"required":["value","low","high"]}, handler=clamp)]
''')

_W2["collatz"] = ('Collatz sequence length (offline)', '''from ronin_agent_patterns import Tool
def collatz(n: int) -> dict:
    n = int(n)
    if n < 1: return {"error": "n must be >= 1"}
    steps, x = 0, n
    while x != 1:
        x = x // 2 if x % 2 == 0 else 3 * x + 1
        steps += 1
        if steps > 100000: break
    return {"n": n, "steps": steps}
def register_tools():
    return [Tool(name="collatz", description="Steps for n to reach 1 in the Collatz conjecture. Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"integer"}},"required":["n"]}, handler=collatz)]
''')

_W2["quadratic"] = ('Solve a quadratic equation (offline)', '''import cmath
from ronin_agent_patterns import Tool
def quadratic(a: float, b: float, c: float) -> dict:
    a, b, c = float(a), float(b), float(c)
    if a == 0: return {"error": "a cannot be 0"}
    d = (b * b - 4 * a * c)
    r1 = (-b + cmath.sqrt(d)) / (2 * a)
    r2 = (-b - cmath.sqrt(d)) / (2 * a)
    fmt = lambda z: round(z.real, 4) if abs(z.imag) < 1e-9 else str(z)
    return {"roots": [fmt(r1), fmt(r2)], "discriminant": d}
def register_tools():
    return [Tool(name="quadratic", description="Solve ax^2+bx+c=0 (real or complex roots). Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"number"},"b":{"type":"number"},"c":{"type":"number"}},"required":["a","b","c"]}, handler=quadratic)]
''')

_W2["circle"] = ('Circle area & circumference (offline)', '''import math
from ronin_agent_patterns import Tool
def circle(radius: float) -> dict:
    r = float(radius)
    return {"radius": r, "area": round(math.pi * r * r, 4), "circumference": round(2 * math.pi * r, 4)}
def register_tools():
    return [Tool(name="circle", description="Area and circumference of a circle from its radius. Offline.",
                 input_schema={"type":"object","properties":{"radius":{"type":"number"}},"required":["radius"]}, handler=circle)]
''')

_W2["pythagorean"] = ('Hypotenuse of a right triangle (offline)', '''import math
from ronin_agent_patterns import Tool
def pythagorean(a: float, b: float) -> dict:
    return {"a": float(a), "b": float(b), "hypotenuse": round(math.hypot(float(a), float(b)), 4)}
def register_tools():
    return [Tool(name="pythagorean", description="Hypotenuse from the two legs of a right triangle. Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"number"},"b":{"type":"number"}},"required":["a","b"]}, handler=pythagorean)]
''')

_W2["discount"] = ('Apply a discount to a price (offline)', '''from ronin_agent_patterns import Tool
def discount(price: float, percent: float) -> dict:
    price, percent = float(price), float(percent)
    save = price * percent / 100
    return {"original": price, "percent_off": percent, "saved": round(save, 2), "final": round(price - save, 2)}
def register_tools():
    return [Tool(name="discount", description="Final price after a percentage discount. Offline.",
                 input_schema={"type":"object","properties":{"price":{"type":"number"},"percent":{"type":"number"}},"required":["price","percent"]}, handler=discount)]
''')

_W2["simple_interest"] = ('Simple interest (offline)', '''from ronin_agent_patterns import Tool
def simple_interest(principal: float, rate: float, years: float) -> dict:
    i = float(principal) * float(rate) / 100 * float(years)
    return {"interest": round(i, 2), "total": round(float(principal) + i, 2)}
def register_tools():
    return [Tool(name="simple_interest", description="Simple interest + total over a period. Offline.",
                 input_schema={"type":"object","properties":{"principal":{"type":"number"},"rate":{"type":"number"},"years":{"type":"number"}},"required":["principal","rate","years"]}, handler=simple_interest)]
''')

_W2["bytes_human"] = ('Humanize a byte count (offline)', '''from ronin_agent_patterns import Tool
def bytes_human(bytes: float) -> dict:
    n = float(bytes)
    for unit in ["B","KB","MB","GB","TB","PB"]:
        if abs(n) < 1024: return {"bytes": float(bytes), "human": f"{n:.2f} {unit}"}
        n /= 1024
    return {"bytes": float(bytes), "human": f"{n:.2f} EB"}
def register_tools():
    return [Tool(name="bytes_human", description="Convert a byte count to a human-readable size. Offline.",
                 input_schema={"type":"object","properties":{"bytes":{"type":"number"}},"required":["bytes"]}, handler=bytes_human)]
''')

_W2["seconds_human"] = ('Humanize a duration (offline)', '''from ronin_agent_patterns import Tool
def seconds_human(seconds: float) -> dict:
    s = int(float(seconds)); parts = []
    for label, size in [("d",86400),("h",3600),("m",60),("s",1)]:
        if s >= size: parts.append(f"{s // size}{label}"); s %= size
    return {"seconds": float(seconds), "human": " ".join(parts) or "0s"}
def register_tools():
    return [Tool(name="seconds_human", description="Convert seconds into a human duration (1d 2h 3m). Offline.",
                 input_schema={"type":"object","properties":{"seconds":{"type":"number"}},"required":["seconds"]}, handler=seconds_human)]
''')

_W2["percent_of"] = ('What percent is X of Y (offline)', '''from ronin_agent_patterns import Tool
def percent_of(part: float, whole: float) -> dict:
    if float(whole) == 0: return {"error": "whole cannot be 0"}
    return {"percent": round(float(part) / float(whole) * 100, 2)}
def register_tools():
    return [Tool(name="percent_of", description="What percentage part is of whole. Offline.",
                 input_schema={"type":"object","properties":{"part":{"type":"number"},"whole":{"type":"number"}},"required":["part","whole"]}, handler=percent_of)]
''')

_W2["bit_ops"] = ('Bitwise AND/OR/XOR (offline)', '''from ronin_agent_patterns import Tool
def bit_ops(a: int, b: int, op: str = "and") -> dict:
    a, b = int(a), int(b)
    r = {"and": a & b, "or": a | b, "xor": a ^ b}.get(op.lower())
    if r is None: return {"error": "op must be and/or/xor"}
    return {"a": a, "b": b, "op": op, "result": r, "binary": bin(r)}
def register_tools():
    return [Tool(name="bit_ops", description="Bitwise AND/OR/XOR of two integers. Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"integer"},"b":{"type":"integer"},"op":{"type":"string","enum":["and","or","xor"]}},"required":["a","b"]}, handler=bit_ops)]
''')

_W2["temperature"] = ('Convert a temperature (offline)', '''from ronin_agent_patterns import Tool
def temperature(value: float, from_unit: str = "c", to_unit: str = "f") -> dict:
    v, f, t = float(value), from_unit.lower(), to_unit.lower()
    c = v if f == "c" else (v - 32) * 5 / 9 if f == "f" else v - 273.15
    out = c if t == "c" else c * 9 / 5 + 32 if t == "f" else c + 273.15
    return {"value": v, "from": f, "to": t, "result": round(out, 2)}
def register_tools():
    return [Tool(name="temperature", description="Convert temperature between C/F/K. Offline.",
                 input_schema={"type":"object","properties":{"value":{"type":"number"},"from_unit":{"type":"string"},"to_unit":{"type":"string"}},"required":["value"]}, handler=temperature)]
''')

_W2["is_perfect"] = ('Perfect-number check (offline)', '''from ronin_agent_patterns import Tool
def is_perfect(n: int) -> dict:
    n = int(n)
    if n < 1: return {"n": n, "is_perfect": False}
    divs = [i for i in range(1, n) if n % i == 0]
    return {"n": n, "is_perfect": sum(divs) == n, "divisors": divs}
def register_tools():
    return [Tool(name="is_perfect", description="Whether a number equals the sum of its proper divisors. Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"integer"}},"required":["n"]}, handler=is_perfect)]
''')


_W3 = {}

_W3["add_days"] = ('Add/subtract days from a date (offline)', '''import datetime
from ronin_agent_patterns import Tool
def add_days(date: str, days: int) -> dict:
    try: d = datetime.date.fromisoformat(date.strip())
    except ValueError: return {"error": "use YYYY-MM-DD"}
    out = d + datetime.timedelta(days=int(days))
    return {"start": str(d), "days": int(days), "result": str(out), "weekday": out.strftime("%A")}
def register_tools():
    return [Tool(name="add_days", description="Add (or subtract) days from a date. Offline.",
                 input_schema={"type":"object","properties":{"date":{"type":"string"},"days":{"type":"integer"}},"required":["date","days"]}, handler=add_days)]
''')

_W3["is_weekend"] = ('Is a date a weekend? (offline)', '''import datetime
from ronin_agent_patterns import Tool
def is_weekend(date: str) -> dict:
    try: d = datetime.date.fromisoformat(date.strip())
    except ValueError: return {"error": "use YYYY-MM-DD"}
    return {"date": str(d), "day": d.strftime("%A"), "is_weekend": d.isoweekday() >= 6}
def register_tools():
    return [Tool(name="is_weekend", description="Whether a date falls on a weekend. Offline.",
                 input_schema={"type":"object","properties":{"date":{"type":"string"}},"required":["date"]}, handler=is_weekend)]
''')

_W3["days_in_month"] = ('Days in a month (offline)', '''import calendar
from ronin_agent_patterns import Tool
def days_in_month(year: int, month: int) -> dict:
    try: n = calendar.monthrange(int(year), int(month))[1]
    except (ValueError, calendar.IllegalMonthError): return {"error": "invalid year/month"}
    return {"year": int(year), "month": int(month), "days": n}
def register_tools():
    return [Tool(name="days_in_month", description="Number of days in a given month/year. Offline.",
                 input_schema={"type":"object","properties":{"year":{"type":"integer"},"month":{"type":"integer"}},"required":["year","month"]}, handler=days_in_month)]
''')

_W3["week_number"] = ('ISO week number of a date (offline)', '''import datetime
from ronin_agent_patterns import Tool
def week_number(date: str) -> dict:
    try: d = datetime.date.fromisoformat(date.strip())
    except ValueError: return {"error": "use YYYY-MM-DD"}
    iso = d.isocalendar()
    return {"date": str(d), "iso_year": iso[0], "week": iso[1], "weekday": iso[2]}
def register_tools():
    return [Tool(name="week_number", description="ISO week number for a date. Offline.",
                 input_schema={"type":"object","properties":{"date":{"type":"string"}},"required":["date"]}, handler=week_number)]
''')

_W3["mime_type"] = ('Guess a file MIME type (offline)', '''import mimetypes
from ronin_agent_patterns import Tool
def mime_type(filename: str) -> dict:
    mt, enc = mimetypes.guess_type(filename)
    return {"filename": filename, "mime_type": mt or "application/octet-stream", "encoding": enc}
def register_tools():
    return [Tool(name="mime_type", description="Guess the MIME type of a filename. Offline.",
                 input_schema={"type":"object","properties":{"filename":{"type":"string"}},"required":["filename"]}, handler=mime_type)]
''')

_W3["parse_url"] = ('Break a URL into parts (offline)', '''import urllib.parse
from ronin_agent_patterns import Tool
def parse_url(url: str) -> dict:
    p = urllib.parse.urlparse(url)
    return {"scheme": p.scheme, "host": p.hostname, "port": p.port, "path": p.path,
            "query": dict(urllib.parse.parse_qsl(p.query)), "fragment": p.fragment}
def register_tools():
    return [Tool(name="parse_url", description="Break a URL into scheme/host/path/query parts. Offline.",
                 input_schema={"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}, handler=parse_url)]
''')

_W3["json_minify"] = ('Minify JSON (offline)', '''import json
from ronin_agent_patterns import Tool
def json_minify(text: str) -> dict:
    try: obj = json.loads(text)
    except json.JSONDecodeError as e: return {"error": str(e)}
    return {"result": json.dumps(obj, separators=(",", ":"), ensure_ascii=False)}
def register_tools():
    return [Tool(name="json_minify", description="Minify a JSON string (strip whitespace). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=json_minify)]
''')

_W3["csv_to_json"] = ('Convert CSV to JSON (offline)', '''import csv, io, json
from ronin_agent_patterns import Tool
def csv_to_json(csv_text: str) -> dict:
    try: rows = list(csv.DictReader(io.StringIO(csv_text.strip())))
    except csv.Error as e: return {"error": str(e)}
    return {"rows": len(rows), "json": json.dumps(rows, ensure_ascii=False)}
def register_tools():
    return [Tool(name="csv_to_json", description="Convert CSV text (with header row) to JSON. Offline.",
                 input_schema={"type":"object","properties":{"csv_text":{"type":"string"}},"required":["csv_text"]}, handler=csv_to_json)]
''')

_W3["semver_compare"] = ('Compare two semver versions (offline)', '''import re
from ronin_agent_patterns import Tool
def semver_compare(v1: str, v2: str) -> dict:
    def parse(v): return [int(x) for x in re.findall(r"\\d+", v)[:3]] + [0, 0, 0]
    a, b = parse(v1)[:3], parse(v2)[:3]
    cmp = (a > b) - (a < b)
    return {"v1": v1, "v2": v2, "result": {1: "v1 > v2", 0: "equal", -1: "v1 < v2"}[cmp]}
def register_tools():
    return [Tool(name="semver_compare", description="Compare two semantic versions. Offline.",
                 input_schema={"type":"object","properties":{"v1":{"type":"string"},"v2":{"type":"string"}},"required":["v1","v2"]}, handler=semver_compare)]
''')

_W3["crc32"] = ('CRC32 checksum of text (offline)', '''import zlib
from ronin_agent_patterns import Tool
def crc32(text: str) -> dict:
    return {"crc32": format(zlib.crc32(text.encode()) & 0xffffffff, "08x")}
def register_tools():
    return [Tool(name="crc32", description="CRC32 checksum of text (hex). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=crc32)]
''')

_W3["random_string"] = ('Random string generator (offline)', '''import secrets, string
from ronin_agent_patterns import Tool
def random_string(length: int = 16, alphanumeric: bool = True) -> dict:
    n = max(1, min(int(length or 16), 256))
    chars = (string.ascii_letters + string.digits) if alphanumeric else string.printable[:94]
    return {"result": "".join(secrets.choice(chars) for _ in range(n))}
def register_tools():
    return [Tool(name="random_string", description="Generate a random string of a given length. Offline.",
                 input_schema={"type":"object","properties":{"length":{"type":"integer"},"alphanumeric":{"type":"boolean"}}}, handler=random_string)]
''')

_W3["random_int"] = ('Random integer in a range (offline)', '''import random
from ronin_agent_patterns import Tool
def random_int(min: int = 1, max: int = 100, count: int = 1) -> dict:
    lo, hi = int(min), int(max)
    if lo > hi: lo, hi = hi, lo
    return {"numbers": [random.randint(lo, hi) for _ in range(max(1, min(int(count or 1), 100)))]}
def register_tools():
    return [Tool(name="random_int", description="Random integers in an inclusive range. Offline.",
                 input_schema={"type":"object","properties":{"min":{"type":"integer"},"max":{"type":"integer"},"count":{"type":"integer"}}}, handler=random_int)]
''')

_W3["coin_flip"] = ('Flip a coin (offline)', '''import random
from ronin_agent_patterns import Tool
def coin_flip(count: int = 1) -> dict:
    n = max(1, min(int(count or 1), 100))
    flips = [random.choice(["heads", "tails"]) for _ in range(n)]
    return {"flips": flips, "heads": flips.count("heads"), "tails": flips.count("tails")}
def register_tools():
    return [Tool(name="coin_flip", description="Flip one or more coins. Offline.",
                 input_schema={"type":"object","properties":{"count":{"type":"integer"}}}, handler=coin_flip)]
''')

_W3["magic_8_ball"] = ('Ask the Magic 8-Ball (offline)', '''import random
from ronin_agent_patterns import Tool
_A = ["It is certain.","Without a doubt.","Yes definitely.","Most likely.","Outlook good.",
      "Signs point to yes.","Reply hazy, try again.","Ask again later.","Cannot predict now.",
      "Don't count on it.","My reply is no.","Very doubtful.","Outlook not so good."]
def magic_8_ball(question: str = "") -> dict:
    return {"question": question, "answer": random.choice(_A)}
def register_tools():
    return [Tool(name="magic_8_ball", description="Ask the Magic 8-Ball a yes/no question. Offline.",
                 input_schema={"type":"object","properties":{"question":{"type":"string"}}}, handler=magic_8_ball)]
''')

_W3["random_choice"] = ('Pick a random option (offline)', '''import random
from ronin_agent_patterns import Tool
def random_choice(options: str) -> dict:
    opts = [o.strip() for o in options.split(",") if o.strip()]
    if not opts: return {"error": "give comma-separated options"}
    return {"options": opts, "chosen": random.choice(opts)}
def register_tools():
    return [Tool(name="random_choice", description="Randomly pick one of several comma-separated options. Offline.",
                 input_schema={"type":"object","properties":{"options":{"type":"string"}},"required":["options"]}, handler=random_choice)]
''')

_W3["palindrome"] = ('Palindrome check (offline)', '''from ronin_agent_patterns import Tool
def palindrome(text: str) -> dict:
    s = [c.lower() for c in text if c.isalnum()]
    return {"text": text, "is_palindrome": s == s[::-1]}
def register_tools():
    return [Tool(name="palindrome", description="Check whether text is a palindrome. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=palindrome)]
''')

_W3["percentage_bar"] = ('ASCII progress bar (offline)', '''from ronin_agent_patterns import Tool
def percentage_bar(value: float, total: float = 100, width: int = 20) -> dict:
    pct = max(0.0, min(float(value) / float(total) if total else 0, 1.0))
    w = int(width or 20); filled = round(pct * w)
    return {"percent": round(pct * 100, 1), "bar": "[" + "#" * filled + "-" * (w - filled) + "]"}
def register_tools():
    return [Tool(name="percentage_bar", description="Render an ASCII progress bar for a value. Offline.",
                 input_schema={"type":"object","properties":{"value":{"type":"number"},"total":{"type":"number"},"width":{"type":"integer"}},"required":["value"]}, handler=percentage_bar)]
''')

_W3["count_substring"] = ('Count occurrences of a substring (offline)', '''from ronin_agent_patterns import Tool
def count_substring(text: str, substring: str) -> dict:
    return {"count": text.count(substring)}
def register_tools():
    return [Tool(name="count_substring", description="Count occurrences of a substring in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"substring":{"type":"string"}},"required":["text","substring"]}, handler=count_substring)]
''')

_W3["unix_now"] = ('Current unix timestamp (offline)', '''import datetime
from ronin_agent_patterns import Tool
def unix_now() -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {"unix": int(now.timestamp()), "iso_utc": now.isoformat(), "millis": int(now.timestamp() * 1000)}
def register_tools():
    return [Tool(name="unix_now", description="The current Unix timestamp + ISO time. Offline.",
                 input_schema={"type":"object","properties":{}}, handler=unix_now)]
''')

_W3["dedupe_list"] = ('Dedupe a comma list (offline)', '''from ronin_agent_patterns import Tool
def dedupe_list(items: str) -> dict:
    seen, out = set(), []
    for it in (i.strip() for i in items.split(",")):
        if it and it not in seen: seen.add(it); out.append(it)
    return {"unique": out, "count": len(out)}
def register_tools():
    return [Tool(name="dedupe_list", description="Remove duplicates from a comma-separated list. Offline.",
                 input_schema={"type":"object","properties":{"items":{"type":"string"}},"required":["items"]}, handler=dedupe_list)]
''')


_W4 = {}

_W4["levenshtein"] = ('Edit distance between two strings (offline)', '''from ronin_agent_patterns import Tool
def levenshtein(a: str, b: str) -> dict:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return {"a": a, "b": b, "distance": prev[-1]}
def register_tools():
    return [Tool(name="levenshtein", description="Levenshtein edit distance between two strings. Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"string"},"b":{"type":"string"}},"required":["a","b"]}, handler=levenshtein)]
''')

_W4["jaccard_similarity"] = ('Word-set similarity (offline)', '''import re
from ronin_agent_patterns import Tool
def jaccard_similarity(a: str, b: str) -> dict:
    sa, sb = set(re.findall(r"\\w+", a.lower())), set(re.findall(r"\\w+", b.lower()))
    u = sa | sb
    return {"similarity": round(len(sa & sb) / len(u), 4) if u else 1.0}
def register_tools():
    return [Tool(name="jaccard_similarity", description="Jaccard word-set similarity of two texts (0-1). Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"string"},"b":{"type":"string"}},"required":["a","b"]}, handler=jaccard_similarity)]
''')

_W4["hamming_distance"] = ('Hamming distance (offline)', '''from ronin_agent_patterns import Tool
def hamming_distance(a: str, b: str) -> dict:
    if len(a) != len(b): return {"error": "strings must be equal length"}
    return {"distance": sum(x != y for x, y in zip(a, b))}
def register_tools():
    return [Tool(name="hamming_distance", description="Hamming distance between two equal-length strings. Offline.",
                 input_schema={"type":"object","properties":{"a":{"type":"string"},"b":{"type":"string"}},"required":["a","b"]}, handler=hamming_distance)]
''')

_W4["shannon_entropy"] = ('Shannon entropy of text (offline)', '''import math
from collections import Counter
from ronin_agent_patterns import Tool
def shannon_entropy(text: str) -> dict:
    if not text: return {"entropy": 0.0}
    counts = Counter(text); n = len(text)
    e = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return {"entropy_bits_per_char": round(e, 4), "length": n}
def register_tools():
    return [Tool(name="shannon_entropy", description="Shannon entropy (bits/char) of text — randomness measure. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=shannon_entropy)]
''')

_W4["soundex"] = ('Soundex phonetic code (offline)', '''from ronin_agent_patterns import Tool
_SX = {**dict.fromkeys("bfpv","1"),**dict.fromkeys("cgjkqsxz","2"),**dict.fromkeys("dt","3"),
       **{"l":"4"},**dict.fromkeys("mn","5"),**{"r":"6"}}
def soundex(word: str) -> dict:
    w = [c.lower() for c in word if c.isalpha()]
    if not w: return {"error": "no letters"}
    code = w[0].upper(); prev = _SX.get(w[0], "")
    for c in w[1:]:
        d = _SX.get(c, "")
        if d and d != prev: code += d
        if c not in "hw": prev = d
    return {"word": word, "soundex": (code + "000")[:4]}
def register_tools():
    return [Tool(name="soundex", description="Soundex phonetic code of a word (sounds-alike matching). Offline.",
                 input_schema={"type":"object","properties":{"word":{"type":"string"}},"required":["word"]}, handler=soundex)]
''')

_W4["validate_email"] = ('Validate an email address (offline)', '''import re
from ronin_agent_patterns import Tool
def validate_email(email: str) -> dict:
    ok = bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", email.strip()))
    return {"email": email, "valid": ok}
def register_tools():
    return [Tool(name="validate_email", description="Check whether a string is a syntactically valid email. Offline.",
                 input_schema={"type":"object","properties":{"email":{"type":"string"}},"required":["email"]}, handler=validate_email)]
''')

_W4["validate_ipv4"] = ('Validate an IPv4 address (offline)', '''from ronin_agent_patterns import Tool
def validate_ipv4(ip: str) -> dict:
    parts = ip.strip().split(".")
    ok = len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 and (p == "0" or not p.startswith("0")) for p in parts)
    return {"ip": ip, "valid": ok}
def register_tools():
    return [Tool(name="validate_ipv4", description="Check whether a string is a valid IPv4 address. Offline.",
                 input_schema={"type":"object","properties":{"ip":{"type":"string"}},"required":["ip"]}, handler=validate_ipv4)]
''')

_W4["credit_card_type"] = ('Detect a card network (offline)', '''import re
from ronin_agent_patterns import Tool
def credit_card_type(number: str) -> dict:
    n = re.sub(r"\\D", "", number)
    if re.match(r"^4", n): brand = "Visa"
    elif re.match(r"^5[1-5]", n) or re.match(r"^2(2[2-9]|[3-6]|7[01]|720)", n): brand = "Mastercard"
    elif re.match(r"^3[47]", n): brand = "American Express"
    elif re.match(r"^6(011|5)", n): brand = "Discover"
    elif re.match(r"^3(0[0-5]|[68])", n): brand = "Diners Club"
    elif re.match(r"^35", n): brand = "JCB"
    else: brand = "Unknown"
    return {"number_prefix": n[:6], "brand": brand}
def register_tools():
    return [Tool(name="credit_card_type", description="Identify the card network from a card number prefix. Offline.",
                 input_schema={"type":"object","properties":{"number":{"type":"string"}},"required":["number"]}, handler=credit_card_type)]
''')

_W4["initials"] = ('Initials from a name (offline)', '''from ronin_agent_patterns import Tool
def initials(name: str) -> dict:
    return {"initials": "".join(w[0].upper() for w in name.split() if w)}
def register_tools():
    return [Tool(name="initials", description="Initials from a full name. Offline.",
                 input_schema={"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, handler=initials)]
''')

_W4["password_entropy"] = ('Estimate password entropy (offline)', '''import math, re
from ronin_agent_patterns import Tool
def password_entropy(password: str) -> dict:
    pool = (26 if re.search(r"[a-z]", password) else 0) + (26 if re.search(r"[A-Z]", password) else 0) + \\
           (10 if re.search(r"\\d", password) else 0) + (33 if re.search(r"[^A-Za-z0-9]", password) else 0)
    bits = len(password) * math.log2(pool) if pool else 0
    label = "very weak" if bits < 28 else "weak" if bits < 36 else "reasonable" if bits < 60 else "strong" if bits < 128 else "very strong"
    return {"bits": round(bits, 1), "rating": label}
def register_tools():
    return [Tool(name="password_entropy", description="Estimate a password's entropy in bits. Offline.",
                 input_schema={"type":"object","properties":{"password":{"type":"string"}},"required":["password"]}, handler=password_entropy)]
''')

_W4["genderize"] = ('Guess gender from a name', '''import httpx
from ronin_agent_patterns import Tool
def genderize(name: str) -> dict:
    d = httpx.get("https://api.genderize.io", params={"name": name}, timeout=15, follow_redirects=True).json()
    return {"name": d.get("name"), "gender": d.get("gender"), "probability": d.get("probability")}
def register_tools():
    return [Tool(name="genderize", description="Predict the likely gender for a first name. No key.",
                 input_schema={"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, handler=genderize)]
''')

_W4["nationalize"] = ('Guess nationality from a name', '''import httpx
from ronin_agent_patterns import Tool
def nationalize(name: str) -> dict:
    d = httpx.get("https://api.nationalize.io", params={"name": name}, timeout=15, follow_redirects=True).json()
    return {"name": d.get("name"), "countries": [{"country": c["country_id"], "probability": round(c["probability"], 3)} for c in (d.get("country") or [])[:3]]}
def register_tools():
    return [Tool(name="nationalize", description="Predict likely nationalities for a name. No key.",
                 input_schema={"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, handler=nationalize)]
''')

_W4["kanye_quote"] = ('A random Kanye quote', '''import httpx
from ronin_agent_patterns import Tool
def kanye_quote() -> dict:
    return {"quote": httpx.get("https://api.kanye.rest", timeout=15, follow_redirects=True).json().get("quote")}
def register_tools():
    return [Tool(name="kanye_quote", description="A random Kanye West quote. No key.",
                 input_schema={"type":"object","properties":{}}, handler=kanye_quote)]
''')

_W4["yes_no"] = ('Random yes/no with a gif', '''import httpx
from ronin_agent_patterns import Tool
def yes_no() -> dict:
    d = httpx.get("https://yesno.wtf/api", timeout=15, follow_redirects=True).json()
    return {"answer": d.get("answer"), "gif": d.get("image")}
def register_tools():
    return [Tool(name="yes_no", description="A random yes/no answer with a matching gif. No key.",
                 input_schema={"type":"object","properties":{}}, handler=yes_no)]
''')

_W4["fox_image"] = ('A random fox photo', '''import httpx
from ronin_agent_patterns import Tool
def fox_image() -> dict:
    return {"image_url": httpx.get("https://randomfox.ca/floof/", timeout=15, follow_redirects=True).json().get("image")}
def register_tools():
    return [Tool(name="fox_image", description="A random fox photo URL. No key.",
                 input_schema={"type":"object","properties":{}}, handler=fox_image)]
''')

_W4["word_count_lines"] = ('Words per line (offline)', '''from ronin_agent_patterns import Tool
def word_count_lines(text: str) -> dict:
    return {"lines": [{"line": i + 1, "words": len(ln.split())} for i, ln in enumerate(text.splitlines())]}
def register_tools():
    return [Tool(name="word_count_lines", description="Count words on each line of text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=word_count_lines)]
''')

_W4["caesar_brute"] = ('Brute-force a Caesar cipher (offline)', '''from ronin_agent_patterns import Tool
def caesar_brute(text: str) -> dict:
    out = []
    for s in range(1, 26):
        shifted = "".join(chr((ord(c) - 97 + s) % 26 + 97) if c.islower() else chr((ord(c) - 65 + s) % 26 + 65) if c.isupper() else c for c in text)
        out.append({"shift": s, "text": shifted})
    return {"candidates": out}
def register_tools():
    return [Tool(name="caesar_brute", description="Show all 25 Caesar-shift decodings of text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=caesar_brute)]
''')

_W4["snake_to_words"] = ('snake_case/camelCase -> words (offline)', '''import re
from ronin_agent_patterns import Tool
def snake_to_words(text: str) -> dict:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\\1 \\2", text).replace("_", " ").replace("-", " ")
    return {"result": " ".join(w.capitalize() for w in spaced.split())}
def register_tools():
    return [Tool(name="snake_to_words", description="Turn snake_case / camelCase / kebab into Title Words. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=snake_to_words)]
''')

_W4["roman_today"] = ('Decimal to Roman numerals, clock style (offline)', '''from ronin_agent_patterns import Tool
_T = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
def roman_today(numbers: str) -> dict:
    out = []
    for tok in numbers.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= 3999:
            n = int(tok); r = ""
            for v, sym in _T:
                while n >= v: r += sym; n -= v
            out.append({"n": int(tok), "roman": r})
    return {"converted": out}
def register_tools():
    return [Tool(name="roman_today", description="Convert several integers to Roman numerals at once. Offline.",
                 input_schema={"type":"object","properties":{"numbers":{"type":"string"}},"required":["numbers"]}, handler=roman_today)]
''')


_W5 = {}

_W5["vowel_remove"] = ('Remove vowels from text (offline)', '''from ronin_agent_patterns import Tool
def vowel_remove(text: str) -> dict:
    return {"result": "".join(c for c in text if c.lower() not in "aeiou")}
def register_tools():
    return [Tool(name="vowel_remove", description="Remove all vowels from text (disemvowel). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=vowel_remove)]
''')

_W5["alternating_case"] = ('mOcKiNg sPoNgEbOb case (offline)', '''from ronin_agent_patterns import Tool
def alternating_case(text: str) -> dict:
    out, up = [], False
    for c in text:
        if c.isalpha():
            out.append(c.upper() if up else c.lower()); up = not up
        else: out.append(c)
    return {"result": "".join(out)}
def register_tools():
    return [Tool(name="alternating_case", description="Convert text to aLtErNaTiNg (mocking) case. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=alternating_case)]
''')

_W5["clap_text"] = ('Put 👏 between 👏 words (offline)', '''from ronin_agent_patterns import Tool
def clap_text(text: str) -> dict:
    return {"result": " 👏 ".join(text.split())}
def register_tools():
    return [Tool(name="clap_text", description="Insert clap emojis between words. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=clap_text)]
''')

_W5["reverse_each_word"] = ('Reverse letters in each word (offline)', '''from ronin_agent_patterns import Tool
def reverse_each_word(text: str) -> dict:
    return {"result": " ".join(w[::-1] for w in text.split())}
def register_tools():
    return [Tool(name="reverse_each_word", description="Reverse the letters within each word. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=reverse_each_word)]
''')

_W5["is_isogram"] = ('Isogram check (no repeated letters, offline)', '''from ronin_agent_patterns import Tool
def is_isogram(word: str) -> dict:
    letters = [c.lower() for c in word if c.isalpha()]
    return {"word": word, "is_isogram": len(letters) == len(set(letters))}
def register_tools():
    return [Tool(name="is_isogram", description="Whether a word has no repeating letters (isogram). Offline.",
                 input_schema={"type":"object","properties":{"word":{"type":"string"}},"required":["word"]}, handler=is_isogram)]
''')

_W5["is_pangram"] = ('Pangram check (offline)', '''import string
from ronin_agent_patterns import Tool
def is_pangram(text: str) -> dict:
    present = {c for c in text.lower() if c.isalpha()}
    missing = sorted(set(string.ascii_lowercase) - present)
    return {"is_pangram": not missing, "missing": missing}
def register_tools():
    return [Tool(name="is_pangram", description="Whether text uses every letter of the alphabet. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=is_pangram)]
''')

_W5["number_to_words"] = ('Spell a number in English (offline)', '''from ronin_agent_patterns import Tool
_O = ["zero","one","two","three","four","five","six","seven","eight","nine","ten","eleven","twelve","thirteen","fourteen","fifteen","sixteen","seventeen","eighteen","nineteen"]
_T = ["","","twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety"]
def _u1000(x):
    if x == 0: return ""
    if x < 20: return _O[x]
    if x < 100: return _T[x // 10] + ("-" + _O[x % 10] if x % 10 else "")
    return _O[x // 100] + " hundred" + (" " + _u1000(x % 100) if x % 100 else "")
def number_to_words(n: int) -> dict:
    n = int(n)
    if n == 0: return {"n": 0, "words": "zero"}
    neg, n2, parts = n < 0, abs(n), []
    for name, size in [("billion", 10**9), ("million", 10**6), ("thousand", 1000), ("", 1)]:
        if n2 >= size:
            parts.append(_u1000(n2 // size) + (" " + name if name else "")); n2 %= size
    return {"n": int(n), "words": ("negative " if neg else "") + " ".join(p for p in parts if p)}
def register_tools():
    return [Tool(name="number_to_words", description="Spell an integer out in English words. Offline.",
                 input_schema={"type":"object","properties":{"n":{"type":"integer"}},"required":["n"]}, handler=number_to_words)]
''')

_W5["tally"] = ('Count items in a comma list (offline)', '''from collections import Counter
from ronin_agent_patterns import Tool
def tally(items: str) -> dict:
    c = Counter(i.strip() for i in items.split(",") if i.strip())
    return {"counts": dict(c.most_common())}
def register_tools():
    return [Tool(name="tally", description="Count occurrences of each item in a comma-separated list. Offline.",
                 input_schema={"type":"object","properties":{"items":{"type":"string"}},"required":["items"]}, handler=tally)]
''')

_W5["longest_word"] = ('Longest & shortest word (offline)', '''import re
from ronin_agent_patterns import Tool
def longest_word(text: str) -> dict:
    words = re.findall(r"[A-Za-z']+", text)
    if not words: return {"error": "no words"}
    return {"longest": max(words, key=len), "shortest": min(words, key=len)}
def register_tools():
    return [Tool(name="longest_word", description="Find the longest and shortest word in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=longest_word)]
''')

_W5["count_syllables"] = ('Estimate syllables in a word (offline)', '''import re
from ronin_agent_patterns import Tool
def count_syllables(word: str) -> dict:
    w = word.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1: n -= 1
    return {"word": word, "syllables": max(1, n)}
def register_tools():
    return [Tool(name="count_syllables", description="Estimate the syllable count of a word (heuristic). Offline.",
                 input_schema={"type":"object","properties":{"word":{"type":"string"}},"required":["word"]}, handler=count_syllables)]
''')

_W5["unique_chars"] = ('Distinct characters (offline)', '''from ronin_agent_patterns import Tool
def unique_chars(text: str) -> dict:
    chars = [c for c in text if not c.isspace()]
    return {"total": len(chars), "unique": len(set(chars)), "characters": sorted(set(chars))}
def register_tools():
    return [Tool(name="unique_chars", description="Count and list the distinct characters in text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=unique_chars)]
''')

_W5["remove_punctuation"] = ('Strip punctuation (offline)', '''import string
from ronin_agent_patterns import Tool
def remove_punctuation(text: str) -> dict:
    return {"result": text.translate(str.maketrans("", "", string.punctuation))}
def register_tools():
    return [Tool(name="remove_punctuation", description="Remove all punctuation from text. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=remove_punctuation)]
''')

_W5["swapcase"] = ('Swap upper/lower case (offline)', '''from ronin_agent_patterns import Tool
def swapcase(text: str) -> dict:
    return {"result": text.swapcase()}
def register_tools():
    return [Tool(name="swapcase", description="Swap the case of every letter. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=swapcase)]
''')

_W5["capitalize_sentences"] = ('Capitalize each sentence (offline)', '''import re
from ronin_agent_patterns import Tool
def capitalize_sentences(text: str) -> dict:
    out = re.sub(r"(^|[.!?]\\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return {"result": out}
def register_tools():
    return [Tool(name="capitalize_sentences", description="Capitalize the first letter of each sentence. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=capitalize_sentences)]
''')

_W5["truncate_text"] = ('Truncate with ellipsis (offline)', '''from ronin_agent_patterns import Tool
def truncate_text(text: str, length: int = 100) -> dict:
    n = max(1, int(length or 100))
    return {"result": text if len(text) <= n else text[:n].rstrip() + "…"}
def register_tools():
    return [Tool(name="truncate_text", description="Truncate text to N characters with an ellipsis. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"length":{"type":"integer"}},"required":["text"]}, handler=truncate_text)]
''')

_W5["repeat_text"] = ('Repeat text N times (offline)', '''from ronin_agent_patterns import Tool
def repeat_text(text: str, times: int = 3, separator: str = " ") -> dict:
    return {"result": separator.join([text] * max(1, min(int(times or 3), 1000)))}
def register_tools():
    return [Tool(name="repeat_text", description="Repeat text N times with a separator. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"},"times":{"type":"integer"},"separator":{"type":"string"}},"required":["text"]}, handler=repeat_text)]
''')

_W5["count_words"] = ('Word & character count (offline)', '''from ronin_agent_patterns import Tool
def count_words(text: str) -> dict:
    return {"words": len(text.split()), "characters": len(text), "characters_no_spaces": len(text.replace(" ", ""))}
def register_tools():
    return [Tool(name="count_words", description="Quick word and character count. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=count_words)]
''')

_W5["frequency_sort"] = ('Sort words by frequency (offline)', '''import re
from collections import Counter
from ronin_agent_patterns import Tool
def frequency_sort(text: str) -> dict:
    c = Counter(re.findall(r"[a-z0-9']+", text.lower()))
    return {"ranked": [{"word": w, "count": n} for w, n in c.most_common()]}
def register_tools():
    return [Tool(name="frequency_sort", description="Rank all words by how often they appear. Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=frequency_sort)]
''')

_W5["caesar_rot13"] = ('ROT13 quick (offline)', '''import codecs
from ronin_agent_patterns import Tool
def caesar_rot13(text: str) -> dict:
    return {"result": codecs.encode(text, "rot_13")}
def register_tools():
    return [Tool(name="caesar_rot13", description="ROT13 a string (self-inverse). Offline.",
                 input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}, handler=caesar_rot13)]
''')

_W5["cat_image"] = ('A random cat photo', '''import httpx
from ronin_agent_patterns import Tool
def cat_image() -> dict:
    d = httpx.get("https://cataas.com/cat?json=true", timeout=15, follow_redirects=True).json()
    u = d.get("url") or (("https://cataas.com/cat/" + d["id"]) if d.get("id") else None)
    return {"image_url": u}
def register_tools():
    return [Tool(name="cat_image", description="A random cat photo URL. No key.",
                 input_schema={"type":"object","properties":{}}, handler=cat_image)]
''')

_W5["dog_breeds"] = ('List dog breeds', '''import httpx
from ronin_agent_patterns import Tool
def dog_breeds() -> dict:
    d = httpx.get("https://dog.ceo/api/breeds/list/all", timeout=15, follow_redirects=True).json()
    return {"breeds": sorted((d.get("message") or {}).keys())}
def register_tools():
    return [Tool(name="dog_breeds", description="List all known dog breeds. No key.",
                 input_schema={"type":"object","properties":{}}, handler=dog_breeds)]
''')

_W5["random_joke"] = ('A random joke (setup/punchline)', '''import httpx
from ronin_agent_patterns import Tool
def random_joke() -> dict:
    d = httpx.get("https://official-joke-api.appspot.com/random_joke", timeout=15, follow_redirects=True).json()
    return {"setup": d.get("setup"), "punchline": d.get("punchline")}
def register_tools():
    return [Tool(name="random_joke", description="A random two-part joke. No key.",
                 input_schema={"type":"object","properties":{}}, handler=random_joke)]
''')

_W5["trivia_categories"] = ('List trivia categories', '''import httpx
from ronin_agent_patterns import Tool
def trivia_categories() -> dict:
    d = httpx.get("https://opentdb.com/api_category.php", timeout=15, follow_redirects=True).json()
    return {"categories": [c["name"] for c in d.get("trivia_categories", [])]}
def register_tools():
    return [Tool(name="trivia_categories", description="List available trivia categories. No key.",
                 input_schema={"type":"object","properties":{}}, handler=trivia_categories)]
''')


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
    "regex_test": LibraryPlugin("regex_test", "Test a regex against text (offline)", _REGEX),
    "jwt_decode": LibraryPlugin("jwt_decode", "Decode a JWT header/payload (offline)", _JWT),
    "url_encode": LibraryPlugin("url_encode", "URL encode/decode (offline)", _URLENCODE),
    "roman_numeral": LibraryPlugin("roman_numeral", "Int <-> Roman numeral (offline)", _ROMAN),
    # --- calculators + more (offline unless noted) ---
    "geo_distance": LibraryPlugin("geo_distance", "Distance between two coordinates (offline)", _GEODIST),
    "loan_payment": LibraryPlugin("loan_payment", "Loan/mortgage monthly payment (offline)", _LOAN),
    "tip_split": LibraryPlugin("tip_split", "Tip + split a bill (offline)", _TIP),
    "bmi": LibraryPlugin("bmi", "Body mass index (offline)", _BMI),
    "dice": LibraryPlugin("dice", "Roll dice in NdM notation (offline)", _DICE),
    "morse_code": LibraryPlugin("morse_code", "Encode/decode Morse code (offline)", _MORSE),
    "random_color": LibraryPlugin("random_color", "Random hex colors (offline)", _RANDCOLOR),
    "weather_forecast": LibraryPlugin("weather_forecast", "Multi-day forecast for a city", _FORECAST),
    "geocode": LibraryPlugin("geocode", "Place name → coordinates", _GEOCODE),
    "random_fact": LibraryPlugin("random_fact", "A random interesting fact", _RANDFACT),
    "base_convert": LibraryPlugin("base_convert", "Number base conversion 2-36 (offline)", _BASECONV),
    "days_between": LibraryPlugin("days_between", "Days between two dates (offline)", _DAYSBETWEEN),
    "age_calculator": LibraryPlugin("age_calculator", "Age from a birthdate (offline)", _AGECALC),
    "percent_change": LibraryPlugin("percent_change", "Percentage change (offline)", _PCTCHANGE),
    "word_frequency": LibraryPlugin("word_frequency", "Most common words in text (offline)", _WORDFREQ),
    "compound_interest": LibraryPlugin("compound_interest", "Investment growth (offline)", _COMPOUND),
    "reddit_top": LibraryPlugin("reddit_top", "Top posts in a subreddit", _REDDIT),
    "crypto_market": LibraryPlugin("crypto_market", "Market cap & rank for a coin", _CRYPTOMKT),
    "prime_check": LibraryPlugin("prime_check", "Primality + prime factors (offline)", _PRIME),
    "fibonacci": LibraryPlugin("fibonacci", "Fibonacci sequence (offline)", _FIB),
    "caesar_cipher": LibraryPlugin("caesar_cipher", "ROT-N shift cipher (offline)", _CAESAR),
    "anagram_check": LibraryPlugin("anagram_check", "Are two words anagrams? (offline)", _ANAGRAM),
    "public_ip": LibraryPlugin("public_ip", "Your public IP address", _PUBLICIP),
    "wikipedia_random": LibraryPlugin("wikipedia_random", "A random Wikipedia article", _WIKIRAND),
    "luhn_check": LibraryPlugin("luhn_check", "Validate a card number (Luhn, offline)", _LUHN),
    "day_of_week": LibraryPlugin("day_of_week", "Weekday for a date (offline)", _DOW),
    "leap_year": LibraryPlugin("leap_year", "Is a year a leap year? (offline)", _LEAP),
    "scrabble_score": LibraryPlugin("scrabble_score", "Scrabble points for a word (offline)", _SCRABBLE),
    "hex_rgb": LibraryPlugin("hex_rgb", "Convert hex <-> RGB color (offline)", _HEXRGB),
    "gravatar": LibraryPlugin("gravatar", "Gravatar URL for an email (offline)", _GRAVATAR),
    "cat_fact": LibraryPlugin("cat_fact", "A random cat fact", _CATFACT),
    "nasa_apod": LibraryPlugin("nasa_apod", "NASA picture of the day", _NASA),
    "password_strength": LibraryPlugin("password_strength", "Rate a password (offline)", _PWSTRENGTH),
    "binary_text": LibraryPlugin("binary_text", "Text <-> binary (offline)", _BINTEXT),
    "color_contrast": LibraryPlugin("color_contrast", "WCAG contrast of two colors (offline)", _CONTRAST),
    "nato_alphabet": LibraryPlugin("nato_alphabet", "Spell text in NATO phonetics (offline)", _NATO),
    "factorial": LibraryPlugin("factorial", "n! factorial (offline)", _FACTORIAL),
    "ascii_code": LibraryPlugin("ascii_code", "Chars <-> code points (offline)", _ASCIICODE),
    "urban_dictionary": LibraryPlugin("urban_dictionary", "Slang definitions", _URBAN),
    "random_meal": LibraryPlugin("random_meal", "A random recipe idea", _RANDMEAL),
}

# Compact waves (name -> (blurb, source)) folded into the library.
for _wave in (_W1, _W2, _W3, _W4, _W5):
    for _n, (_b, _s) in _wave.items():
        LIBRARY[_n] = LibraryPlugin(_n, _b, _s)

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
    "distance": "geo_distance", "haversine": "geo_distance", "loan": "loan_payment",
    "mortgage": "loan_payment", "tip": "tip_split", "bill": "tip_split",
    "roll": "dice", "morse": "morse_code", "forecast": "weather_forecast",
    "geo": "geocode", "latlon": "geocode", "fact": "random_fact",
    "regex": "regex_test", "jwt": "jwt_decode", "roman": "roman_numeral",
    "base": "base_convert", "hex": "base_convert", "binary": "base_convert",
    "birthday": "age_calculator", "days": "days_between", "pct": "percent_change",
    "wordfreq": "word_frequency", "compound": "compound_interest",
    "reddit": "reddit_top", "marketcap": "crypto_market",
    "prime": "prime_check", "fib": "fibonacci", "caesar": "caesar_cipher",
    "rot13": "caesar_cipher", "anagram": "anagram_check", "myip": "public_ip",
    "randomwiki": "wikipedia_random", "luhn": "luhn_check", "card": "luhn_check",
    "weekday": "day_of_week", "leap": "leap_year", "scrabble": "scrabble_score",
    "rgb": "hex_rgb", "avatar": "gravatar", "cat": "cat_fact", "nasa": "nasa_apod",
    "apod": "nasa_apod", "pwstrength": "password_strength", "strength": "password_strength",
    "bintext": "binary_text", "contrast": "color_contrast", "nato": "nato_alphabet",
    "phonetic": "nato_alphabet", "fact_n": "factorial", "ascii": "ascii_code",
    "urban": "urban_dictionary", "slang": "urban_dictionary", "randommeal": "random_meal",
}


def resolve(name: str) -> LibraryPlugin | None:
    """Look up a library plugin by name or alias (case-insensitive). Pure."""
    key = (name or "").strip().lower()
    return LIBRARY.get(_ALIASES.get(key, key))


def library_rows() -> list[tuple[str, str, str]]:
    """(name, needs, blurb) for every library plugin, for display. Pure."""
    return [(p.name, p.needs, p.blurb) for p in LIBRARY.values()]


def installed_names(root: Path | str = ".") -> set[str]:
    """File stems of plugins installed under ``<root>/.ronin/plugins/``. Pure-ish."""
    pdir = Path(root) / ".ronin" / "plugins"
    if not pdir.is_dir():
        return set()
    return {p.stem for p in pdir.glob("*.py") if not p.name.startswith("_")}


def outdated(root: Path | str = ".") -> list[str]:
    """Installed *library* plugins whose on-disk source differs from the current
    library source (i.e. a newer/fixed version is available). User-written plugins
    (not in the library) are never reported."""
    pdir = Path(root) / ".ronin" / "plugins"
    stale: list[str] = []
    for name in sorted(installed_names(root)):
        entry = LIBRARY.get(name)
        if entry is None:
            continue
        try:
            current = (pdir / f"{name}.py").read_text(encoding="utf-8")
        except OSError:
            continue
        if current != entry.source:
            stale.append(name)
    return stale


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
