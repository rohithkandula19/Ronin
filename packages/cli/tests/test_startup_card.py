"""Premium startup card — real state, NO_COLOR + narrow fallbacks."""
from __future__ import annotations

import io
import re

from rich.console import Console

from ronin_cli.config import RoninConfig
from ronin_cli.ui_cards import render_startup_card, startup_lines

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(config, *, width=80, no_color=False, monkeypatch=None, root="."):
    if no_color and monkeypatch is not None:
        monkeypatch.setenv("NO_COLOR", "1")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=not no_color, width=width)
    render_startup_card(console, config, root, version="1.0.0-rc.1")
    return _ANSI.sub("", buf.getvalue())   # strip ANSI so content asserts are stable


def test_startup_lines_from_real_state(tmp_path) -> None:
    lines = startup_lines(RoninConfig(provider="cerebras"), tmp_path, version="1.0.0-rc.1")
    assert lines[0].startswith("🐼 ronin v1.0.0-rc.1")
    # free provider → FREE badge; real provider/model, no hardcoding
    assert lines[1].startswith("FREE · cerebras/")
    assert str(tmp_path.resolve()) in lines[2]
    assert "/help" in "\n".join(lines) and "/free on" in "\n".join(lines)


def test_paid_provider_shows_paid_badge(tmp_path) -> None:
    lines = startup_lines(RoninConfig(provider="anthropic"), tmp_path, version="1.0.0")
    assert lines[1].startswith("PAID · anthropic/")


def test_local_workspace_when_not_git(tmp_path) -> None:
    lines = startup_lines(RoninConfig(provider="groq"), tmp_path, version="1.0.0")
    assert "LOCAL WORKSPACE" in lines[1]  # tmp_path is not a git repo


def test_card_renders_and_shows_badge(tmp_path) -> None:
    out = _render(RoninConfig(provider="cerebras"), root=tmp_path)
    assert "ronin" in out and "FREE" in out and "cerebras" in out


def test_card_no_color_is_plain_text(tmp_path, monkeypatch) -> None:
    out = _render(RoninConfig(provider="groq"), no_color=True, monkeypatch=monkeypatch, root=tmp_path)
    assert "ronin" in out and "FREE" in out
    # no rich box-drawing under NO_COLOR
    assert "╭" not in out and "─" not in out


def test_card_narrow_sheds_hint_rows(tmp_path) -> None:
    wide = _render(RoninConfig(provider="cerebras"), width=80, root=tmp_path)
    narrow = _render(RoninConfig(provider="cerebras"), width=40, root=tmp_path)
    assert "/provider models" in wide          # hints present when wide
    assert "/provider models" not in narrow     # shed when narrow
    assert "FREE" in narrow                      # badge always stays


def test_card_never_hardcodes_provider(tmp_path) -> None:
    for prov in ("gemini", "groq", "anthropic", "ollama"):
        lines = startup_lines(RoninConfig(provider=prov), tmp_path, version="1.0")
        assert prov in lines[1]
