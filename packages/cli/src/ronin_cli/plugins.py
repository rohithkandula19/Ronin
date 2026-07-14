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


def _is_trusted(py_file: Path) -> bool:
    """Local import so the loader keeps working if the trust store is unreadable —
    but fail CLOSED: any error means 'not trusted', never 'trusted'."""
    try:
        from .plugin_trust import is_trusted
        return is_trusted(py_file)
    except Exception:  # noqa: BLE001 — an unusable trust store must not grant trust
        return False


@dataclass
class PluginLoadResult:
    name: str
    path: Path
    tools: list[Tool]
    error: str | None = None


def find_plugin_dir() -> Path:
    """Project-local plugin dir: ``.ronin/plugins/``."""
    return Path(".ronin") / PLUGIN_DIR_NAME


def load_plugins(
    plugin_dir: Path | None = None, *, require_trust: bool = True
) -> list[PluginLoadResult]:
    """Discover and load every plugin in ``plugin_dir``.

    Each ``.py`` file (not starting with ``_``) is imported; if it defines
    ``register_tools() -> list[Tool]`` that function is called. Errors in one
    plugin do not abort loading the others — they are captured on the result.

    **Untrusted files are never imported.** ``.ronin/plugins/`` lives inside the
    repository, and importing a file executes it — before any tool call, so the
    approval gate and destructive floor never see it. A cloned repo carrying a
    plugin would otherwise be remote code execution. Only files the user has
    explicitly trusted (``ronin plugin trust``, or installed themselves via
    ``plugin new`` / ``add`` / ``from-api``) are imported; see :mod:`plugin_trust`.

    ``require_trust=False`` skips that check and is for the loader's own unit
    tests only — never pass it on a path reachable from a repository.
    """
    plugin_dir = plugin_dir or find_plugin_dir()
    results: list[PluginLoadResult] = []
    if not plugin_dir.exists():
        return results

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        if require_trust and not _is_trusted(py_file):
            # Do NOT import it. Reporting the file is safe; executing it is not.
            results.append(PluginLoadResult(
                py_file.stem, py_file, [],
                "untrusted — not loaded. Review the file, then: "
                f"ronin plugin trust {py_file.name}",
            ))
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


def load_plugin_tools(
    plugin_dir: Path | None = None, *, require_trust: bool = True
) -> list[Tool]:
    """Convenience: just the tools from successful loads. Untrusted files are not
    imported (see :func:`load_plugins`); ``require_trust=False`` is for the
    loader's own unit tests only."""
    tools: list[Tool] = []
    seen: set[str] = set()
    for result in load_plugins(plugin_dir, require_trust=require_trust):
        for tool in result.tools:
            if tool.name in seen:
                continue
            seen.add(tool.name)
            tools.append(tool)
    return tools


def harden(tool: Tool) -> Tool:
    """Wrap a tool's handler so any exception becomes a structured error instead of
    crashing the agent loop. A transient API hiccup (non-JSON, timeout, KeyError)
    in a plugin then surfaces as ``{"error": ...}`` the model can read and recover
    from. Returns a new Tool (handler swapped); pure given the tool."""
    inner = tool.handler

    def safe_handler(*args, **kwargs):
        try:
            return inner(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a tool must never take down the agent
            return {"error": f"{tool.name} failed: {type(exc).__name__}: {exc}"}

    # Plugin tools run arbitrary user/library code with full privileges, so mark
    # them gated by default — the approval gate prompts once and 'always' remembers.
    return tool.model_copy(update={"handler": safe_handler, "sensitive": True})


def build_plugin_tools(root: str | Path = ".", *, console=None) -> list[Tool]:
    """Load user plugin tools from ``<root>/.ronin/plugins/`` for the agent.

    Root-aware sibling of ``load_plugin_tools`` so an in-session agent picks up the
    project's plugins. Per-plugin errors are surfaced (if ``console``) and skipped,
    never fatal — exactly like the MCP loader. Each tool's handler is hardened so a
    runtime failure returns a clean error rather than crashing the session."""
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
                tools.append(harden(tool))
    return tools
