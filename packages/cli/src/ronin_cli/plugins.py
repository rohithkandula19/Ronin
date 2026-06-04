"""Plugin loader — drop a Python file in ``.ronin/plugins/`` and its tools auto-load.

Each plugin file must define a top-level ``register_tools()`` function returning a
``list[Tool]``. Plugins are discovered alphabetically; file names starting with
``_`` are skipped.

Example plugin (``.ronin/plugins/weather.py``)::

    from ronin_agent_patterns import Tool

    def my_weather_handler(location: str) -> dict:
        return {"location": location, "temp_f": 72}

    def register_tools() -> list[Tool]:
        return [Tool(
            name="weather",
            description="Get current weather for a location.",
            input_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
            handler=my_weather_handler,
        )]
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from ronin_agent_patterns import Tool


PLUGIN_DIR_NAME = "plugins"


@dataclass
class PluginLoadResult:
    name: str
    path: Path
    tools: list[Tool]
    error: str | None = None


def find_plugin_dir() -> Path:
    """Project-local plugin dir: ``.ronin/plugins/``."""
    return Path(".ronin") / PLUGIN_DIR_NAME


def load_plugins(plugin_dir: Path | None = None) -> list[PluginLoadResult]:
    """Discover and load every plugin in ``plugin_dir``.

    Each ``.py`` file (not starting with ``_``) is imported; if it defines
    ``register_tools() -> list[Tool]`` that function is called. Errors in one
    plugin do not abort loading the others — they are captured on the result.
    """
    plugin_dir = plugin_dir or find_plugin_dir()
    results: list[PluginLoadResult] = []
    if not plugin_dir.exists():
        return results

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"_csk_plugin_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                results.append(PluginLoadResult(py_file.stem, py_file, [], "could not build import spec"))
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, "register_tools"):
                results.append(PluginLoadResult(py_file.stem, py_file, [], "no register_tools() function"))
                continue
            raw = module.register_tools()
            if not isinstance(raw, list):
                results.append(PluginLoadResult(py_file.stem, py_file, [], "register_tools() must return a list"))
                continue
            tools: list[Tool] = []
            for item in raw:
                if isinstance(item, Tool):
                    tools.append(item)
                else:
                    results.append(PluginLoadResult(
                        py_file.stem, py_file, [],
                        f"register_tools() returned a non-Tool item: {type(item).__name__}",
                    ))
                    tools = []
                    break
            if tools:
                results.append(PluginLoadResult(py_file.stem, py_file, tools))
        except Exception as exc:  # noqa: BLE001
            results.append(PluginLoadResult(py_file.stem, py_file, [], f"{type(exc).__name__}: {exc}"))

    return results


def load_plugin_tools(plugin_dir: Path | None = None) -> list[Tool]:
    """Convenience: just the tools from successful loads."""
    tools: list[Tool] = []
    seen: set[str] = set()
    for result in load_plugins(plugin_dir):
        for tool in result.tools:
            if tool.name in seen:
                continue
            seen.add(tool.name)
            tools.append(tool)
    return tools


def build_plugin_tools(root: str | Path = ".", *, console=None) -> list[Tool]:
    """Load user plugin tools from ``<root>/.ronin/plugins/`` for the agent.

    Root-aware sibling of ``load_plugin_tools`` so an in-session agent picks up the
    project's plugins. Per-plugin errors are surfaced (if ``console``) and skipped,
    never fatal — exactly like the MCP loader."""
    plugin_dir = Path(root) / ".ronin" / PLUGIN_DIR_NAME
    tools: list[Tool] = []
    seen: set[str] = set()
    for result in load_plugins(plugin_dir):
        if result.error:
            if console:
                console.print(f"[yellow]⚠ plugin '{result.name}': {result.error}[/yellow]")
            continue
        for tool in result.tools:
            if tool.name not in seen:
                seen.add(tool.name)
                tools.append(tool)
    return tools
