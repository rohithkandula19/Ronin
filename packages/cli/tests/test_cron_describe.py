"""Tests for cron expression descriptions."""
from __future__ import annotations

import pytest

from ronin_cli.cron_describe import describe_cron


@pytest.mark.parametrize("expr,contains", [
    ("0 9 * * *", "At 09:00"),
    ("*/15 * * * *", "Every 15 minutes"),
    ("0 9 * * 1-5", "Monday through Friday"),
    ("0 9,17 * * *", "09:00 and 17:00"),
    ("*/15 9-17 * * 1-5", "between 09:00 and 17:59"),
    ("0 0 1 1 *", "in January"),
    ("@daily", "At 00:00"),
    ("@hourly", "minute 0"),
])
def test_describe_cron(expr, contains) -> None:
    assert contains in describe_cron(expr)


def test_invalid_cron() -> None:
    assert "invalid" in describe_cron("nope").lower()
    assert "invalid" in describe_cron("1 2 3").lower()
