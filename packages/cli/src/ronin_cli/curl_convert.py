"""Convert a curl command into code — `ronin curl2code`.

Paste a ``curl`` command and get equivalent Python (httpx/requests) or JavaScript
(fetch) code. Parses method, headers, and body; the parsing and code generation
are pure and unit-tested.
"""
from __future__ import annotations

import json
import shlex


def parse_curl(command: str) -> dict:
    """Parse a curl command into {method, url, headers, data}. Pure."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]
    method, url, headers, data = "GET", "", {}, None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper(); i += 2
        elif t in ("-H", "--header") and i + 1 < len(tokens):
            k, _, v = tokens[i + 1].partition(":")
            headers[k.strip()] = v.strip(); i += 2
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode") and i + 1 < len(tokens):
            data = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
        elif t in ("-u", "--user") and i + 1 < len(tokens):
            headers["Authorization"] = f"Basic <base64 of {tokens[i + 1]}>"; i += 2
        elif t in ("-A", "--user-agent") and i + 1 < len(tokens):
            headers["User-Agent"] = tokens[i + 1]; i += 2
        elif t in ("-L", "--location", "-s", "--silent", "-k", "--insecure", "-i", "--include"):
            i += 1
        elif t.startswith("-"):
            i += 2 if (i + 1 < len(tokens) and not tokens[i + 1].startswith("http")) else 1
        else:
            url = t; i += 1
    return {"method": method, "url": url, "headers": headers, "data": data}


def _maybe_json(data):
    try:
        return json.loads(data), True
    except (json.JSONDecodeError, TypeError):
        return data, False


def to_python(parsed: dict) -> str:
    """Generate httpx Python code for a parsed curl. Pure."""
    m, url, headers, data = parsed["method"], parsed["url"], parsed["headers"], parsed["data"]
    lines = ["import httpx", ""]
    args = [f'"{url}"']
    if headers:
        lines.insert(1, f"headers = {json.dumps(headers, indent=4)}")
        args.append("headers=headers")
    if data is not None:
        obj, is_json = _maybe_json(data)
        if is_json:
            lines.insert(1, f"payload = {json.dumps(obj, indent=4)}")
            args.append("json=payload")
        else:
            args.append(f"content={data!r}")
    lines.append(f"r = httpx.{m.lower()}({', '.join(args)})")
    lines.append("print(r.status_code, r.json())")
    return "\n".join(lines)


def to_javascript(parsed: dict) -> str:
    """Generate fetch() JavaScript for a parsed curl. Pure."""
    m, url, headers, data = parsed["method"], parsed["url"], parsed["headers"], parsed["data"]
    opts: dict = {"method": m}
    if headers:
        opts["headers"] = headers
    if data is not None:
        obj, is_json = _maybe_json(data)
        opts["body"] = json.dumps(obj) if is_json else data
        if is_json:
            opts.setdefault("headers", {})["Content-Type"] = "application/json"
    body = json.dumps(opts, indent=2)
    return (f'const res = await fetch("{url}", {body});\n'
            "const data = await res.json();\nconsole.log(data);")
