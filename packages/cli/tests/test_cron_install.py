"""Tests for the cron scheduler's pure crontab-text logic."""
from __future__ import annotations

from ronin_cli.cron_install import (
    MARKER,
    cron_line,
    current_entry,
    remove_cron,
    upsert_cron,
)


def test_cron_line_has_marker() -> None:
    line = cron_line("0 2 * * *", "ronin nightshift --execute")
    assert line.startswith("0 2 * * *") and MARKER in line
    assert "ronin nightshift --execute" in line


def test_upsert_into_empty() -> None:
    out = upsert_cron("", "0 2 * * *", "ronin nightshift")
    assert MARKER in out and out.endswith("\n")
    assert len([ln for ln in out.splitlines() if MARKER in ln]) == 1


def test_upsert_preserves_other_entries() -> None:
    existing = "30 1 * * * backup.sh\n"
    out = upsert_cron(existing, "0 2 * * *", "ronin nightshift")
    assert "backup.sh" in out                       # other job kept
    assert MARKER in out


def test_upsert_replaces_existing_ronin_line() -> None:
    first = upsert_cron("", "0 2 * * *", "ronin nightshift")
    second = upsert_cron(first, "0 5 * * *", "ronin nightshift --issues")
    ronin_lines = [ln for ln in second.splitlines() if MARKER in ln]
    assert len(ronin_lines) == 1                    # not duplicated
    assert ronin_lines[0].startswith("0 5 * * *")   # updated schedule


def test_remove_cron() -> None:
    existing = upsert_cron("30 1 * * * backup.sh\n", "0 2 * * *", "ronin nightshift")
    text, removed = remove_cron(existing)
    assert removed and MARKER not in text
    assert "backup.sh" in text                      # unrelated job preserved
    # removing again → nothing to remove
    _, removed2 = remove_cron(text)
    assert removed2 is False


def test_current_entry() -> None:
    text = upsert_cron("", "0 2 * * *", "ronin nightshift")
    assert MARKER in (current_entry(text) or "")
    assert current_entry("30 1 * * * backup.sh") is None
