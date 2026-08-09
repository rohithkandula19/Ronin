"""Reads opsboard.ini into a settings object the renderer can use."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COLUMN_WIDTH = 14
DEFAULT_SEPARATOR = " | "


@dataclass(frozen=True)
class Settings:
    column_width: int
    separator: str
    show_rule: bool


def load_settings(path: str | Path) -> Settings:
    parser = ConfigParser()
    parser.read(path)
    if not parser.has_section("table"):
        return Settings(
            column_width=DEFAULT_COLUMN_WIDTH,
            separator=DEFAULT_SEPARATOR,
            show_rule=True,
        )
    section = parser["table"]
    return Settings(
        column_width=section.getint("column_width", DEFAULT_COLUMN_WIDTH),
        separator=section.get("separator", DEFAULT_SEPARATOR),
        show_rule=section.getboolean("show_rule", True),
    )
