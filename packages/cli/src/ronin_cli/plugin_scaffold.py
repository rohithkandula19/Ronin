"""Scaffold a working ronin plugin — `ronin plugin new <name>`.

Writes a ready-to-run plugin into ``.ronin/plugins/<name>.py`` with a real,
editable example tool (a live HTTP call) so it works the moment it's created and
shows the pattern to adapt. The name validation and template are pure and
unit-tested; the command just writes the file.
"""
from __future__ import annotations

import keyword
import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def valid_plugin_name(name: str) -> bool:
    """A plugin name must be a lowercase Python identifier (file + tool safe). Pure."""
    n = (name or "").strip()
    return bool(_NAME_RE.match(n)) and not keyword.iskeyword(n)


def plugin_template(name: str, description: str = "") -> str:
    """Source for a working starter plugin exposing one tool named ``name``. Pure."""
    desc = description.strip() or f"Example {name} tool — edit me."
    return f'''"""ronin plugin: {name}

{desc}

Drop this in .ronin/plugins/ and the agent gains a `{name}` tool it can call.
Edit `{name}` to call whatever API or logic you want; keep register_tools() at the
bottom returning your Tool(s).
"""
from __future__ import annotations

import httpx
from ronin_agent_patterns import Tool


PLUGIN = {{
    "name": "{name}",
    "version": "1",
    "description": "{desc}",
    "capabilities": ["network"],
}}


def {name}(query: str = "") -> dict:
    """{desc}

    This starter hits a public no-auth endpoint so it works immediately. Replace
    the body with your own API call, computation, or shell-out.
    """
    # Example: echo service that returns whatever you send (swap for your API).
    resp = httpx.get("https://httpbin.org/get", params={{"q": query}}, timeout=15)
    resp.raise_for_status()
    return {{"query": query, "echo": resp.json().get("args", {{}})}}


def register_tools():
    return [Tool(
        name="{name}",
        description="{desc}",
        input_schema={{"type": "object", "properties": {{
            "query": {{"type": "string", "description": "Your input to the tool."}}}}}},
        handler={name},
    )]
'''


def write_plugin(name: str, root: Path | str = ".", *, description: str = "",
                 overwrite: bool = False) -> Path:
    """Write a scaffolded plugin to ``<root>/.ronin/plugins/<name>.py``.

    Raises ValueError on a bad name, FileExistsError if it exists and not
    ``overwrite``."""
    if not valid_plugin_name(name):
        raise ValueError(f"invalid plugin name '{name}' — use lowercase letters, "
                         "digits and underscores, starting with a letter")
    pdir = Path(root) / ".ronin" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{name}.py"
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    path.write_text(plugin_template(name, description), encoding="utf-8")
    return path
