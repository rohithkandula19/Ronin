"""The reusable choice picker — pure selection logic + off-TTY fallback."""
from __future__ import annotations


def test_parse_reply_single() -> None:
    from ronin_cli.picker import parse_reply
    assert parse_reply("2", 4, multi=False) == [1]
    assert parse_reply("2 3", 4, multi=False) == [1]   # single keeps first only
    assert parse_reply("9", 4, multi=False) == []      # out of range dropped
    assert parse_reply("", 4, multi=False) == []


def test_parse_reply_multi() -> None:
    from ronin_cli.picker import parse_reply
    assert parse_reply("1,3", 4, multi=True) == [0, 2]
    assert parse_reply("3 1 3", 4, multi=True) == [2, 0]   # dedup, order preserved
    assert parse_reply("1, 5, 2", 4, multi=True) == [0, 1]  # 5 clamped out


def test_normalize_accepts_strings_tuples_choices() -> None:
    from ronin_cli.picker import Choice, normalize
    out = normalize(["a", ("b", "desc"), Choice("c", value="C")])
    assert [o.label for o in out] == ["a", "b", "c"]
    assert out[1].description == "desc"
    assert out[2].val() == "C"
    assert out[0].val() == "a"   # value defaults to label


def test_ask_choice_fallback_off_tty(monkeypatch) -> None:
    # Off-TTY (Console.is_terminal False), ask_choice uses the numbered fallback.
    from rich.console import Console

    from ronin_cli import picker
    con = Console(force_terminal=False)
    monkeypatch.setattr(con, "input", lambda *a, **k: "2")
    got = picker.ask_choice("pick", [("a", ""), ("b", ""), ("c", "")], console=con)
    assert got == "b"

    monkeypatch.setattr(con, "input", lambda *a, **k: "1,3")
    got_multi = picker.ask_choice("pick", ["a", "b", "c"], console=con, multi=True)
    assert got_multi == ["a", "c"]
