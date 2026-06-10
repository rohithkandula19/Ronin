"""Example user plugin.

Copy to ``.ronin/plugins/weather.py`` in your project to make ``csk`` agents call
this tool. Replace the fake implementation with a real weather API.
"""
from __future__ import annotations

from ronin_agent_patterns import Tool


_FAKE_WEATHER = {
    "san francisco": {"temp_f": 62, "condition": "foggy"},
    "new york": {"temp_f": 48, "condition": "cloudy"},
    "tokyo": {"temp_f": 71, "condition": "sunny"},
}


def get_weather(location: str) -> dict:
    """Toy weather backend. Replace with a real API call."""
    return _FAKE_WEATHER.get(location.lower(), {"temp_f": 70, "condition": "unknown"})


def register_tools() -> list[Tool]:
    """csk calls this function to discover the tools this plugin exposes."""
    return [
        Tool(
            name="weather",
            description="Get current weather for a city.",
            input_schema={
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name."}},
                "required": ["location"],
            },
            handler=get_weather,
        ),
    ]
